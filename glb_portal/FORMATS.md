# Supported formats and interoperability

Model Room accepts ordinary model files. No exporter, custom metadata, manifest, or link to the main application is required. Geometry, materials, textures, animation clips and perspective cameras in glTF use their native glTF representation.

## Ordinary ZIP bundles

Upload an archive with relative references intact, for example:

```text
scene.zip
  model/scene.gltf
  model/buffer.bin
  textures/paint.png
```

The glTF image URI can be `../textures/paint.png`. OBJ bundles work similarly with `.mtl` files and their texture paths. JPEG, PNG, WebP and AVIF images are accepted. Missing files and references outside the archive are rejected. ZIPs can also contain several independent GLB, OBJ, PLY, SPZ or SPLAT files. Each model retains its coordinates; no alignment is inferred. The preview shows the files and placement before committing one scene to the selected Project. Unrecognized `scene.json` or glTF extras do not prevent ordinary geometry from opening.

Only referenced model data is extracted and served. Bundled HTML, JavaScript, Python and executables are ignored. Limits: 128 root models, 4,096 archive entries, each file within the configured upload limit, expanded archive size within twice that limit. Abandoned import previews expire after 24 hours. The original archive remains available for download after import.

The current renderers support ordinary mesh glTF beside SPZ/PLY Gaussian assets in ZIPs. Gaussian GLB/glTF uses a different renderer and cannot be mixed with SPZ/Gaussian PLY in the same ZIP yet.

## Optional navigation information

glTF's `extras` mechanism can hold optional application data. Model Room supports a documented `navigation` object in the selected scene's extras (or the document's root extras). A generic ZIP can instead include a top-level `navigation.json` containing that object. This is an optional Model Room convention, **not an official glTF physics extension**.

```json
{
  "version": 1,
  "units": "meters",
  "view": { "position": [6, 4, 6], "target": [0, 1, 0] },
  "spawn": { "position": [0, 1.65, 3], "yaw": 0 },
  "player": { "height": 1.65, "radius": 0.25, "speed": 1.4 },
  "colliders": [
    { "type": "box", "kind": "floor", "center": [0, -0.1, 0], "size": [10, 0.2, 10] },
    { "type": "box", "kind": "wall", "center": [0, 1.5, -5], "size": [10, 3, 0.2], "rotation": [0, 0, 0] }
  ]
}
```

Coordinates use Y up, dimensions are full box dimensions, rotations are XYZ Euler angles in radians, and spawn is the eye position. `center` and `position` are accepted for boxes. Only supplied boxes participate in collision; a surface mesh is not reconstructed. Without navigation data, Walk keeps the current eye height. Named views can still be saved for every file format using the portal.

For compatibility, GLBs with `extras.scene_document` and ZIPs containing `{ "scene": ..., "assets": [...] }` in `scene.json` are also accepted. This adapter preserves parented collider transforms from the main application's scene editor. When a compatible checksum manifest is present, referenced model checksums are verified. These adapters are optional and do not alter ordinary model handling.

## Format limits

- GLB and glTF: version 2.0. Standalone files must embed their dependencies; relative dependencies belong in ZIPs. Core mesh/material/skin/animation data and `KHR_gaussian_splatting` are supported; the first animation clip plays automatically. Draco, Meshopt and KTX2 decoders are not bundled yet. An unknown required extension may still prevent rendering.
- OBJ: UTF-8 geometry and vertex colors. Standalone OBJ uses geometry without external materials; ZIP adds MTL and supported textures.
- PLY mesh/points: ASCII, little-endian or big-endian binary, supported scalar types and face lists. Gaussian PLY requires float32 little-endian data with complete position, SH0 color, opacity, scale and quaternion fields.
- SPZ: legacy gzip v1/v2/v3, SH degree 0–3. SPZ v4's ZSTD format requires a newer decoder and is not supported by the pinned runtime.
- SPLAT: the common raw 32-byte record format (float32 position/scale, byte RGBA/quaternion).

The upload limit is 2 GB by default and is configurable. Successful storage does not imply every viewer's GPU can render a file of that size. Files larger than 64 MB are not automatically opened on login.

## Dependencies and maintenance

Three.js and Spark are bundled locally. Run `python glb_portal/install_assets.py` to verify/reinstall the additional official Three.js loaders from pinned URLs in `vendor/loaders.json`. The collision and transform modules are copied from `assets/app/` to keep the standalone portal independent at runtime; HTTP tests check that these copies match the shared source.

References: [glTF 2.0 specification](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html), [Three.js loaders](https://threejs.org/docs/), [Niantic SPZ format](https://github.com/nianticlabs/spz/blob/main/README.md).
