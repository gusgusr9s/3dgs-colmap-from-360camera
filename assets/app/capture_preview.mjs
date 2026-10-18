// Preview the exact sampling plan without writing a job or modifying the source.
const captureStates = new WeakMap();
const captureKeys = ['width','height','fov','views','sample_every','sampling_guard','projection','quality','start_seconds','end_seconds'];
const control = (form, selector) => form.querySelector(selector);

function captureQuery(form) {
  const query = new URLSearchParams({source:form.dataset.settingsSource});
  for (const key of captureKeys) query.set(key, form.elements.namedItem(key)?.value || '0');
  if(form.elements.namedItem('pipeline_mode')?.value==='preview5') {
    query.set('preview_seconds','5');
  }
  return query;
}

function imageURL(form, state, ordinal, direction, raw=false, size=512) {
  const query = new URLSearchParams(state.query);
  query.set('ordinal', ordinal); query.set('direction', direction);
  query.set('raw', raw ? '1' : '0'); query.set('size', size);
  query.set('version', state.result.profile.modified_ns);
  return '/api/capture-image?' + query;
}

function imageStatus(form,text,error=false) {
  control(form,'[data-capture-image-note]').textContent=text;
  control(form,'[data-capture-image-status]').dataset.state=error?'error':'loading';
  control(form,'[data-capture-retry]').hidden=!error;
}

function cancelImages(state) {
  ++state.imageSequence;state.imageAbort?.abort();
  clearTimeout(state.imageTimeout);
}

async function loadImages(form,ordinal,direction) {
  const state=captureStates.get(form);cancelImages(state);
  const sequence=state.imageSequence, abort=new AbortController();state.imageAbort=abort;
  const view=control(form,'[data-capture-view]'),raw=control(form,'[data-capture-raw]');
  view.classList.add('loading');raw.classList.add('loading');
  imageStatus(form,'กำลังโหลดภาพมุมที่เลือก… · CPU preview แยกจากคิว Model');
  let timedOut=false;
  const timeout=setTimeout(()=>{timedOut=true;abort.abort();},35000);state.imageTimeout=timeout;
  const fresh=()=>sequence===state.imageSequence && !abort.signal.aborted;
  try {
    // One selected crop, then its panorama. The server shares the decoded frame cache.
    for(const [image,isRaw] of [[view,false],[raw,true]]) {
      const response=await fetch(imageURL(form,state,ordinal,direction,isRaw),{signal:abort.signal});
      if(!response.ok) {
        const details=await response.json().catch(()=>({}));
        throw new Error(details.error||`อ่านภาพไม่ได้ (HTTP ${response.status})`);
      }
      const blob=await response.blob();if(!fresh())return;
      const url=URL.createObjectURL(blob),previous=state.urls.get(image);
      state.urls.set(image,url);image.src=url;
      if(previous)URL.revokeObjectURL(previous);
      if(typeof image.decode==='function')await image.decode();
      if(!fresh())return;
      image.classList.remove('loading');
      if(isRaw)control(form,'[data-clip-video]').poster=url;
    }
    if(fresh()) {
      imageStatus(form,'พรีวิวพร้อม · โหลดเฉพาะตำแหน่งและมุมที่เลือก · CPU / ดิสก์ยังใช้ร่วมกับงานอื่น');
      control(form,'[data-capture-image-status]').dataset.state='ready';
    }
  } catch(error) {
    if(sequence!==state.imageSequence)return;
    if(error.name==='AbortError' && !timedOut)return;
    imageStatus(form,timedOut?'โหลดพรีวิวเกิน 35 วินาที · กดลองใหม่หรือเลือกช่วงต้นคลิป':error.message,true);
  } finally {clearTimeout(timeout);}
}

