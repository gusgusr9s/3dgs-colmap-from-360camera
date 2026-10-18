
function updateResourcePreview(card, status) {
  const view = card.querySelector('[data-preview-kind="views"] img');
  if (view && status.views?.state === 'ready' && status.views.run_id && view.dataset.viewRun !== status.views.run_id) {
    const url = new URL(view.src); url.searchParams.set('v', status.views.run_id);
    view.src = url.href; view.hidden = false; view.dataset.viewRun = status.views.run_id;
  }
  const preview = card.querySelector('[data-run-preview]');
  if (preview && status.state === 'done' && status.model) {
    const target = '/model/' + encodeURIComponent(status.model);
    if (preview.getAttribute('href') !== target) {
      preview.href = target; preview.dataset.previewKind = 'model';
      const image = preview.querySelector('img');
      image.src = '/api/resource-preview?' + new URLSearchParams({kind:'model', key:status.model}); image.hidden = false;
      image.alt = 'Preview: ' + status.model; preview.querySelector('span').textContent = 'Model';
    }
  }
}
document.querySelectorAll('[data-modal]').forEach(button => button.addEventListener('click', () => {
  const dialog = document.getElementById(button.dataset.modal); if (dialog) dialog.showModal();
}));
document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
document.querySelectorAll('dialog').forEach(dialog => dialog.addEventListener('click', event => {
  if (event.target === dialog) dialog.close();
}));
const dateFormat = new Intl.DateTimeFormat('en-GB', {timeZone:'Asia/Bangkok', dateStyle:'short', timeStyle:'medium'});
function formatDuration(value) {
  let seconds = Math.max(0, Math.floor(Number(value) || 0));
  const hours = Math.floor(seconds/3600); seconds %= 3600;
  const minutes = Math.floor(seconds/60); seconds %= 60;
  return [hours ? hours+' ชม.' : '', minutes ? minutes+' นาที' : '', seconds+' วินาที'].filter(Boolean).join(' ');
}
function renderTiming(slot, status, label) {
  if (!slot) return;
  const parts = [];
  if (status.started_at) {
    parts.push(label+' · เริ่ม '+dateFormat.format(new Date(status.started_at))+' (UTC+7)');
    if (status.finished_at) parts.push('เสร็จ '+dateFormat.format(new Date(status.finished_at)));
    let elapsed = Number(status.elapsed_seconds || 0);
    if (status.state === 'running' && !status.finished_at) elapsed = Math.max(elapsed, (Date.now()-Date.parse(status.started_at))/1000);
    parts.push('ใช้เวลา '+formatDuration(elapsed));
    if (status.state === 'running') {
      if (status.estimate_state === 'available' && Number.isFinite(Number(status.eta_seconds)) && status.estimated_finish_at) {
        parts.push('เหลือประมาณ '+formatDuration(status.eta_seconds));
        parts.push('คาดว่าเสร็จ '+dateFormat.format(new Date(status.estimated_finish_at))+' (UTC+7)');
      } else if (status.estimate_state === 'finishing') parts.push('กำลังสรุปผล / ส่งออก');
      else if (status.estimate_state === 'stale') parts.push('รอความคืบหน้าใหม่เพื่อปรับเวลาคาดการณ์');
      else parts.push('กำลังเก็บข้อมูลเพื่อประเมินเวลาที่เหลือ');
    }
  } else if (status.recorded_at) {
    parts.push(label+' · บันทึกไฟล์ '+dateFormat.format(new Date(status.recorded_at))+' (UTC+7) · ไม่มีระยะเวลาในงานเดิม');
  }
  if(status.state==='running' && (status.last_activity_at||status.updated_at)) {
    const at=status.last_activity_at||status.updated_at;
    parts.push('Log / สถานะล่าสุด '+dateFormat.format(new Date(at)));
    const quiet=Math.floor((Date.now()-Date.parse(at))/1000);
    if(quiet>=60)parts.push('ยังไม่มีข้อมูลใหม่ '+formatDuration(quiet));
  }
  setLiveText(slot, parts.join(' · '));
}
function setLiveText(node, value) {
  if (node.textContent !== value) node.textContent = value;
}
const liveStatusNodes = new WeakMap();
function renderStatus(slot, status) {
  if (!slot) return;
  const state = status.state || 'waiting';
  const step = status.step || state;
  const progress = Math.max(0, Math.min(100, Number(status.progress) || 0));
  const structure = JSON.stringify([state, status.error || '', status.job, status.can_stop, status.stop_requested, status.stop_token]);
  let nodes = liveStatusNodes.get(slot);
  if (!nodes || nodes.structure !== structure) {
    slot.replaceChildren();
    const badge = document.createElement('span');
    badge.className = 'badge ' + (state === 'done' || state === 'ready' ? 'good' : state === 'error' ? 'bad' : state === 'running' ? 'running' : 'warn');
    const dot = document.createElement('i'); dot.className = 'dot';
    const text = document.createElement('span'); badge.append(dot, text); slot.append(badge);
    const fill = document.createElement('i');
    if (state === 'running') { const bar=document.createElement('div'); bar.className='progress'; bar.append(fill); slot.append(bar); }
    const percentLabel=document.createElement('div');percentLabel.className='task-progress-label';slot.append(percentLabel);
    if (state === 'error' && status.error) {
      const detail=document.createElement('div');detail.className='job-error';
      const title=document.createElement('strong');const first=status.error.split('\n')[0];
      title.textContent=first.startsWith('Command ') ? 'ขั้นตอนนี้ไม่สำเร็จ เปิดรายละเอียดหรือ Log เพื่อตรวจสาเหตุ' : first;
      const more=document.createElement('details'),summary=document.createElement('summary'),trace=document.createElement('pre');
      summary.textContent='รายละเอียดข้อผิดพลาด';trace.textContent=status.error;trace.style.whiteSpace='pre-wrap';trace.style.overflowWrap='anywhere';
      more.append(summary,trace);detail.append(title,more);slot.append(detail);
    }
    const device = document.createElement('div'); device.className = 'resource-meta'; slot.append(device);
    const preparer = document.createElement('div'); preparer.className = 'resource-meta'; slot.append(preparer);
    const timing = document.createElement('div'); timing.className = 'resource-meta task-timing'; slot.append(timing);
    if (state === 'queued' && status.job) {
      const form = document.createElement('form'); form.method='post'; form.action='/actions'; form.dataset.working='';
      for (const [name,value] of [['action','cancel_queued'],['job',status.job],['return_to','/job/'+encodeURIComponent(status.job)]]) {
        const input=document.createElement('input');input.type='hidden';input.name=name;input.value=value;form.append(input);
      }
      const button=document.createElement('button');button.type='submit';button.textContent='ยกเลิกรอคิว';form.append(button);slot.append(form);
    }
    if (state === 'running' && status.can_stop && status.job) {
      const form=document.createElement('form');form.method='post';form.action='/actions';form.dataset.working='';
      for (const [name,value] of [['action','stop_task'],['job',status.job],['token',status.stop_token]]) {
        const input=document.createElement('input');input.type='hidden';input.name=name;input.value=value;form.append(input);
      }
      const button=document.createElement('button');button.type='submit';button.disabled=!!status.stop_requested;
      button.textContent=status.stop_requested?'กำลังหยุด…':'หยุดและเก็บงานไว้';form.append(button);slot.append(form);
      const note=document.createElement('small');note.textContent='COLMAP เซฟ pose ก่อนออก · Train กู้จาก checkpoint ล่าสุด (ทุก 5,000 steps)';slot.append(note);
    }
    if (state === 'stopped' && status.job) {
      const link=document.createElement('a');link.href='/job/'+encodeURIComponent(status.job);link.textContent='เปิดตัวเลือกทำต่อ';slot.append(link);
    }
    nodes = {structure, text, fill, percentLabel, device, preparer, timing}; liveStatusNodes.set(slot, nodes);
  }
  setLiveText(nodes.text, ' ' + step);
  updatePhaseProgress(slot,status,nodes);
  nodes.device.hidden = !status.quality?.compute_device;
  if (!nodes.device.hidden) {
    const label = status.quality.compute_device_type === 'Cpu' ? 'CPU' : 'GPU';
    setLiveText(nodes.device, label + ': ' + status.quality.compute_device + ' · ' + status.quality.compute_backend);
  }
  const preparer = status.view_compute || status.quality?.mask_compute;
  nodes.preparer.hidden = !preparer;
  if (preparer) {
    setLiveText(nodes.preparer, preparer.backend + ': ' + preparer.device +
      (preparer.cpu_runtime?' · CPU '+preparer.cpu_runtime.mode+' · '+preparer.cpu_runtime.threads+'/'+preparer.cpu_runtime.available+' threads':'')+
      (preparer.fallback_reasons?.length ? ' · ' + preparer.fallback_reasons.join(' · ') : ''));
  }
  renderTiming(nodes.timing, status, status.stage === 'views' ? 'สร้าง Views' : status.stage === 'poses' ? 'หา Camera Pose' : 'สร้าง Model');
}
function updatePhaseProgress(slot,status,nodes=liveStatusNodes.get(slot)) {
  const fill=nodes?.fill||slot?.querySelector('.progress > i');
  const label=nodes?.percentLabel||slot?.querySelector('.task-progress-label');
  const measurable=status.phase_percent!==null && status.phase_percent!==undefined;
  const value=measurable?Math.max(0,Math.min(100,Number(status.phase_percent)||0)):0;
  if(fill){fill.style.width=value+'%';fill.parentElement?.classList.toggle('indeterminate',!measurable);}
  if(label)setLiveText(label,status.progress_label||('ภาพรวมโดยประมาณ '+Math.round(Number(status.progress)||0)+'%'));
}
let refreshingJobs = false;
function announcePageUpdate() {
  if (document.getElementById('live-update-notice')) return;
  const notice = document.createElement('aside'); notice.id = 'live-update-notice'; notice.className = 'live-update-notice';
  notice.setAttribute('role','status');
  const text=document.createElement('span'); text.textContent='มีข้อมูลหรือผลลัพธ์ใหม่';
  const button=document.createElement('button'); button.type='button';button.textContent='โหลดรายการล่าสุด';
  button.addEventListener('click',()=>{if (!document.querySelector('form[data-action-busy]')) window.location.reload();});
  notice.append(text,button);document.body.append(notice);
}
const pendingJobUpdates = new Map();
function interactionActive(card) {
  return Boolean(document.querySelector('dialog[open],form[data-action-busy]') ||
    document.querySelector('[data-job]:hover,[data-job]:focus-within,.resource-menu[open]') ||
    card.matches?.(':hover,:focus-within') || card.querySelector('details[open]') ||
    window.getSelection?.()?.toString());
}
function applyJobStatus(card, status) {
  if (interactionActive(card)) {
    pendingJobUpdates.set(card,status);
    const slot=card.querySelector('.status-slot');
    if(status.state==='running')updatePhaseProgress(slot,status);
    return;
  }
  pendingJobUpdates.delete(card);
  renderStatus(card.querySelector('.status-slot'), {...status,job:card.dataset.job});
  const phase=card.querySelector('[data-experiment-state]');
  if(phase) {
    setLiveText(phase,({done:'สร้าง Model สำเร็จ',error:'สร้างไม่ได้',queued:'รอทดสอบ',running:'กำลังทดสอบ',ready:'พร้อมใช้งาน'})[status.state] || status.state);
    const css='badge '+(['done','ready'].includes(status.state)?'good':status.state==='error'?'bad':'running');
    if(phase.className!==css)phase.className=css;
  }
  updateResourcePreview(card, status);
  renderTiming(card.querySelector('.views-timing-slot'), status.views || {}, 'สร้าง Views');
  card.querySelectorAll('[data-create-model]').forEach(button => { if (button.disabled !== Boolean(status.busy)) button.disabled=Boolean(status.busy); });
  const artifacts=card.querySelector('.run-artifacts');
  const key=JSON.stringify([status.state,status.model,status.run_id,Boolean(status.quality?.eval_views)]);
  if (artifacts && artifacts.dataset.liveArtifacts !== key) {
    artifacts.dataset.liveArtifacts=key;
    if (status.state==='done' && status.model) {
      artifacts.replaceChildren();
      for (const [prefix,title,download] of [['/model/','เปิด Model',false],['/models/','ดาวน์โหลด PLY',true]]) {
        const link=document.createElement('a');link.className='button';link.href=prefix+encodeURIComponent(status.model);link.textContent=title;
        if(download)link.download='';artifacts.append(link);
      }
      if(status.run_id && status.quality?.eval_views){const link=document.createElement('a');link.className='button';link.href='/quality/'+encodeURIComponent(card.dataset.job)+'/'+encodeURIComponent(status.run_id);link.textContent='ตรวจภาพ Render';artifacts.append(link);}
    } else if(status.state==='deleted') artifacts.replaceChildren();
  }
}
function flushJobUpdates() {
  for (const [card,status] of pendingJobUpdates) applyJobStatus(card,status);
}
async function statusJSON(endpoint) {
  const controller=typeof AbortController==='undefined' ? null : new AbortController();
  const timer=controller ? setTimeout(()=>controller.abort(),10000) : null;
  try {
    const response=await fetch(endpoint,{cache:'no-store',signal:controller?.signal});
    if (!response.ok) throw new Error('Status unavailable');
    return await response.json();
  } finally { if(timer!==null)clearTimeout(timer); }
}
async function refreshJobs(force = true) {
  if (refreshingJobs || document.hidden) return;
  refreshingJobs = true;
  try {
  for (const card of document.querySelectorAll('[data-job]')) {
    if (!force && Number(card.dataset.nextPollAt || 0) > Date.now()) continue;
    try {
      const endpoint = card.dataset.modelRun
        ? '/api/run/'+encodeURIComponent(card.dataset.job)+'/'+encodeURIComponent(card.dataset.modelRun)
        : '/api/job/'+encodeURIComponent(card.dataset.job);
      const status = await statusJSON(endpoint);
      card.dataset.nextPollAt = Date.now() + (status.busy || ['running','queued'].includes(status.state) ? 2500 : 30000);
      applyJobStatus(card,status);
      if (card.dataset.jobDetail && status.views?.state==='ready') {
        const count=Number(status.views.frames ?? card.dataset.viewCount);
        if (Number(card.dataset.viewCount)!==count || (status.views.run_id && status.views.run_id!==card.dataset.viewRun)) {
          announcePageUpdate();
        }
      }
      if (card.dataset.jobDetail && status.stage==='model' && status.run_id && status.run_id!==card.dataset.knownModelRun) {
        announcePageUpdate();
      }
      if (card.dataset.sourceHub && status.state==='done' && status.model && status.model!==card.dataset.knownModel) {
        announcePageUpdate();
      }
      if (card.dataset.refreshOnComplete && ['done','error','ready'].includes(status.state)) announcePageUpdate();
    }
    catch (error) { console.warn(error); }
  }
  } finally { refreshingJobs = false; }
}
if (document.querySelector('[data-job]')) {
  for(const card of document.querySelectorAll('[data-job]')) {
    card.addEventListener('pointerleave',flushJobUpdates);
    card.addEventListener('focusout',()=>setTimeout(flushJobUpdates,0));
    card.addEventListener('toggle',()=>setTimeout(flushJobUpdates,0),true);
  }
  document.addEventListener('close',()=>setTimeout(flushJobUpdates,0),true);
  document.addEventListener('selectionchange',flushJobUpdates);
  refreshJobs(); setInterval(() => refreshJobs(false), 2500);
  document.addEventListener?.('visibilitychange', () => { if (!document.hidden) refreshJobs(); });
}
for (const badge of document.querySelectorAll('[data-compression-watch]')) {
  let polling=false;
  setInterval(async()=>{
    if(polling || document.hidden || !['queued','running'].includes(badge.dataset.knownState))return;
    polling=true;
    try {
      const status=await statusJSON('/api/compression/'+encodeURIComponent(badge.dataset.compressionWatch));
      if(status.state!==badge.dataset.knownState) {
        if(['ready','error','deleted'].includes(status.state))announcePageUpdate();
        if(!interactionActive(badge.closest('.panel'))) {
          badge.dataset.knownState=status.state;
          badge.className='badge '+(status.state==='ready'?'good':status.state==='error'?'bad':'warn');
          setLiveText(badge,({queued:'รอบีบอัด',running:'กำลังบีบอัด',ready:'พร้อมใช้งาน',error:'บีบอัดไม่สำเร็จ',deleted:'ไฟล์ถูกลบแล้ว'})[status.state] || status.state);
        }
      }
    } catch(error) {console.warn(error);} finally {polling=false;}
  },3000);
}
for (const button of document.querySelectorAll('[data-refresh-page]')) button.addEventListener('click',()=>{
  if(!document.querySelector('form[data-action-busy]'))window.location.reload();
});
