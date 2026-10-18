"""Make a labeled scientific figure from real held-out Brush GPU renders."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw, ImageFont
from app.web import server as app


def comparison(job, run, stem=None):
    directory = app.JOBS / job / 'model_runs' / run
    status = app.read_json(directory / 'status.json')
    parent = directory.parent / status['parent_run_id']
    parent_status = app.read_json(parent / 'status.json')
    rendered = directory / 'brush_export' / f"eval_{status['quality']['training_steps']}"
    if stem is None:
        stem = sorted(rendered.glob('*.png'))[0].stem
    frames = app.read_json(directory / 'dataset/transforms.json')['frames']
    frame = next(f for f in frames if Path(f['file_path']).stem == stem)
    paths = [directory / 'dataset' / frame['file_path'],
             parent / 'brush_export' / f"eval_{parent_status['quality']['training_steps']}" / (stem+'.png'),
             rendered / (stem+'.png')]
    images = [Image.open(path).convert('RGB') for path in paths]
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 22)
    title_font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 25)
    size, gap = 640, 12
    figure = Image.new('RGB', (size*3+gap*4, size+144), '#101827')
    draw = ImageDraw.Draw(figure)
    draw.text((gap,12), 'Held-out GPU render comparison - '+stem, fill='white',font=title_font)
    draw.text((gap,48), 'Brush checkpoint renders before cleanup. These are not browser screenshots.',
              fill='#bec9da', font=font)
    for i,(label,img) in enumerate(zip(['Input image','Before fine tuning','After fine tuning'],images)):
        x = gap+i*(size+gap)
        draw.text((x,87),label,fill='white',font=font)
        figure.paste(img.resize((size,size),Image.Resampling.LANCZOS),(x,122))
    output = directory / ('quality_comparison_'+stem+'.png')
    figure.save(output)
    return output


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--job',required=True)
    parser.add_argument('--run',required=True)
    parser.add_argument('--stem')
    args=parser.parse_args()
    print(comparison(args.job,args.run,args.stem))