function showCapture(form) {
  const state = captureStates.get(form);
  if (!state?.result) return;
  const {plan, profile} = state.result;
  const position = control(form,'[data-capture-position]');
  const yaw = control(form,'[data-capture-yaw]');
  position.max = Math.max(0, (plan.positions || 1) - 1);
  position.value = Math.min(Number(position.value), Number(position.max));
  yaw.max = plan.directions.length - 1;
  yaw.value = Math.min(Number(yaw.value), Number(yaw.max));
  const ordinal = Number(position.value), direction = Number(yaw.value);
  const index = plan.start_frame + ordinal * plan.effective_sample_every;
  const angle = plan.directions[direction];
  const time = profile.fps ? (index / profile.fps).toFixed(2) + ' วินาที' : '';
  control(form,'[data-capture-frame]').textContent = `frame ${index} · ${time} · เฟรมที่ ${ordinal+1}/${plan.positions || '?'}`;
  control(form,'[data-capture-direction]').textContent = plan.panorama
    ? `มุม ${direction+1}/${plan.directions.length} · ทิศ ${angle.yaw.toFixed(0)}° · เงย/ก้ม ${angle.pitch.toFixed(0)}° · ${state.result.settings.width} × ${state.result.settings.height} px`
    : 'ภาพจากวิดีโอปกติ · รักษาสัดส่วนตามค่าที่ตั้งไว้';
  loadImages(form,ordinal,direction);
  const strip = control(form,'[data-capture-strip]'); strip.replaceChildren();
  // Show actual adjacent sampled frames, even when sampling was capped for overlap.
  for (let n=Math.max(0,ordinal-3); n<=Math.min((plan.positions || 1)-1,ordinal+3); n++) {
    const button=document.createElement('button'); button.type='button';
    button.className=n===ordinal ? 'active' : '';
    const thumb=document.createElement('span'); thumb.textContent=`${n===ordinal?'●':'○'} ${n+1}`;
    const caption=document.createElement('span');
    caption.textContent=`#${plan.start_frame+n*plan.effective_sample_every} · ${profile.fps ? ((plan.start_frame+n*plan.effective_sample_every)/profile.fps).toFixed(2)+'s' : ''}`;
    button.append(thumb,caption);
    button.addEventListener('click',()=>{position.value=n;showCapture(form);}); strip.append(button);
  }
  const directions = control(form,'[data-capture-directions]'); directions.replaceChildren();
  for (const n of [...new Set([0,Math.floor(plan.directions.length/4),Math.floor(plan.directions.length/2),plan.directions.length-1])]) {
    const button=document.createElement('button'); button.type='button'; button.className=n===direction ? 'active' : '';
    const thumb=document.createElement('span'); thumb.textContent=n===direction?'●':'○';
    const caption=document.createElement('span'), a=plan.directions[n]; caption.textContent=`${a.yaw.toFixed(0)}° / ${a.pitch.toFixed(0)}°`;
    button.append(thumb,caption);button.addEventListener('click',()=>{yaw.value=n;showCapture(form);});directions.append(button);
  }
  control(form,'[data-direction-control]').hidden = !plan.panorama;
  directions.hidden = !plan.panorama;
  const status = control(form,'[data-capture-status]');
  status.textContent = `${plan.positions || '?'} ตำแหน่ง × ${plan.views_per_frame} มุม = ${plan.total_views?.toLocaleString() || '?'} ภาพ · เก็บจริงทุก ${plan.effective_sample_every} เฟรม` +
    (plan.sample_gap_seconds ? ` (${plan.sample_gap_seconds.toFixed(3)} วินาที)` : '') +
    (plan.effective_sample_every < plan.requested_sample_every ? ` · ลดจาก ${plan.requested_sample_every} เพื่อให้ภาพใกล้กันพอ` : '');
  const warnings=[];
  if ((plan.positions || 0) < 6) warnings.push('ช่วงนี้มีน้อยกว่า 6 เฟรม: ดู Views ได้ แต่ควรขยายช่วงก่อนสร้างโมเดล');
  if (plan.total_views > 8000) warnings.push('ภาพจำนวนมาก ควรแบ่งคลิปตามพื้นที่ก่อนประมวลผล');
  if (!profile.fps && profile.kind==='video') warnings.push('อ่าน FPS ไม่ได้ เลือกช่วงเวลาไม่ได้');
  warnings.push('เลือกช่วงที่กล้องเดินผ่านฉากนิ่ง ภาพชัดและเห็นพื้นผิวซ้ำกัน การหมุนอยู่กับที่อย่างเดียวไม่สร้างความลึก 3D');
  control(form,'[data-capture-warning]').textContent=warnings.join(' · ');
  const auto=control(form,'[data-auto-model]');
  if(auto){auto.disabled=(plan.positions || 0)<6 || profile.kind!=='video';auto.checked=!auto.disabled && state.autoWanted;}
  const mode=form.elements.namedItem('pipeline_mode');
  const submit=form.closest('dialog')?.querySelector?.(`button[type="submit"][form="${form.id}"]`);
  if(submit) {
    submit.textContent=mode ? ({full:'เริ่มสร้างโมเดล 3D',preview5:'ทดลองไม่เกิน 5 วินาที → ดูแผนที่กล้อง',poses:'สร้าง Views + แผนที่กล้อง',views:'สร้าง Views เท่านั้น'}[mode.value]) : auto?.checked ? 'เริ่มสร้างโมเดล 3D' : 'สร้าง Views เท่านั้น';
    if(mode)submit.disabled=mode.value!=='views' && ((plan.positions||0)<6 || profile.kind!=='video');
  }
}

