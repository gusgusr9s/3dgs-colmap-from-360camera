// Explicit calculation only: opening the Source form never starts processing.
document.querySelectorAll('form[data-settings-source]').forEach(form=>{
  const button=form.querySelector('[data-temporary-pose]');
  if(!button)return;
  const status=form.querySelector('[data-temporary-pose-status]');
  const frame=form.querySelector('[data-temporary-map-frame]');
  const placeholder=form.querySelector('[data-temporary-map-empty]');
  const stale=form.querySelector('[data-temporary-pose-stale]');
  const settingsKeys=new Set(['source','width','height','fov','views','sample_every','sampling_guard','projection','quality','start_seconds','end_seconds','preview_duration']);
  const signature=body=>JSON.stringify([...body.entries()].filter(([key])=>settingsKeys.has(key)).sort(([a],[b])=>a.localeCompare(b)));
  function payload(){const body=new FormData(form);body.set('source',form.dataset.settingsSource);return body;}
  function previewState(state){form.dataset.previewState=state;form.dispatchEvent(new Event('preview-state'));}
  let sequence=0;
  let lastSnapshot;
  const changed=()=>{
    if(lastSnapshot&&signature(payload())!==lastSnapshot){
      if(stale)stale.hidden=false;
      previewState('stale');
    }
  };
  form.addEventListener('input',changed);form.addEventListener('change',changed);
  button.disabled=false;
  status.textContent='พร้อมทดสอบ · แผนที่จะแสดงในฟอร์มนี้';
  button.addEventListener('click',async()=>{
    if(!form.reportValidity())return;
    button.disabled=true;
    const request=++sequence;
    if(stale)stale.hidden=true;
    if(frame){frame.hidden=true;frame.removeAttribute('src');}
    if(placeholder){placeholder.hidden=false;placeholder.textContent='กำลังเตรียมช่วงทดสอบและแผนที่กล้อง…';}
    previewState('running');
    const duration=form.elements?.namedItem('preview_duration')?.value||'5';
    status.textContent=`กำลังขอคำนวณแผนที่ ${duration} วินาที…`;
    try {
      const body=payload();body.set('action','preview_pose');
      const snapshot=signature(body);lastSnapshot=snapshot;
      const response=await fetch('/actions',{method:'POST',body,headers:{'X-Requested-With':'XMLHttpRequest'}});
      const result=await response.json();
      if(!response.ok||!result.ok)throw new Error(result.error||result.message||'เริ่ม Preview ไม่สำเร็จ');
      if(request!==sequence)return;
      status.replaceChildren();
      const link=document.createElement('a');link.href=result.redirect_to;link.target='_blank';link.rel='noopener';
      link.textContent=`เปิดแผนที่ชั่วคราว ${duration} วินาที · ค่านี้ใช้เฉพาะ Preview`;status.append(link);
      const resultNode=document.createElement('p');status.append(resultNode);
      const poll=async()=>{
        if(request!==sequence||form.isConnected===false)return;
        try{
          const response=await fetch('/api/pose-preview/'+result.redirect_to.split('?')[0].split('/').pop()+'?summary=1',{cache:'no-store'});
          const state=await response.json();
          if(request!==sequence)return;
          if(!response.ok)throw new Error(state.error||'อ่านผลทดสอบไม่ได้');
          if(['queued','running'].includes(state.state)){
            resultNode.textContent=state.step||'กำลังคำนวณแผนที่…';setTimeout(poll,3000);return;
          }
          if(state.state!=='ready')throw new Error(state.error||'พรีวิวหมดอายุ กรุณาทดสอบใหม่');
          const outdated=signature(payload())!==snapshot;
          previewState(outdated?'stale':'ready');if(stale)stale.hidden=!outdated;
          if(frame){
            frame.src=result.redirect_to.split('?')[0]+'?embed=1';frame.hidden=false;
            if(form.closest('dialog')?.open&&!frame.closest('[data-wizard-step]')?.hidden)frame.scrollIntoView({block:'nearest',behavior:'smooth'});
          }
          if(placeholder)placeholder.hidden=true;
          const spacing=state.preview?.spacing,r=spacing?.recommendation;
          resultNode.textContent=spacing?`ระยะห่างตำแหน่งกล้องเฉลี่ย ${spacing.mean.toFixed(4)} หน่วยสัมพัทธ์`+
            (spacing.reference?` · ${(spacing.ratio_to_reference*100).toFixed(1)}% ของงานอ้างอิง ${spacing.reference.job} หลังเทียบความลึกฉาก`:' · ยังเทียบงานอ้างอิงไม่ได้'):'ยังวัดระยะห่างไม่ได้';
          if(!r)return;
          const advice=document.createElement('p');advice.textContent=
            ({increase:'ลองเพิ่ม',decrease:'ลองลด',keep:'คงค่า',inspect:'ตรวจ pose ก่อนเปลี่ยนค่า'}[r.direction])+` เก็บทุก ${r.sample_every} เฟรม (ปัจจุบัน ${r.current_sample_every})`+
            (r.minimum_preview_seconds>Number(duration)?` · เลือกทดสอบ 10 วินาที (ต้องมีอย่างน้อย ${r.minimum_preview_seconds.toFixed(1)} วินาทีเพื่อให้มี 6 ตำแหน่ง)`:'')+' · ค่านี้เป็นข้อเสนอทดลอง ต้องตรวจช่วงเลี้ยว/จุดเชื่อมเพิ่ม';
          status.append(advice);
          if(!['increase','decrease'].includes(r.direction))return;
          const apply=document.createElement('button');apply.type='button';apply.textContent=`ใช้ทุก ${r.sample_every} เฟรม`;apply.disabled=outdated;
          apply.addEventListener('click',()=>{
            const current=signature(payload());
            if(current!==snapshot){advice.textContent='ค่าฟอร์มเปลี่ยนหลังทดสอบ กรุณาทดสอบอีกครั้งก่อนใช้คำแนะนำ';apply.disabled=true;return;}
            for(const [key,value] of [['sample_every',r.sample_every],['sampling_guard','manual']]){
              const input=form.elements.namedItem(key);if(input){input.value=String(value);input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));}
            }
            apply.disabled=true;advice.textContent='ปรับค่าฟอร์มแล้ว · ยังไม่ได้สร้างงานใหม่ กรุณาตรวจ Preview และช่วงเลี้ยวก่อนสร้างจริง';
          });status.append(apply);
        }catch(error){resultNode.textContent=error.message;previewState('error');if(placeholder)placeholder.textContent='ยังสร้างแผนที่ไม่สำเร็จ · ปรับช่วงเวลาหรือจำนวนเฟรม แล้วลองทดสอบใหม่';}
      };
      poll();
    } catch(error) {
      status.textContent=error.message;previewState('error');if(placeholder)placeholder.textContent='ยังเริ่มทดสอบไม่ได้ · ตรวจข้อความด้านบนแล้วลองใหม่';
    } finally {button.disabled=false;}
  });
});
