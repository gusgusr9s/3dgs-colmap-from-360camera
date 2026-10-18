"""Incremental spherical mapping in growing segments with an external global BA.

COLMAP's mapper picks its global-BA solver from fixed frame thresholds (sparse
cuDSS above 200 frames on CUDA, CHOLMOD up to 1000 on CPU) and repeats
retriangulation + BA up to five times per global solve.  Here the mapper grows
the model by ~20% per segment (the balanced profile's global-BA ratio) with its
intermediate global BAs disabled; its final refinement keeps retriangulation,
track merging and outlier filtering with one solver step.  bundle_adjuster then
converges the segment with the fastest measured solver (dense CUDA / CPU
iterative Schur), and the next segment continues from that model.
"""
from contextlib import closing
import math
import os
from pathlib import Path
import shutil
import sqlite3

from app.pipeline.colmap_recovery import MODEL_FILES, model_count

# The mapper itself already uses dense CUDA (or small CPU solves) up to 200 frames.
START_FRAMES = 200
GROWTH = 1.2
MIN_GROWTH = 40
CHECKPOINT_PREFIX = 'staged-'
NO_INTERMEDIATE_GLOBAL_BA = ['--Mapper.ba_global_frames_ratio', '1000000', '--Mapper.ba_global_points_ratio', '1000000',
                             '--Mapper.ba_global_frames_freq', '1000000000', '--Mapper.ba_global_points_freq', '1000000000']
ONE_STEP_FINAL_REFINEMENT = ['--Mapper.ba_global_max_refinements', '1', '--Mapper.ba_global_max_num_iterations', '1']


def enabled(panorama_count, profile, environment=None):
    mode = (environment or os.environ).get('COLMAP_STAGED_MAPPING', 'auto').strip().lower()
    if mode not in {'auto', 'on', 'off'}:
        raise ValueError('COLMAP_STAGED_MAPPING must be auto, on or off')
    if mode != 'auto':
        return mode == 'on'
    # The full profile is the reference path; keep COLMAP's own refinement there.
    return profile == 'balanced' and panorama_count > START_FRAMES*GROWTH


def database_names(database):
    with closing(sqlite3.connect(Path(database).as_uri()+'?mode=ro', uri=True, timeout=5)) as connection:
        return [name for (name,) in connection.execute('SELECT name FROM images ORDER BY name')]


def registered_names(model):
    import pycolmap
    reconstruction = pycolmap.Reconstruction(model)
    return {reconstruction.image(image_id).name for image_id in reconstruction.reg_image_ids()}


def plan_chunk(names, registered, attempted, start_frames=START_FRAMES):
    """Next image list: earlier failures plus a time-ordered block of new frames."""
    fresh = [name for name in names if name not in registered and name not in attempted]
    retry = sorted(attempted-registered)
    if len(registered) < start_frames:
        take = start_frames-len(registered)
    else:
        take = max(MIN_GROWTH, math.ceil(len(registered)*(GROWTH-1)))
    chunk = retry+fresh[:max(0, take-len(retry))] if len(retry) < take else retry+fresh[:MIN_GROWTH]
    return sorted(registered | set(chunk)), chunk, bool(fresh)


def largest_model(directory):
    found = [(model_count(path), path) for path in [directory, *directory.glob('*')] if path.is_dir()]
    found = [item for item in found if item[0] > 0]
    return max(found, key=lambda item: item[0])[1] if found else None


def replace_model(source, destination):
    destination.mkdir(parents=True, exist_ok=True)
    for name in MODEL_FILES:
        shutil.copy2(source/name, destination/name)