function setupClip(form, result) {
  const state=captureStates.get(form), video=control(form,'[data-clip-video]');
  const duration=result.profile.duration_seconds;
  const available=result.profile.kind==='video' && !!duration;
  video.hidden=result.profile.kind!=='video';
  control(form,'[data-clip-controls]').hidden=!available;
  control(form,'[data-clip-actions]').hidden=!available;
  if (!state.videoAttached && result.profile.kind==='video') {
    video.src=result.source_url;
    document.dispatchEvent(new CustomEvent('video-preview-source', {detail:{video,source:form.dataset.settingsSource}}));
    state.videoAttached=true;
  }
  if (available) {
    for (const selector of ['[data-clip-start]','[data-clip-end]','[data-clip-start-number]','[data-clip-end-number]']) control(form,selector).max=duration;
    control(form,'[data-clip-start]').value=result.plan.start_seconds;
    control(form,'[data-clip-end]').value=result.plan.end_seconds;
    control(form,'[data-clip-label]').textContent=`${duration.toFixed(2)} วินาที · ลากช่วงหรือใช้เวลาที่กำลังเล่น`;
    if (video.currentTime < result.plan.start_seconds || video.currentTime >= result.plan.end_seconds) video.currentTime=result.plan.start_seconds;
    if (!state.initialized && !Number(control(form,'[data-clip-end-number]').value)) {
      control(form,'[data-clip-end-number]').value=Number(duration.toFixed(6));
      state.query=captureQuery(form).toString();
    }
  } else {
    control(form,'[data-clip-label]').textContent=result.profile.kind==='image' ? 'ภาพนิ่ง' : 'ไม่ทราบความยาวคลิป';
  }
  state.initialized=true;
}

async function loadCapture(form) {
  const state=captureStates.get(form);
  const sequence=++state.sequence;
  state.abort?.abort(); state.abort=new AbortController(); state.pending=true; state.valid=false;
  const abort=state.abort;
  cancelImages(state);
  imageStatus(form,'กำลังอ่านข้อมูลคลิป / ตรวจช่วงเวลา…');
  const status=control(form,'[data-capture-status]'); status.textContent='กำลังอัปเดตภาพตามค่าที่เลือก…';
  state.query=captureQuery(form).toString();
  const query=new URLSearchParams(state.query);query.set('ordinal',control(form,'[data-capture-position]').value);
  let timedOut=false;
  const timeout=setTimeout(()=>{timedOut=true;abort.abort();},30000);
  try {
    const response=await fetch('/api/capture-plan?'+query,{signal:abort.signal,cache:'no-store'});
    const result=await response.json();
    if (sequence!==state.sequence) return;
    if (!response.ok) throw new Error(result.error || 'สร้างพรีวิวไม่ได้');
    state.result=result; state.valid=true;
    setupClip(form,result);showCapture(form);
  } catch (error) {
    if (sequence!==state.sequence || error.name==='AbortError'&&!timedOut) return;
    status.textContent=timedOut?'อ่านข้อมูลคลิปเกิน 30 วินาที':error.message;
    imageStatus(form,status.textContent+' · กดลองใหม่ได้',true);
    const auto=control(form,'[data-auto-model]');if(auto)auto.disabled=true;
    // Keep previous images labeled as stale when the newly selected range is invalid.
    control(form,'[data-capture-frame]').textContent='ภาพจากค่าก่อนหน้า · กรุณาแก้ช่วงเวลา';
  } finally {clearTimeout(timeout);if(sequence===state.sequence)state.pending=false;}
}

function scheduleCapture(form) {
  const state=captureStates.get(form);if(!state)return;
  state.valid=false;state.pending=false;++state.sequence;state.abort?.abort();
  cancelImages(state);
  clearTimeout(state.timer);state.timer=setTimeout(()=>loadCapture(form),250);
}

