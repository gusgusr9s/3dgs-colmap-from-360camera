"""Approximate finish times from completed work, recent training logs and history."""
from datetime import datetime, timezone, timedelta
import math
import re
from statistics import median


def timestamp(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result
    except (ValueError, TypeError):
        return None


def phase(status):
    if status.get("stage") == "views":
        return "views"
    progress = int(status.get("progress", 0))
    step = status.get("step", "").lower()
    if progress >= 95:
        return "finalize"
    if progress >= 40:
        return "training"
    if progress == 39 and "mask" in step:
        return "masks"
    if progress >= 36:
        return "dataset"
    if progress >= 28:
        return "poses"
    if progress >= 18:
        return "matching"
    if progress >= 8:
        return "features"
    return "preparation"


class PhaseTimer:
    def __init__(self):
        self.current = None
        self.started = None
        self.durations = {}

    def update(self, status, now=None):
        now = now or timestamp(status.get("finished_at")) or datetime.now(timezone.utc)
        current = phase(status)
        if current != self.current:
            if self.started:
                self.durations[self.current] = self.durations.get(self.current, 0) + (now - self.started).total_seconds()
            self.current, self.started = current, now
        durations = dict(self.durations)
        durations[current] = durations.get(current, 0) + (now - self.started).total_seconds()
        return {**status, "phase": current, "phase_started_at": self.started.isoformat(),
                "phase_durations_seconds": {key: round(value, 3) for key, value in durations.items()}}


def training_samples(log):
    samples = []
    pattern = r"\[([^\]]+)\s+INFO[^\]]*\].*?Refine iter (\d+),"
    for match in re.finditer(pattern, log):
        at = timestamp(match.group(1).split()[0])
        iteration = int(match.group(2))
        if at is None:
            continue
        if samples and iteration <= samples[-1][1]:
            samples = []  # A codec recovery/retry started a fresh training run.
        samples.append((at, iteration))
    return samples[-20:]


def compatible_history(status, records):
    candidates = []
    for record in records:
        if record.get("state") != "done" or (status.get("run_id") and record.get("run_id") == status["run_id"]):
            continue
        if bool(record.get("refined_from")) != bool(status.get("refined_from")):
            continue
        current_quality, old_quality = status.get('quality', {}), record.get('quality', {})
        current_compute = current_quality.get('mask_compute') or status.get('view_compute')
        old_compute = old_quality.get('mask_compute') or record.get('view_compute')
        if current_compute and (not old_compute or current_compute.get('backend') != old_compute.get('backend') or current_compute.get('device') != old_compute.get('device')):
            continue
        settings, previous = status.get("settings", {}), record.get("settings", {})
        if any(settings.get(key) != previous.get(key) for key in ("quality", "width", "height", "projection", "sample_every", "start_seconds", "end_seconds")):
            continue
        source, old_source = status.get("source_profile", {}), record.get("source_profile", {})
        if any(source.get(key) != old_source.get(key) for key in ("width", "height", "kind")):
            continue
        count, old_count = source.get("frame_count"), old_source.get("frame_count")
        if count and old_count and not .8 <= count / old_count <= 1.25:
            continue
        if status.get("training_steps") and status["training_steps"] != record.get("quality", {}).get("training_steps"):
            continue
        if status.get("training_views") and record.get("quality", {}).get("selected_views"):
            if not .8 <= status["training_views"] / record["quality"]["selected_views"] <= 1.25:
                continue
        if record.get("elapsed_seconds", 0) > 0:
            candidates.append(record)
    return candidates


def estimate_status(status, *, now=None, log="", history=()):
    result = {key: value for key, value in status.items() if not key.startswith("eta_") and key not in {"estimated_finish_at", "estimate_state"}}
    if status.get("state") != "running":
        return result
    now = now or datetime.now(timezone.utc)
    start = timestamp(status.get("started_at"))
    elapsed = max(0, (now - start).total_seconds()) if start else float(status.get("elapsed_seconds", 0))
    result.update(elapsed_seconds=round(elapsed, 3), estimate_state="warming_up")

    def available(seconds, source, scope="task"):
        if not math.isfinite(seconds) or seconds < 0:
            return result
        result.update(estimate_state="available", eta_seconds=round(seconds), eta_source=source,
                      eta_scope=scope, estimated_finish_at=(now + timedelta(seconds=seconds)).isoformat())
        return result

    if status.get("stage") == "views":
        plan = status.get("capture_plan", {})
        total = plan.get("total_views")
        done = status.get("frames", 0)
        if total and done >= total:
            result["estimate_state"] = "finishing"
        elif total:
            changed = timestamp(status.get('compute_changed_at'))
            phase_elapsed = max(0, (now - changed).total_seconds()) if changed else elapsed
            phase_done = done - status.get('compute_start_frames', 0)
            if phase_done >= max(2, 2 * plan.get("views_per_frame", 1)) and phase_elapsed >= 3:
                available(phase_elapsed / phase_done * (total - done), "completed_views")
        return result

    current_phase = status.get("phase") or phase(status)
    if current_phase == "finalize":
        result["estimate_state"] = "finishing"
        return result
    samples = training_samples(log) if current_phase == "training" else []
    total = status.get("training_steps", 0)
    if len(samples) >= 2 and total:
        first, last = samples[0], samples[-1]
        span, work = (last[0] - first[0]).total_seconds(), last[1] - first[1]
        if span > 0 and work > 0:
            intervals = [(right[0] - left[0]).total_seconds() for left, right in zip(samples, samples[1:])]
            age = max(0, (now - last[0]).total_seconds())
            if age > max(60, median(intervals) * 5):
                result["estimate_state"] = "stale"
                return result
            if last[1] >= total:
                result["estimate_state"] = "finishing"
                return result
            records = compatible_history(status, history)
            overheads = [record.get("phase_durations_seconds", {}).get("finalize") or record.get("finalization_seconds") for record in records]
            overheads = [value for value in overheads if value is not None and value >= 0]
            overhead = median(overheads) if overheads else 60
            remaining = max(0, (total - last[1]) * span / work - age)
            available(remaining + overhead, "recent_training_rate")
            result.update(eta_training_seconds=round(remaining), eta_finalization_seconds=round(overhead),
                          eta_finalization_source="history" if overheads else "initial_allowance",
                          eta_sample_count=len(samples))
            return result
    records = compatible_history(status, history)
    if records:
        remaining = median(record["elapsed_seconds"] for record in records) - elapsed
        if remaining > 0:
            return available(remaining, "similar_completed_models")
    return result
