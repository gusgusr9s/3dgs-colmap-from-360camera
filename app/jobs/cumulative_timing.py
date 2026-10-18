"""Display active processing time across a model's actual dependency chain."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path


def seconds(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else 0
    except (TypeError, ValueError):
        return 0


def read(path):
    try:
        value = json.loads(path.read_text(encoding='utf8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def valid_id(value):
    return isinstance(value, str) and bool(value) and value not in {'.', '..'} and '/' not in value and '\\' not in value


def elapsed(record, now):
    value = seconds(record.get('elapsed_seconds'))
    if record.get('state') == 'running' and not record.get('finished_at'):
        try:
            start = datetime.fromisoformat(record['started_at'].replace('Z', '+00:00'))
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            value = max(value, (now-start).total_seconds())
        except (KeyError, ValueError, TypeError):
            pass
    return value


def cumulative_timing(status, job_directory, now=None):
    """Keep per-run elapsed unchanged for ETA; add separate display totals.

    Follow explicit resume/refinement references, never sum unrelated attempts.
    Include the matching Views version once. Paused wall time is excluded.
    """
    status = dict(status or {})
    if not status.get('run_id') or status.get('stage') not in {'model', 'poses'}:
        return status
    root, now = Path(job_directory).resolve(), now or datetime.now(timezone.utc)
    seen, records, missing = set(), [], []

    def visit(record):
        identifier = record.get('run_id')
        if not valid_id(identifier) or identifier in seen:
            return
        seen.add(identifier)
        records.append(record)
        recovery = record.get('colmap_recovery') or {}
        snapshot = recovery.get('previous_status') or {}
        parent = ((record.get('training_recovery') or {}).get('source_run')
                  or (record.get('continuation') or {}).get('source_run')
                  or snapshot.get('run_id') or record.get('parent_run_id') or record.get('pose_source_run'))
        if not parent or parent in seen:
            return
        if not valid_id(parent):
            missing.append(str(parent))
            return
        group = 'pose_runs' if parent == record.get('pose_source_run') else 'model_runs'
        path = root/group/parent/'status.json'
        previous = read(path) if path.resolve().is_relative_to(root) else {}
        if previous.get('run_id') != parent:
            previous = snapshot if snapshot.get('run_id') == parent else {}
        if previous:
            visit(previous)
        else:
            missing.append(parent)

    visit(status)
    view_id = status.get('view_run_id')
    if valid_id(view_id):
        view_path = root/'view_runs'/view_id/'status.json'
        view = read(view_path) if view_path.resolve().is_relative_to(root) else {}
        if view.get('run_id') != view_id:
            view = read(root/'views_status.json')
        if view.get('run_id') == view_id:
            records.append({**view, 'stage':'views'})
        else:
            missing.append('Views '+view_id)
    phases = {}
    for record in records:
        values = dict(record.get('phase_durations_seconds') or {})
        if record.get('stage') == 'views':
            values = {'views':elapsed(record, now)}
        elif record is records[0] and record.get('state') == 'running' and record.get('phase'):
            # PhaseTimer snapshots are persisted only on progress updates.
            accounted = sum(seconds(value) for value in values.values())
            key = record['phase']
            values[key] = seconds(values.get(key))+max(0, elapsed(record, now)-accounted)
        for key, value in values.items():
            phases[key] = phases.get(key, 0)+seconds(value)
    current = elapsed(status, now)
    total = sum(elapsed(record, now) for record in records)
    return {**status, 'total_elapsed_seconds':round(total, 3),
            'elapsed_before_current_seconds':round(max(0, total-current), 3),
            'total_phase_durations_seconds':{key:round(value, 3) for key,value in phases.items()},
            'timing_run_count':len([record for record in records if record.get('stage') != 'views']),
            'timing_includes_views':any(record.get('stage') == 'views' for record in records),
            'timing_missing_runs':missing, 'timing_scope':'processing_time_excluding_pauses'}