function initCapture(form) {
  if(captureStates.has(form))return;
  const auto=control(form,'[data-auto-model]');
  const state={sequence:0,imageSequence:0,urls:new Map(),query:'',result:null,initialized:false,autoWanted:auto?.checked||false,valid:false,pending:false};captureStates.set(form,state);
  control(form,'[data-capture-retry]').addEventListener('click',()=>{if(state.valid)showCapture(form);else loadCapture(form);});
  auto?.addEventListener('change',()=>{state.autoWanted=auto.checked;showCapture(form);});
  form.elements.namedItem('pipeline_mode')?.addEventListener('change',()=>scheduleCapture(form));
  form.addEventListener('capture-settings',()=>scheduleCapture(form));
  form.addEventListener('submit',event=>{
    if(!state.valid || state.pending){event.preventDefault();control(form,'[data-capture-status]').textContent='รอพรีวิวอัปเดตและตรวจช่วงเวลาสักครู่ แล้วกดเริ่มอีกครั้ง';}
  });
  for(const key of captureKeys)form.elements.namedItem(key)?.addEventListener('input',()=>scheduleCapture(form));
  for(const key of ['start_seconds','end_seconds'])form.elements.namedItem(key)?.addEventListener('change',()=>scheduleCapture(form));
  for(const end of ['start','end']) {
    const slider=control(form,`[data-clip-${end}]`), number=control(form,`[data-clip-${end}-number]`);
    slider.addEventListener('input',()=>{
      const duration=state.result?.profile.duration_seconds || Number(slider.max);
      const gap=1/(state.result?.profile.fps || 30);
      const first=Number(control(form,'[data-clip-start-number]').value || 0);
      const last=Number(control(form,'[data-clip-end-number]').value) || duration;
      const selected=end==='start' ? Math.max(0,Math.min(Number(slider.value),last-gap))
        : Math.min(duration,Math.max(Number(slider.value),first+gap));
      slider.value=selected;number.value=Number(selected.toFixed(6));scheduleCapture(form);
    });
  }
  for(const selector of ['[data-capture-position]','[data-capture-yaw]']) {
    control(form,selector).addEventListener('input',()=>{
      clearTimeout(state.imageTimer);state.imageTimer=setTimeout(()=>{if(state.valid)showCapture(form);},90);
    });
  }
  const video=control(form,'[data-clip-video]');
  const syncVideo=()=>{
    if(!state.valid || !state.result.profile.fps)return;
    const plan=state.result.plan;
    const ordinal=Math.max(0,Math.min((plan.positions || 1)-1,
      Math.round((video.currentTime*state.result.profile.fps-plan.start_frame)/plan.effective_sample_every)));
    const position=control(form,'[data-capture-position]');
    if(Number(position.value)===ordinal)return;
    position.value=ordinal;
    clearTimeout(state.imageTimer);state.imageTimer=setTimeout(()=>{if(state.valid)showCapture(form);},90);
  };
  video.addEventListener('seeked',syncVideo);
  video.addEventListener('play',()=>{
    const plan=state.result?.plan;
    if(plan && (video.currentTime<plan.start_seconds || video.currentTime>=plan.end_seconds))
      video.currentTime=plan.start_seconds;
  });
  video.addEventListener('timeupdate',()=>{
    if(!state.result || video.paused)return;
    const plan=state.result.plan;
    if(video.currentTime<plan.start_seconds) video.currentTime=plan.start_seconds;
    if(plan.end_seconds && video.currentTime>=plan.end_seconds)video.pause();
    if(!state.lastVideoPreview || Date.now()-state.lastVideoPreview>2000 || video.paused){state.lastVideoPreview=Date.now();syncVideo();}
  });
  for(const end of ['start','end']) control(form,`[data-set-${end}]`).addEventListener('click',()=>{
    control(form,`[data-clip-${end}-number]`).value=Number(video.currentTime.toFixed(2));scheduleCapture(form);
  });
  control(form,'[data-reset-clip]').addEventListener('click',()=>{
    control(form,'[data-clip-start-number]').value=0;control(form,'[data-clip-end-number]').value=0;
    control(form,'[data-capture-position]').value=0;scheduleCapture(form);
  });
  form.closest('dialog')?.addEventListener('close',()=>{
    video.pause();++state.sequence;state.pending=false;state.valid=false;state.abort?.abort();cancelImages(state);
    clearTimeout(state.timer);clearTimeout(state.imageTimer);
    for(const url of state.urls.values())URL.revokeObjectURL(url);state.urls.clear();
  });
}

document.addEventListener('click',event=>{
  const button=event.target.closest('[data-modal]');if(!button || button.disabled)return;
  const form=document.getElementById(button.dataset.modal)?.querySelector('form[data-settings-source]');
  if(!form?.querySelector('[data-capture-preview]'))return;
  initCapture(form);scheduleCapture(form);
});
