// Source-aware defaults. Numeric edits remain local until explicitly reapplied.
const keys = ['quality', 'width', 'height', 'fov', 'views', 'sample_every', 'projection'];
const states = new WeakMap();
const field = (form, key) => form.elements.namedItem(key);
const label = (key, value) => key === 'projection'
  ? ({auto:'ตรวจอัตโนมัติ', panorama:'360 equirectangular', walkthrough:'วิดีโอปกติ'}[value] || value)
  : key === 'quality' ? ({preview:'Preview', high:'High', ultra:'Ultra'}[value] || value) : String(value);

function stateFor(form) {
  if (states.has(form)) return states.get(form);
  const state = {dirty:new Set(form.dataset.settingsMode === 'edit' ? keys : []), result:null, sequence:0};
  states.set(form, state);
  form.addEventListener('submit',event=>{
    if(form.dataset.settingsMode==='new' && state.pending && !state.result) {
      event.preventDefault();
      form.querySelector('[data-suggestion-note]').textContent='กำลังคำนวณค่าเริ่มต้น กรุณารอสักครู่ก่อนสร้าง Views';
    }
  });
  for (const key of keys) {
    const input = field(form, key);
    input.addEventListener('input', () => {state.dirty.add(key); renderEstimate(form);});
    input.addEventListener('change', () => {
      state.dirty.add(key);
      if (['quality','projection','fov'].includes(key)) loadSuggestions(form);
      else renderEstimate(form);
    });
  }
  form.querySelector('[data-apply-suggestions]').addEventListener('click', () => {
    if (!state.result) return;
    for (const key of keys) {field(form,key).value=state.result.settings[key]; state.dirty.delete(key);}
    renderEstimate(form);
    form.querySelector('[data-suggestion-note]').textContent='ใช้ค่าที่แนะนำแล้ว · แก้แต่ละช่องต่อได้';
  });
  const select = form.querySelector('[data-template-select]');
  const apply = form.querySelector('[data-apply-template]');
  select?.addEventListener('change', () => renderTemplate(form));
  apply?.addEventListener('click', () => {
    const template = state.result?.templates?.find(item => item.id === select.value);
    if (!template) return;
    for (const key of [...keys, 'start_seconds', 'end_seconds']) {
      const input = field(form, key);
      if (input && template.adapted_settings[key] !== undefined) {
        input.value = template.adapted_settings[key]; state.dirty.add(key);
      }
    }
    renderEstimate(form);
    form.querySelector('[data-suggestion-note]').textContent = 'ใช้ Template แล้ว · ตรวจภาพ Preview ก่อนเริ่มสร้าง';
    // Refresh hints for this quality without overwriting the selected recipe.
    loadSuggestions(form);
  });
  form.querySelectorAll('[data-use-setting]').forEach(button => button.addEventListener('click', () => {
    if (!state.result) return;
    const key=button.dataset.useSetting;
    field(form,key).value=state.result.settings[key]; state.dirty.delete(key);
    if (['quality','projection','fov'].includes(key)) loadSuggestions(form);
    else renderEstimate(form);
  }));
  return state;
}

function renderTemplate(form) {
  const state = states.get(form), select = form.querySelector('[data-template-select]');
  if (!select) return;
  const template = state.result?.templates?.find(item => item.id === select.value);
  form.querySelector('[data-apply-template]').disabled = !template;
  const info = form.querySelector('[data-template-info]');
  info.replaceChildren();
  if (!template) return;
  const profile = template.source_profile, settings = template.adapted_settings, proof = template.evidence;
  const text = document.createElement('p');
  text.textContent = `สำเร็จจาก Source ${profile.width}×${profile.height} · ${template.settings.quality} · PSNR ${proof.psnr_db.toFixed(2)} dB · pose ${(proof.coverage*100).toFixed(0)}% · ${proof.training_steps?.toLocaleString() || '?'} steps`;
  const note = document.createElement('p');
  note.textContent = `${template.adaptation} · ภาพ ${settings.width}×${settings.height} · เก็บทุก ${template.capture_plan.effective_sample_every} เฟรม · ${template.warning}`;
  const link = document.createElement('a');
  link.href = '/model/' + encodeURIComponent(template.model); link.textContent = 'เปิด Model ต้นแบบ';
  info.append(text, note);
  if (template.model_available !== false) info.append(link);
  if (proof.comparison_limitation) {
    const limit = document.createElement('p'); limit.textContent = proof.comparison_limitation; info.append(limit);
  }
}

