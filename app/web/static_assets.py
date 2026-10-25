"""Keep public asset URLs stable while application assets live together."""
from app.paths import STATIC

PUBLIC_ASSETS = frozenset(['workspace.css', 'workspace.js', 'action_feedback.mjs', 'capture_preview.mjs', 'capture_wizard.mjs', 'capture_wizard.css', 'collections.mjs', 'crop_state.mjs', 'editor.css', 'editor_ui.mjs', 'glb_export.mjs', 'job_logs.mjs', 'model_crop.css', 'model_crop.mjs', 'model_gamepad.mjs', 'model_minimap.mjs', 'model_navigation.mjs', 'model_viewer.mjs', 'point_painter.mjs', 'pose_viewer.mjs', 'preparation.mjs', 'quality_viewer.mjs', 'runtime_verify.mjs', 'scene_geometry.mjs', 'scene_alignment.mjs', 'scene_player.mjs', 'scene_auto_proxy.mjs', 'scene_editor.mjs', 'scene_ui_controls.mjs', 'scene_editor.css', 'scene_collision.mjs', 'scene_library.mjs', 'temporary_poses.mjs', 'tutorial.mjs', 'ui_controls.mjs', 'video_preview.mjs', 'view_settings.mjs'])


def static_asset(path):
    prefix = '/assets/'
    name = path[len(prefix):] if path.startswith(prefix) else ''
    return STATIC / name if name in PUBLIC_ASSETS else None
