Sample video: “Walk through a seemingly endless cellar below an appartment block in Munich, Germany”
Author: Pittigrilli
Source: https://commons.wikimedia.org/wiki/File:Walk_through_a_seemingly_endless_cellar_below_an_appartment_block_in_Munich,_Germany.webm
License: CC0 1.0 Universal Public Domain Dedication
Original file: 1080 x 1920 WebM, approximately 3 min 57 sec.
Downloaded from Wikimedia Commons on 2026-10-07.

Sample 360 video: `sample_360.mp4`
Source dataset: OmniPhotos: Casual 360° VR Photography (Unprocessed.zip, Temple1 clip)
Creators: Tobias Bertel, Mingze Yuan, Reuben Lindroos, Christian Richardt
Dataset DOI: https://doi.org/10.15125/BATH-00948
Dataset record: https://researchdata.bath.ac.uk/948/
Original archive path: Unprocessed/Temple1/VID_20190402_094020_00_016-flowstate.mp4
Capture: Insta360 One X; stitched equirectangular video; 3840×1920, 9.42 seconds.
License: Creative Commons Attribution 4.0 International (CC BY 4.0).
Attribution: Bertel, T., Yuan, M., Lindroos, R., & Richardt, C. (2020). Datasets for “OmniPhotos: Casual 360° VR Photography”. Version 2. University of Bath Research Data Archive.

Test 360 video: `test_360_tram.mp4`
Source dataset: 360NPR, sequence `Tram` (30 equirectangular frames)
Dataset: https://huggingface.co/datasets/SiminKou/360NPR
Paper: Neural Panoramic Representation for Spatially and Temporally Consistent 360° Video Editing
Format: 960×480 equirectangular, encoded locally as H.264 at 10 fps (3 seconds)
License: Creative Commons Attribution 4.0 International (CC BY 4.0)
Downloaded and encoded from the published frames on 2026-10-07.
Note: initially used as a limited-camera-motion test. In the 2026-10-08 batch,
native spherical COLMAP registered all 15 sampled panorama frames; Brush then
failed to read some training JPEGs with `early eof`. This decoder failure does
not establish insufficient parallax. The separate codec-recovery attempt on
2026-10-08 trained 20,000 steps on AMD/Vulkan and produced a model (15/15 poses).
Inspected GPU renders match static buildings and the road in the checked views,
with ghosting/blur around moving people and masked lower regions. Perspective
images are only 192×192; this is not proof of accurate geometry from every angle.

Moving-camera 360 test video: `test_360_corridor.mp4`
Source dataset: DyOmni, sequence `Corridor` (30 sampled equirectangular frames)
Dataset: https://huggingface.co/datasets/SiminKou/DyOmni
Paper/project: OmniPlane
Format: 1920×960 equirectangular, encoded locally as H.264 (approximately 3 seconds)
Capture: casually moving handheld 360° camera; source sequence contains 100 frames
License: Creative Commons Attribution 4.0 International (CC BY 4.0)
Downloaded frames 1, 4, 7, ..., 88 and encoded them on 2026-10-07.

Exported Insta360 X4 test video: `insta360_x4_exported_sample.mp4`
Source: 360camsters public test data download page
Source page: https://www.360camsters.com/download/
Original download: `Insta360 X4 equirectangular panoramas.zip`
Camera: Insta360 X4
Original format: 77 exported equirectangular JPEG panoramas, 8192×4096 (2:1)
Local test format: H.264 MP4, 4096×2048, 5 fps, 15.4 seconds
SHA-256 of downloaded ZIP: `5A9FBFF14D1F2745432E947ECD8A30823861D133ACA8119E5FB8E217BF3412B8`
SHA-256 of local MP4: `390EB4437EC85ACA57172CD1F87E0EAAA72BC748A94C19FFF713195203179E2F`
Downloaded and encoded on 2026-10-07. The source page provides this material as test data;
no separate redistribution license is stated on that page.

Additional original stitched Insta360 One X MP4s (downloaded 2026-10-08):

Dataset: OmniPhotos: Casual 360° VR Photography, Version 2.
Creators: Tobias Bertel, Mingze Yuan, Reuben Lindroos, Christian Richardt.
Record: https://researchdata.bath.ac.uk/948/
DOI: https://doi.org/10.15125/BATH-00948
License: CC BY 4.0. Original archive: https://researchdata.bath.ac.uk/948/6/Unprocessed.zip

| Local source | Original archive entry | SHA-256 |
| --- | --- | --- |
| `omniphotos_alley.mp4` | `Unprocessed/Alley/VID_20200408_160043_00_172-flowstate.mp4` | `3d2d4e4cc043a2f8bc3cc8c0ccc91628c1ccf4063b5abafcd78bac8594512563` |
| `omniphotos_cathedral.mp4` | `Unprocessed/Cathedral/VID_20190510_180308_00_044-flowstate.mp4` | `3d13138a2677f22e80f5824e5108d028434f8f592005a544f7558e02d608b1bb` |
| `omniphotos_ship.mp4` | `Unprocessed/Ship/VID_20190610_181806_00_047-flowstate.mp4` | `c729df774386a65f8e2ed6d4b99018e7607d223be96fdfafca1853017bc7fc17` |

These MP4s were extracted unchanged with HTTP byte ranges and ZIP CRC validation.
Each accompanying `.source.json` records file size, attribution and archive path.
The app independently reconstructs poses and Gaussian splats from the videos;
the dataset's supplied camera poses, proxy meshes and preprocessed renders are not imported.
The first attempts use the first four seconds of each new video.
Additional full-length Cathedral/Ship attempts are recorded separately, preserving the failed short-clip attempts.

## Files included in Git

- `sample_360_preview.mp4`: derived from the OmniPhotos Temple1 `sample_360.mp4` documented above. Changes: retain the first 8 seconds, resize from 3840x1920 to 1920x960, reduce from 50 fps to 15 fps, re-encode as H.264/yuv420p at CRF 27 with a 4 Mbit/s maximum rate and 8 Mbit buffer, and omit audio and source metadata. The 2:1 equirectangular projection is preserved. License: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Attribution: Bertel, T., Yuan, M., Lindroos, R., & Richardt, C. (2020). Datasets for OmniPhotos: Casual 360 VR Photography. Version 2. University of Bath Research Data Archive. [Dataset record](https://researchdata.bath.ac.uk/948/), [DOI](https://doi.org/10.15125/BATH-00948).
- `test_360_corridor.mp4` and `test_360_tram.mp4`: the small locally encoded samples documented above, included unchanged.

The remaining media and archives listed above are local reference inputs and are excluded from Git. The `.source.json` files describe the full original OmniPhotos downloads, not the compressed preview.

To reproduce the preview from the original file (FFmpeg required):

```bash
ffmpeg -n -i data/raw/sample_360.mp4 -t 8 -map 0:v:0 -an -vf "scale=1920:960:flags=lanczos,fps=15" -c:v libx264 -preset medium -crf 27 -maxrate 4M -bufsize 8M -pix_fmt yuv420p -movflags +faststart -map_metadata -1 data/raw/sample_360_preview.mp4
```
