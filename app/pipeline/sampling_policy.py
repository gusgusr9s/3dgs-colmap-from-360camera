"""Shared sampling target for Source defaults and camera-map advice."""
import math


TARGET_POSITIONS_PER_SECOND = 3


def recommended_sample_every(fps):
    """Scale 10 frames at 30 FPS to the nearest whole source-frame interval."""
    try:
        fps = float(fps)
    except (TypeError, ValueError):
        return 1
    if not math.isfinite(fps) or fps <= 0:
        return 1
    return max(1, min(10000, math.floor(fps / TARGET_POSITIONS_PER_SECOND + .5)))
