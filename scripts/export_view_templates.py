"""Persist proven local model recipes as small, reusable repository presets."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.web import server as w
from app.pipeline.view_templates import model_template


def main():
    records=[];seen=set()
    models=sorted(w.MODELS.glob('*.ply'),key=lambda p:p.stat().st_mtime,reverse=True)
    for model in models:
        record=w.model_info(model)
        template=model_template(record)
        if not template:continue
        signature=(template['source_profile'].get('fingerprint'),
                   *(template['settings'].get(key) for key in w.DEFAULT_VIEW_SETTINGS))
        if signature in seen:continue
        seen.add(signature)
        quality=record['quality']
        records.append({key:record.get(key) for key in
                        ('model','label','job','run_id','state','finished_at','source_profile','view_settings','settings')})
        records[-1]['quality']={key:quality[key] for key in (
            'registered_panoramas','input_panoramas','registered_views','input_views',
            'eval_psnr_mean_db','training_steps','cumulative_training_steps','quality_preset',
            'max_splats','pose_source','comparison_limitation') if key in quality}
    target=ROOT/'app/pipeline/view_template_presets.json'
    # Retain recipes whose model was deliberately removed from the local library.
    for record in w.read_json(target,{}).get('templates',[]):
        template=model_template(record)
        if not template:continue
        signature=(template['source_profile'].get('fingerprint'),
                   *(template['settings'].get(key) for key in w.DEFAULT_VIEW_SETTINGS))
        if signature not in seen:records.append(record);seen.add(signature)
    w.write_json(target,{'description':'Recipes from successful local runs; camera poses/depth are not bundled',
                         'exported_at':w.utc_now(),'templates':records})
    print(f'Persisted {len(records)} successful recipes to {target}',flush=True)


if __name__=='__main__':main()
