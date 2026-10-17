// Keep action feedback visible until the server acknowledges success or failure.
const active = new Map();
const initialControls = new WeakMap();

function controls(form) {
  const dialog = form.closest('dialog');
  return [...new Set([...form.elements, ...(dialog?.querySelectorAll('[data-close],#refine-close') || [])])]
    .filter(control => !control.hasAttribute('data-request-cancel'));
}

function snapshots(form) {
  return controls(form).map(control => ({control, disabled:control.disabled,
    text:control.tagName === 'BUTTON' ? control.textContent : null}));
}

function restore(saved) {
  for (const item of saved) {
    item.control.disabled = item.disabled;
    if (item.text !== null) item.control.textContent = item.text;
  }
}

function feedback(form) {
  let box = form.querySelector('[data-action-feedback]');
  if (!box) {
    box = document.createElement('section'); box.className = 'action-feedback';
    box.setAttribute('data-action-feedback', ''); box.setAttribute('aria-live', 'polite');
    const title = document.createElement('strong'); title.setAttribute('data-action-title', '');
    const detail = document.createElement('p'); detail.setAttribute('data-action-detail', '');
    const progress = document.createElement('progress'); progress.max = 100;
    progress.setAttribute('data-action-progress', '');
    const cancel = document.createElement('button'); cancel.type = 'button';
    cancel.textContent = 'ยกเลิกการอัปโหลด'; cancel.setAttribute('data-request-cancel', '');
    box.append(title, detail, progress, cancel); form.append(box);
  }
  box.hidden = false; box.setAttribute('role', 'status'); box.dataset.state = 'working';
  return {box, title:box.querySelector('[data-action-title]'), detail:box.querySelector('[data-action-detail]'),
    progress:box.querySelector('[data-action-progress]'), cancel:box.querySelector('[data-request-cancel]')};
}

function size(value) {
  if (value >= 1024**3) return (value/1024**3).toFixed(2)+' GB';
  if (value >= 1024**2) return (value/1024**2).toFixed(1)+' MB';
  return Math.round(value/1024)+' KB';
}

function actionLabel(action) {
  return ({generate:'กำลังสร้าง job ใหม่', reprocess_job:'กำลังเริ่มสร้าง Views ใหม่',
    generate3d:'กำลังเริ่มสร้างโมเดล', estimate_poses:'กำลังเริ่มหา Camera Pose',
    refine_model:'กำลังเริ่มปรับละเอียดโมเดล', compress_model:'กำลังเริ่มบีบอัดโมเดล', export_glb:'กำลังเข้าคิวส่งออก GLB',
    rename_source:'กำลังเปลี่ยนชื่อ source', rename_model:'กำลังเปลี่ยนชื่อโมเดล',
    update_job:'กำลังบันทึกชื่อและการตั้งค่า', delete_source:'กำลังลบ source',
    delete_job:'กำลังลบ job', delete_model:'กำลังลบโมเดล'})[action] || 'กำลังดำเนินการ';
}

document.addEventListener('submit', event => {
  const form = event.target;
  if (form.tagName !== 'FORM') return;
  if (active.has(form)) { event.preventDefault(); return; }
  initialControls.set(form, snapshots(form));
}, true);

