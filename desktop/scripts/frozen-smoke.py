"""Exercise installed native workers/codecs with only System32 on PATH (Windows)."""
import argparse
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--backend',type=Path)
    parser.add_argument('--mask-device',choices=['cpu','auto','directml'],default='cpu')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[2]
    installed=(args.backend or Path(os.environ['LOCALAPPDATA'])/'Programs/360 Studio/resources/backend').resolve()
    internal=installed/'_internal'
    base=root/'tools/desktop-qa'
    base.mkdir(parents=True,exist_ok=True)
    output=Path(tempfile.mkdtemp(prefix='frozen-engines-',dir=base))
    images=output/'images';images.mkdir()
    shutil.copy2(root/'desktop/tests/fixtures/panorama.png',images/'one.png')
    (output/'transforms.json').write_text(json.dumps({'w':256,'h':128,'fl_x':128,
        'frames':[{'file_path':'images/one.png'}]}),encoding='utf8')
    weights=output/'cache/torch/hub/checkpoints';weights.mkdir(parents=True)
    for file in (internal/'tools/mask_weights').glob('*.pth'):shutil.copy2(file,weights/file.name)
    env={**os.environ,'PATH':str(Path(os.environ['SystemRoot'])/'System32'),
         'STUDIO_WORKSPACE':str(output),'TORCH_HOME':str(output/'cache/torch'),'MASK_DEVICE':args.mask_device}
    def run(label,args):
        result=subprocess.run(list(map(str,args)),cwd=output,env=env,capture_output=True,
                              text=True,encoding='utf8',timeout=180)
        (output/(label+'.log')).write_text(result.stdout+'\n'+result.stderr,encoding='utf8')
        if result.returncode:raise RuntimeError(f'{label} failed ({result.returncode}); see {output}')
        return result.stdout
    masks=run('masks',[installed/'studio360-backend.exe','--worker-module','app.runtime.mask_compute',output])
    events=[json.loads(line[len('@masks:'):]) for line in masks.splitlines() if line.startswith('@masks:')]
    completed=next(event['complete'] for event in events if 'complete' in event)
    assert completed['mask_model'] and (output/'masks/one.png').is_file(),completed
    video=output/'preview.mp4'
    run('ffmpeg',[internal/'tools/ffmpeg/bin/ffmpeg.exe','-hide_banner','-loglevel','error',
        '-f','lavfi','-i','testsrc2=size=256x128:rate=5','-t','1','-pix_fmt','yuv420p',video])
    preview=[installed/'studio360-backend.exe','--worker-module','app.runtime.capture_preview_worker']
    profile=json.loads(run('preview-profile',[*preview,'profile',video]))
    assert (profile['width'],profile['height'])==(256,128),profile
    frame=subprocess.run(list(map(str,[*preview,'frame',video,'1'])),cwd=output,env=env,
        capture_output=True,timeout=30,check=True).stdout
    assert struct.unpack('<II',frame[:8])==(128,256) and len(frame)==8+128*256*3
    node=internal/'tools/node/bin/node.exe'
    encoded=json.loads(run('spz',[node,'--loader',(internal/'scripts/spark_node_loader.mjs').as_uri(),
        internal/'scripts/encode_model_spz.mjs',root/'desktop/tests/fixtures/splat.ply',output/'model.spz']))
    exported=json.loads(run('glb',[node,internal/'scripts/export_splat_glb.mjs',output/'model.spz',output/'model.glb']))
    assert exported['splats']==128,exported
    with (output/'model.glb').open('rb') as file:assert file.read(4)==b'glTF'
    result={'state':'passed','masks':completed,'video_profile':profile,'video_preview_bytes':len(frame),
            'spz':encoded,'glb':exported,'output':str(output),'path':'System32 only'}
    (output/'result.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    (base/'frozen-engines.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    (base/f'frozen-engines-{args.mask_device}.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps(result))


if __name__=='__main__':main()