def remove_checkpoint(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    if path.parent == root and path.name.startswith(CHECKPOINT_PREFIX) and path.is_dir():
        shutil.rmtree(path)


def run(colmap, *, database, image_dir, output_root, checkpoints, input_model, bootstrap_options,
        segment_options, choose_solver, global_tolerance, run_command, report, log):
    """Return the final model directory written under output_root (or None if nothing registered)."""
    checkpoints, output_root = Path(checkpoints).resolve(), Path(output_root).resolve()
    checkpoints.mkdir(parents=True, exist_ok=True)
    current = Path(input_model).resolve() if input_model else None
    for stale in checkpoints.glob(CHECKPOINT_PREFIX+'*'):
        # A retry (e.g. after expanded matching) must not resume from an older attempt.
        if stale != current and stale.is_dir():
            remove_checkpoint(stale, checkpoints)
    names = database_names(database)
    registered = registered_names(current) if current else set()
    attempted, segment, staged_any, owned = set(), 0, False, None

    def note(message):
        log.write('Staged mapping: '+message+'\n')
        log.flush()

    def adjust(model, frames, label):
        solver, flags, reason = choose_solver(frames)
        target = checkpoints/f'{CHECKPOINT_PREFIX}{segment:03d}-{label}'
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir()
        note(f'global BA {frames} frames · {solver} ({reason})')
        report(len(registered), len(names), 'global_bundle_adjustment', solver)
        run_command([str(colmap), 'bundle_adjuster', '--input_path', str(model), '--output_path', str(target),
                     '--BundleAdjustment.backend', 'CERES', *flags,
                     '--BundleAdjustmentCeres.max_num_iterations', '50',
                     '--BundleAdjustmentCeres.function_tolerance', global_tolerance, '--log_target', 'stdout'])
        if model_count(target) < frames:
            raise RuntimeError(f'Staged BA lost frames: {model_count(target)}/{frames}; see 3d.log')
        return target

    while True:
        images, chunk, fresh_left = plan_chunk(names, registered, attempted)
        if not chunk:
            break
        staged = len(registered) >= START_FRAMES
        segment += 1
        listing = checkpoints/f'{CHECKPOINT_PREFIX}{segment:03d}.txt'
        listing.write_text('\n'.join(images)+'\n', encoding='utf8')
        output = checkpoints/f'{CHECKPOINT_PREFIX}{segment:03d}-mapper'
        shutil.rmtree(output, ignore_errors=True)
        output.mkdir()
        note(f'segment {segment}: {len(registered)} registered + {len(chunk)} candidate frames'
             +(' (continuing; one-step final refinement)' if staged else ' (bootstrap; COLMAP global BA)'))
        report(len(registered), len(names), 'registering', None, segment_start=len(registered))
        options = segment_options if staged else bootstrap_options
        command = [str(colmap), 'mapper', '--database_path', str(database), '--image_path', str(image_dir),
                   '--output_path', str(output), '--Mapper.image_list_path', str(listing), *options,
                   *(['--input_path', str(current), '--Mapper.multiple_models', '0'] if current else [])]
        try:
            run_command(command)
        finally:
            listing.unlink(missing_ok=True)
        model = largest_model(output)
        progressed = bool(model) and model_count(model) > len(registered)
        attempted |= set(chunk)
        if not progressed:
            remove_checkpoint(output, checkpoints)
            note(f'segment {segment} registered no new frames')
            if not fresh_left:
                break
            if current is None:
                # No initial pair in the first block: one plain attempt over every frame.
                attempted |= set(names)
            continue
        checkpoint = output
        if staged:
            model = checkpoint = adjust(model, model_count(model), 'ba')
            remove_checkpoint(output, checkpoints)
            staged_any = True
        if owned and owned != checkpoint:
            remove_checkpoint(owned, checkpoints)
        current, owned = model, checkpoint
        registered = registered_names(current)
    if current is None:
        return None
    if staged_any:
        # Mirror the mapper's closing refine: filter outliers after the converged solve, then re-solve.
        filtered = checkpoints/f'{CHECKPOINT_PREFIX}{segment:03d}-filtered'
        shutil.rmtree(filtered, ignore_errors=True)
        filtered.mkdir()
        note('final outlier filtering (max reprojection 4 px, min triangulation angle 1.5 deg)')
        run_command([str(colmap), 'point_filtering', '--input_path', str(current), '--output_path', str(filtered),
                     '--min_track_len', '2', '--max_reproj_error', '4', '--min_tri_angle', '1.5', '--log_target', 'stdout'])
        segment += 1
        final = adjust(filtered, model_count(filtered), 'final')
        remove_checkpoint(filtered, checkpoints)
        if owned and owned != final:
            remove_checkpoint(owned, checkpoints)
        current = owned = final
    destination = output_root/'staged'
    replace_model(current, destination)
    if owned:
        remove_checkpoint(current, checkpoints)
    note(f'complete: {model_count(destination)}/{len(names)} frames → {destination}')
    return destination