document.addEventListener('submit', event => {
  const form = event.target;
  if (form.tagName !== 'FORM' || form.method.toLowerCase() !== 'post') return;
  if (new URL(form.action, location.href).pathname !== '/actions') return;
  const saved = initialControls.get(form) || snapshots(form);
  if (event.defaultPrevented) { if (!active.has(form)) restore(saved); return; }
  if (active.has(form)) { event.preventDefault(); return; }
  if (typeof XMLHttpRequest === 'undefined') return;
  event.preventDefault();

  // Capture before disabling inputs, including the clicked button outside the form.
  restore(saved);
  const payload = new FormData(form);
  if (event.submitter?.name) payload.set(event.submitter.name, event.submitter.value);
  const action = payload.get('action');
  const uploading = action === 'upload_source' || action === 'upload_model';
  const display = feedback(form);
  const request = new XMLHttpRequest();
  const state = {request, display, saved, uploading, cancellable:uploading, finished:false};
  active.set(form, state); form.setAttribute('data-action-busy', ''); form.setAttribute('aria-busy', 'true');
  for (const item of saved) {
    item.control.disabled = true;
    if (item.control.tagName === 'BUTTON' && item.control.type === 'submit') item.control.textContent = 'กำลังดำเนินการ…';
  }
  display.title.textContent = uploading ? 'กำลังอัปโหลด' : actionLabel(action);
  display.detail.textContent = uploading ? 'กำลังส่งไฟล์ กรุณาเปิดหน้านี้ไว้' : 'รอผลจากระบบสักครู่';
  display.progress.hidden = false; display.progress.removeAttribute('value');
  display.box.scrollIntoView?.({block:'nearest', behavior:'smooth'});
  display.cancel.hidden = !uploading; display.cancel.disabled = false;
  display.cancel.onclick = () => { if (state.cancellable) request.abort(); };

  function finish(kind, title, detail) {
    if (state.finished) return;
    state.finished = true; active.delete(form);
    form.removeAttribute('data-action-busy'); form.removeAttribute('aria-busy');
    restore(saved); display.box.dataset.state = kind;
    display.box.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    display.title.textContent = title; display.detail.textContent = detail;
    display.progress.hidden = true; display.cancel.hidden = true;
    display.box.scrollIntoView?.({block:'nearest', behavior:'smooth'});
  }
  if (uploading) {
    request.upload.addEventListener('progress', event => {
      if (state.finished) return;
      if (!event.lengthComputable) return;
      const percent = Math.min(100, event.loaded / event.total * 100);
      display.progress.value = percent;
      display.detail.textContent = `${percent.toFixed(1)}% · ${size(event.loaded)} / ${size(event.total)}`;
      if (event.loaded >= event.total) { state.cancellable = false; display.cancel.disabled = true; }
    });
    request.upload.addEventListener('load', () => {
      if (state.finished) return;
      state.cancellable = false; display.cancel.disabled = true;
      display.title.textContent = 'ส่งไฟล์ครบแล้ว กำลังตรวจและบันทึก';
      display.detail.textContent = 'กำลังเพิ่มไฟล์เข้ารายการ กรุณารอผลยืนยัน';
      display.progress.removeAttribute('value');
    });
  }
  request.addEventListener('load', () => {
    if (state.finished) return;
    const result = request.response;
    if (request.status >= 200 && request.status < 300 && result?.ok && result.redirect_to) {
      const destination = new URL(result.redirect_to, location.href);
      if (destination.origin !== location.origin) {
        finish('error', 'เปิดผลลัพธ์ไม่ได้', 'กรุณาตรวจสถานะในหน้ารายการ'); return;
      }
      finish('success', 'สำเร็จ', result.message || 'ดำเนินการเรียบร้อยแล้ว');
      location.assign(destination.href);
    } else finish('error', 'ดำเนินการไม่สำเร็จ', result?.message || 'ไม่ได้รับผลยืนยัน กรุณาตรวจสถานะงานก่อนลองส่งอีกครั้ง');
  });
  request.addEventListener('error', () => finish('error', 'การเชื่อมต่อขาด', 'กรุณาตรวจรายการหรือสถานะงานก่อนลองส่งอีกครั้ง'));
  request.addEventListener('abort', () => finish('cancelled', 'ยกเลิกการอัปโหลดแล้ว', 'ไฟล์ยังอยู่ในช่องเลือก กดอัปโหลดเพื่อลองใหม่ได้'));
  try {
    request.open('POST', form.action);
    request.responseType = 'json';
    request.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
    request.send(payload);
  } catch (error) { finish('error', 'ส่งคำขอไม่สำเร็จ', error.message); }
});

document.addEventListener('cancel', event => {
  if ([...active.keys()].some(form => form.closest('dialog') === event.target)) event.preventDefault();
}, true);
document.addEventListener('click', event => {
  for (const form of active.keys()) {
    const dialog = form.closest('dialog');
    if (dialog && (event.target === dialog || (event.target.closest('dialog') === dialog && event.target.closest('[data-close],#refine-close')))) {
      event.preventDefault(); event.stopImmediatePropagation(); return;
    }
  }
}, true);
addEventListener('beforeunload', event => {
  if (active.size) { event.preventDefault(); event.returnValue = ''; }
});