function renderEstimate(form) {
  const state=states.get(form), output=form.querySelector('[data-view-estimate]');
  if (!state?.result) return;
  const profile=state.result.profile;
  const quality=field(form,'quality').value, projection=field(form,'projection').value;
  const panorama=projection==='panorama' || (projection==='auto' && profile.detected_projection==='panorama');
  const requested=Math.max(1, Math.floor(Number(field(form,'sample_every').value) || 1));
  const interval=profile.kind==='video' && profile.fps ? Math.min(requested,Math.max(1,Math.floor(profile.fps*.3))) : requested;
  const horizontal=panorama ? Math.max(1,Math.floor(Number(field(form,'views').value) || 1)) : 1;
  const vertical=panorama && quality!=='preview' ? 2*Math.max(4,Math.ceil(horizontal/2)) : 0;
  const start=Number(field(form,'start_seconds')?.value || 0), end=Number(field(form,'end_seconds')?.value || 0);
  const first=profile.fps ? Math.ceil(start*profile.fps-1e-8) : 0;
  const last=end && profile.fps ? Math.min(profile.frame_count,Math.ceil(end*profile.fps-1e-8)) : profile.frame_count;
  const positions=last ? Math.max(0,Math.ceil((last-first)/interval)) : null;
  const parts=[];
  if (positions) parts.push(`ประมาณ ${positions.toLocaleString()} เฟรม × ${horizontal+vertical} มุม = ${(positions*(horizontal+vertical)).toLocaleString()} ภาพ`);
  else parts.push('ยังไม่ทราบจำนวนเฟรมทั้งหมด');
  if (panorama && vertical) parts.push(`แนวนอน ${horizontal} + เงย/ก้ม ${vertical} มุม`);
  if (profile.kind==='video') {
    parts.push(`ใช้จริงทุก ${interval} เฟรม`);
    if (profile.fps) parts.push(`ห่าง ${(interval/profile.fps).toFixed(3)} วินาที`);
    if (interval<requested) parts.push(`ลดจากค่าที่ขอ ${requested} เพื่อให้ช่วงห่างไม่เกินประมาณ 0.3 วินาที`);
  }
  output.textContent=parts.join(' · ');
  const geometryNote=form.querySelector('[data-geometry-note]');
  const fov=Number(field(form,'fov').value);
  geometryNote.textContent=panorama && fov>0 && 360/horizontal>=fov
    ? 'มุมแนวนอนที่เลือกยังไม่ซ้อนกัน ควรเพิ่มมุมต่อเฟรมหรือลองใช้ค่าที่แนะนำ' : '';
  if (typeof CustomEvent !== 'undefined') form.dispatchEvent(new CustomEvent('capture-settings'));
}

async function loadSuggestions(form) {
  const state=stateFor(form), sequence=++state.sequence;
  state.pending=true;
  state.abort?.abort(); state.abort=new AbortController();
  const note=form.querySelector('[data-suggestion-note]');
  note.textContent='กำลังอ่านข้อมูลไฟล์และประวัติ…';
  const query=new URLSearchParams({source:form.dataset.settingsSource});
  for (const key of ['quality','projection','fov']) {
    if (state.dirty.has(key)) query.set(key,field(form,key).value);
  }
  try {
    const response=await fetch('/api/view-suggestions?'+query,{cache:'no-store',signal:state.abort.signal});
    const result=await response.json();
    if (sequence!==state.sequence) return;
    if (!response.ok) throw new Error(result.error || 'อ่านข้อมูลไฟล์ไม่ได้');
    state.result=result;
    const select = form.querySelector('[data-template-select]');
    if (select) {
      const selectedId = select.value;
      select.replaceChildren();
      const placeholder = document.createElement('option'); placeholder.value = '';
      placeholder.textContent = result.templates?.length ? 'เลือก Template ที่สร้าง Model สำเร็จ' : 'ยังไม่มี Template ที่เข้ากับ Source นี้';
      select.append(placeholder);
      for (const template of result.templates || []) {
        const option = document.createElement('option'); option.value = template.id;
        option.textContent = `${template.name} · ${template.source_profile.width}×${template.source_profile.height} · ${template.settings.quality}`;
        select.append(option);
      }
      select.disabled = !result.templates?.length;
      select.value = result.templates?.some(item => item.id === selectedId) ? selectedId : '';
      renderTemplate(form);
    }
    for (const key of keys) {
      if (!state.dirty.has(key)) field(form,key).value=result.settings[key];
      form.querySelector(`[data-suggestion-for="${key}"]`).textContent=
        `แนะนำ ${label(key,result.settings[key])} · ${result.reasons[key]}`;
    }
    form.querySelectorAll('[data-use-setting], [data-apply-suggestions]').forEach(button => {button.disabled=false;});
    const profile=result.profile;
    const info=[`${profile.width} × ${profile.height} px`, profile.kind==='image' ? 'ภาพนิ่ง' : `${profile.fps ? profile.fps.toFixed(2)+' FPS' : 'ไม่ทราบ FPS'}`];
    if (profile.duration_seconds) info.push(`${profile.duration_seconds.toFixed(1)} วินาที`);
    form.querySelector('[data-source-profile]').textContent=info.join(' · ');
    const selected=result.history.selected;
    note.textContent=selected
      ? `ประกอบกับประวัติ ${selected.level===2 ? 'Model' : selected.level===1 ? 'Camera Pose' : 'Views'} สำเร็จจาก job ${selected.job} · ช่องที่แก้เองจะเก็บไว้`
      : 'คำนวณจากไฟล์ต้นฉบับและคุณภาพที่เลือก · ยังไม่มีประวัติสำเร็จที่ใช้กับชุดนี้ได้';
    const warnings=form.querySelector('[data-suggestion-warnings]'); warnings.replaceChildren();
    for (const message of result.warnings) {
      const item=document.createElement('div'); item.className='suggestion-warning'; item.textContent=message; warnings.append(item);
    }
    renderEstimate(form);
  } catch (error) {
    if (error.name==='AbortError' || sequence!==state.sequence) return;
    note.textContent='แนะนำค่าอัตโนมัติไม่ได้: '+error.message+' · ยังแก้ค่าด้วยตัวเองได้';
  } finally {
    if(sequence===state.sequence) state.pending=false;
  }
}

document.addEventListener('click', event => {
  const button=event.target.closest('[data-modal]');
  if (!button || button.disabled) return;
  const form=document.getElementById(button.dataset.modal)?.querySelector('form[data-settings-source]');
  if (form) loadSuggestions(form);
});
