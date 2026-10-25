"""Serve a small, isolated Gaussian scene for real-browser editor checks.

Run with the project's Python environment. All generated files live under the
ignored tools/browser-qa directory; the normal model library is never opened.
The fixture persists edits between runs and does not delete any files.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import struct
import sys


ROOT = Path(__file__).resolve().parents[1]
QA_ROOT = ROOT / "tools" / "browser-qa"
DEFAULT_WORKSPACE = QA_ROOT / "scene-test-workspace"
MARKER = ".scene-test-fixture.json"
FIXTURE_VERSION = 1
SH_C0 = 0.28209479177387814
PROPERTIES = (
    "x", "y", "z", "nx", "ny", "nz", "f_dc_0", "f_dc_1", "f_dc_2",
    "opacity", "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3",
)


def fixture_workspace(value: Path) -> Path:
    """Only allow a dedicated child directory inside the QA output directory."""
    workspace = value.expanduser().resolve()
    qa_root = QA_ROOT.resolve()
    if workspace == qa_root or not workspace.is_relative_to(qa_root):
        raise ValueError(f"Fixture workspace must be a child of {qa_root}")
    marker_path = workspace / MARKER
    marker = {}
    if marker_path.exists():
        marker = json.loads(marker_path.read_text(encoding="utf8"))
        if marker.get("fixture_version") != FIXTURE_VERSION:
            raise ValueError("Workspace has an unsupported scene fixture marker")
    elif workspace.exists() and any(workspace.iterdir()):
        raise ValueError("Refusing to use a nonempty directory without a scene fixture marker")
    workspace.mkdir(parents=True, exist_ok=True)
    if not marker:
        marker_path.write_text(json.dumps({"fixture_version": FIXTURE_VERSION}), encoding="utf8")
    return workspace


def room_vertices(warm: bool = False):
    """Deterministic, degree-zero Gaussian splats on a floor and three walls."""
    samples = []
    for i in range(61):
        x = -2 + 4 * i / 60
        for j in range(61):
            z = -2 + 4 * j / 60
            checker = (int((x + 2) * 2) + int((z + 2) * 2)) % 2
            color = ((.78, .48, .22) if warm else (.23, .55, .67)) if checker else (.58, .65, .69)
            samples.append((x, 0., z, color))
        for j in range(43):
            y = 3 * j / 42
            stripe = .08 * (int(y * 3) % 2)
            color = (.8 - stripe, .6 - stripe, .35) if warm else (.3, .65 - stripe, .78 - stripe)
            samples.append((x, y, -2., color))
            # An open front and a door on the shared side make the composition
            # easy to see and allow a walking check between adjacent rooms.
            z = x
            samples.append((-2., y, z, (.58, .65, .7)))
            if not (-.7 < z < .7 and y < 2.2):
                samples.append((2., y, z, color))
    scale = math.log(.043)
    opacity = math.log(.97 / .03)
    for x, y, z, color in samples:
        yield (x, y, z, 0., 1., 0., *((channel - .5) / SH_C0 for channel in color),
               opacity, scale, scale, scale, 1., 0., 0., 0.)


def write_room(path: Path, warm: bool = False) -> int:
    vertices = list(room_vertices(warm))
    header = ["ply", "format binary_little_endian 1.0", "comment 360 Studio isolated browser QA fixture",
              f"element vertex {len(vertices)}"]
    header.extend(f"property float {name}" for name in PROPERTIES)
    header.append("end_header")
    with path.open("wb") as stream:
        stream.write(("\n".join(header) + "\n").encode("ascii"))
        pack = struct.Struct("<" + "f" * len(PROPERTIES)).pack
        for vertex in vertices:
            stream.write(pack(*vertex))
    return len(vertices)


def prepare_fixture(workspace: Path, port: int = 8766):
    workspace = fixture_workspace(workspace)
    if "app.paths" in sys.modules:
        raise RuntimeError("Run fixture setup before importing app.paths")
    os.environ["STUDIO_WORKSPACE"] = str(workspace)
    sys.path.insert(0, str(ROOT))
    # Imports happen after workspace selection because app paths are initialized
    # on import. This is the actual web Handler and scene validation code.
    from app.web import server as app
    from app.models import scene_projects

    counts = {}
    for filename, warm in [("qa-room-a.ply", False), ("qa-room-b.ply", True)]:
        source = app.MODELS / filename
        counts[filename] = len(list(room_vertices(warm)))
        if not source.exists():
            write_room(source, warm)
        metadata = source.with_suffix(".meta.json")
        if not metadata.exists():
            metadata.write_text(json.dumps({
                "model": filename, "world_up": [0, 1, 0], "viewer_config": {},
                "source_name": "Synthetic Gaussian browser QA fixture",
                "quality": {"published_splats": counts[filename]},
            }, indent=2), encoding="utf8")

    marker_path = workspace / MARKER
    marker = json.loads(marker_path.read_text(encoding="utf8"))
    scene_id = marker.get("scene_id")
    if not scene_id or not (scene_projects.SCENES / f"{scene_id}.json").is_file():
        document = scene_projects.create_scene({
            "name": "Chromium QA - two adjoining Gaussian rooms", "units": "meters",
            "objects": [
                {"id": "qa-room-a", "name": "Room A (blue)", "asset": {"kind": "ply", "ref": "qa-room-a.ply"}},
                {"id": "qa-room-b", "name": "Room B (warm)", "asset": {"kind": "ply", "ref": "qa-room-b.ply"}, "position": [4.2, 0, 0]},
            ],
            "colliders": [
                {"id": "qa-floor", "name": "QA floor", "kind": "floor", "position": [2.1, -.1, 0], "size": [8.4, .2, 4.2]},
                {"id": "qa-wall", "name": "QA back wall", "kind": "wall", "position": [2.1, 1.5, -2.05], "size": [8.4, 3, .1]},
            ],
            "view": {"position": [8.5, 6, 12], "target": [2.1, 1, 0]},
            "spawn": {"position": [0, 1.65, 1], "yaw": 0},
        }, lambda kind, ref: app.MODELS / ref if kind == "ply" and (app.MODELS / ref).is_file() else None)
        scene_id = document["id"]
        marker["scene_id"] = scene_id
        marker_path.write_text(json.dumps(marker, indent=2), encoding="utf8")
    info = {
        "url": f"http://127.0.0.1:{port}/scene/{scene_id}", "scene_id": scene_id, "sceneId": scene_id,
        "workspace": str(workspace), "models": counts,
        "manifest": str(workspace / "fixture.json"),
    }
    (workspace / "fixture.json").write_text(json.dumps(info, indent=2), encoding="utf8")
    return app, info


def fixture_glb(external=False):
    positions=[(-.4,0,-.4),(.4,0,-.4),(.4,.8,-.4),(-.4,.8,-.4),(-.4,0,.4),(.4,0,.4),(.4,.8,.4),(-.4,.8,.4)]
    faces=[0,2,1,0,3,2,4,5,6,4,6,7,0,1,5,0,5,4,3,7,6,3,6,2,0,4,7,0,7,3,1,2,6,1,6,5]
    binary=b''.join(struct.pack('<3f',*p) for p in positions)+struct.pack('<36H',*faces)
    document={'asset':{'version':'2.0'},'scene':0,'scenes':[{'nodes':[0]}],'nodes':[{'mesh':0}],
      'meshes':[{'primitives':[{'attributes':{'POSITION':0},'indices':1,'material':0}]}],
      'materials':[{'pbrMetallicRoughness':{'baseColorFactor':[.25,.85,.2,1],'metallicFactor':0,'roughnessFactor':.7}}],
      'buffers':[{'byteLength':len(binary)}],'bufferViews':[{'buffer':0,'byteOffset':0,'byteLength':96,'target':34962},{'buffer':0,'byteOffset':96,'byteLength':72,'target':34963}],
      'accessors':[{'bufferView':0,'componentType':5126,'count':8,'type':'VEC3','min':[-.4,0,-.4],'max':[.4,.8,.4]},{'bufferView':1,'componentType':5123,'count':36,'type':'SCALAR'}]}
    if external:document['images']=[{'uri':'https://example.invalid/texture.png'}]
    text=json.dumps(document).encode();text+=b' '*((-len(text))%4)
    return struct.pack('<5I',0x46546c67,2,28+len(text)+len(binary),len(text),0x4e4f534a)+text+struct.pack('<2I',len(binary),0x004e4942)+binary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--create-only", action="store_true", help="Generate fixture and print its URL without serving")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535 or (args.create_only and args.port == 0):
        parser.error("Use a port from 1 to 65535, or port 0 when serving")
    app, info = prepare_fixture(args.workspace, args.port)
    if args.create_only:
        print(json.dumps(info), flush=True)
        return
    server = app.ThreadingHTTPServer(("127.0.0.1", args.port), app.Handler)
    info["url"] = f"http://127.0.0.1:{server.server_port}/scene/{info['scene_id']}"
    Path(info["manifest"]).write_text(json.dumps(info, indent=2), encoding="utf8")
    print(json.dumps(info), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
