import { createViewer } from './viewer.js';

const $ = selector => document.querySelector(selector);
let account = null;
let selected = null;
let viewer = null;
let models = [];
let projects = [];
let selectedProject = null;
let viewerRequest = 0;
const viewerSettingIds = ['movement-speed', 'look-sensitivity', 'zoom-sensitivity'];
const viewerSettingsKey = 'glb-portal-viewer-settings-v1';
const modelCacheName = 'glb-portal-models-v1';
const autoOpenMaxBytes = 64 * 1024 * 1024;
let modelCacheSetup = null;

function ensureModelCache() {
  if (modelCacheSetup) return modelCacheSetup;
  modelCacheSetup = (async () => {
    if (!window.isSecureContext || !('serviceWorker' in navigator) || !('caches' in window)) return false;
    try {
      await navigator.serviceWorker.register('/model-cache-sw.js');
      if (!navigator.serviceWorker.controller) {
        await new Promise(resolve => {
          const changed = () => { clearTimeout(timer); navigator.serviceWorker.removeEventListener('controllerchange', changed); resolve(); };
          const timer = setTimeout(changed, 5000);
          navigator.serviceWorker.addEventListener('controllerchange', changed);
          if (navigator.serviceWorker.controller) changed();
        });
      }
      return !!navigator.serviceWorker.controller;
    } catch (error) {
      console.warn('Model cache unavailable:', error);
      return false;
    }
  })();
  return modelCacheSetup;
}

function readViewerSettings() {
  return {
    movementSpeed: Number($('#movement-speed').value),
    lookSensitivity: Number($('#look-sensitivity').value),
    zoomSensitivity: Number($('#zoom-sensitivity').value)
  };
}

function restoreViewerSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(viewerSettingsKey) || '{}');
    for (const id of viewerSettingIds) {
      const select = $(`#${id}`);
      if ([...select.options].some(option => option.value === saved[id])) select.value = saved[id];
    }
  } catch { /* Settings are optional when browser storage is unavailable. */ }
}

function applyViewerSettings() {
  viewer?.setControls(readViewerSettings());
  try {
    localStorage.setItem(viewerSettingsKey, JSON.stringify(Object.fromEntries(viewerSettingIds.map(id => [id, $(`#${id}`).value]))));
  } catch { /* Continue with the selected values for this page. */ }
}

function updateUrl(modelId = null) {
  const params = new URLSearchParams();
  if (selectedProject) params.set('project', selectedProject.id);
  if (modelId) params.set('model', modelId);
  history.replaceState(null, '', `${location.pathname}${params.size ? `?${params}` : ''}`);
}

async function request(url, options = {}) {
  const response = await fetch(url, { credentials: 'same-origin', ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

async function boot() {
  const me = await request('/api/me');
  if (!me.user) {
    account = null;
    $('#login-screen').hidden = false;
    $('#workspace').hidden = true;
    viewer?.dispose(); viewer = null;
    return;
  }
  account = me;
  $('#login-screen').hidden = true;
  $('#workspace').hidden = false;
  $('#identity').textContent = `${me.user} · ${me.role === 'admin' ? 'ผู้ดูแล' : 'ผู้ชม'}`;
  $('#upload-panel').hidden = me.role !== 'admin';
  $('#show-log').hidden = me.role !== 'admin';
  $('#new-project').hidden = me.role !== 'admin';
  await ensureModelCache();
  await loadProjects();
}

async function loadProjects(preferredId = null, autoSelect = true) {
  projects = (await request('/api/projects')).projects;
  const fromUrl = new URL(location.href).searchParams.get('project');
  selectedProject = projects.find(p => p.id === preferredId) ||
    projects.find(p => p.id === selectedProject?.id) ||
    projects.find(p => p.id === fromUrl) || projects[0];
  const picker = $('#project-select');
  picker.replaceChildren();
  for (const project of projects) {
    const option = document.createElement('option');
    option.value = project.id;
    option.textContent = `${project.name} (${project.model_count})`;
    picker.append(option);
  }
  picker.value = selectedProject.id;
  await loadModels(autoSelect);
}

async function loadModels(autoSelect = true) {
  try {
    models = (await request(`/api/models?project=${encodeURIComponent(selectedProject.id)}`)).models;
    $('#model-count').textContent = models.length;
    $('#list-error').textContent = '';
    renderModels();
    const modelFromUrl = new URL(location.href).searchParams.get('model');
    if (autoSelect) {
      const next = models.find(m => m.id === selected?.id) || models.find(m => m.id === modelFromUrl) || models[0];
      if (next && next.bytes <= autoOpenMaxBytes && next.id !== selected?.id) await selectModel(next);
      if (!next || (next.bytes > autoOpenMaxBytes && !selected)) clearViewer();
    }
  } catch (error) {
    $('#list-error').textContent = error.message;
  }
}

function renderModels() {
  const list = $('#model-list');
  list.replaceChildren();
  for (const model of models) {
    const item = document.createElement('div');
    item.className = `model-item${model.id === selected?.id ? ' active' : ''}`;
    item.role = 'button'; item.tabIndex = 0;
    const title = document.createElement('strong');
    title.textContent = model.name;
    const detail = document.createElement('small');
    detail.textContent = `${model.format.toUpperCase()} · ${formatBytes(model.bytes)} · ${new Date(model.uploaded_at).toLocaleDateString('th-TH')}`;
    const row = document.createElement('span'); row.className = 'row'; row.append(title);
    if (account.role === 'admin') {
      const remove = document.createElement('button');
      remove.className = 'delete-model'; remove.type = 'button'; remove.textContent = '×';
      remove.title = `ลบ ${model.name}`;
      remove.addEventListener('click', async event => {
        event.stopPropagation();
        if (!confirm(`ลบโมเดล “${model.name}” หรือไม่?`)) return;
        try {
          await request(`/api/models/${model.id}`, { method: 'DELETE', headers: { 'X-CSRF-Token': account.csrf } });
          try { await (await caches.open(modelCacheName)).delete(`/api/models/${model.id}/file`); } catch { /* Browser cache is optional. */ }
          if (selected?.id === model.id) clearViewer();
          await loadProjects(selectedProject.id);
        } catch (error) { $('#list-error').textContent = error.message; }
      });
      row.append(remove);
    }
    item.append(row, detail);
    item.addEventListener('click', () => selectModel(model));
    item.addEventListener('keydown', event => { if (event.target === item && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); selectModel(model); } });
    list.append(item);
  }
}

function formatBytes(bytes) { return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`; }

function clearViewer() {
  viewerRequest++;
  viewer?.dispose(); viewer = null; selected = null;
  $('#viewer-title').textContent = 'เลือกโมเดลเพื่อเริ่มชม';
  $('#model-meta').textContent = '';
  $('#empty-state').hidden = false;
  $('#mode-bar').hidden = true;
  $('#viewer-settings').hidden = true;
  $('#viewport-status').hidden = true;
  $('#controls-tip').hidden = true;
  $('#crosshair').hidden = true;
  $('#reset-view').disabled = true;
  $('#download-model').hidden = true;
  $('#render-info').textContent = '';
  $('#cache-info').textContent = '';
  updateUrl();
  renderModels();
}

async function selectModel(model) {
  const requestId = ++viewerRequest;
  viewer?.dispose(); viewer = null;
  selected = model;
  renderModels();
  updateUrl(model.id);
  $('#viewer-title').textContent = model.name;
  $('#model-meta').textContent = `${model.format.toUpperCase()} · ${formatBytes(model.bytes)} · อัปโหลดโดย ${model.uploaded_by}`;
  $('#empty-state').hidden = true;
  $('#download-model').href = `/api/models/${model.id}/file`;
  $('#download-model').download = `${model.name}.${model.format}`;
  $('#download-model').hidden = false;
  $('#mode-bar').hidden = true;
  $('#viewer-settings').hidden = true;
  $('#reset-view').disabled = true;
  $('#viewport-status').hidden = false;
  $('#viewport-status').textContent = 'กำลังโหลดโมเดล…';
  $('#cache-info').textContent = navigator.serviceWorker?.controller ? 'กำลังตรวจ cache…' : 'Cache ไม่พร้อมในเบราว์เซอร์นี้';
  try {
    const instance = await createViewer($('#canvas-host'), `/api/models/${model.id}/file`, {
      format: model.format,
      isCurrent: () => viewerRequest === requestId,
      settings: readViewerSettings(),
      progress: message => { if (selected?.id === model.id) $('#viewport-status').textContent = message; },
      ready: info => {
        if (viewerRequest !== requestId) return;
        $('#viewport-status').hidden = true;
        $('#mode-bar').hidden = false;
        $('#viewer-settings').hidden = false;
        $('#reset-view').disabled = false;
        $('#render-info').textContent = info;
        setMode('orbit');
      },
      mode: mode => { $('#crosshair').hidden = mode === 'orbit'; tip(mode); }
    });
    if (viewerRequest === requestId) viewer = instance;
    else instance.dispose();
  } catch (error) {
    if (viewerRequest === requestId) $('#viewport-status').textContent = `เปิดโมเดลไม่ได้: ${error.message}`;
    console.error(error);
  }
}

function tip(mode) {
  const element = $('#controls-tip');
  element.hidden = false;
  element.textContent = mode === 'orbit' ? 'ลากเพื่อหมุน · Scroll เพื่อซูม · คลิกขวาเพื่อเลื่อน' :
    mode === 'walk' ? 'คลิกภาพเพื่อควบคุมเมาส์ · WASD เดิน · Shift เร็วขึ้น 3 เท่า · Esc ปล่อยเมาส์' :
      'คลิกภาพเพื่อควบคุมเมาส์ · WASD บิน · Q / E ลงขึ้น · Shift เร็วขึ้น 3 เท่า';
}

function setMode(mode) {
  viewer?.setMode(mode);
  document.querySelectorAll('#mode-bar button').forEach(button => button.classList.toggle('active', button.dataset.mode === mode));
  tip(mode);
}

$('#login-form').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('button');
  button.disabled = true;
  $('#login-error').textContent = '';
  try {
    await request('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(Object.fromEntries(new FormData(form))) });
    form.reset();
    await boot();
  } catch (error) { $('#login-error').textContent = error.message; }
  finally { button.disabled = false; }
});

$('#logout').addEventListener('click', async () => {
  try { await request('/api/logout', { method: 'POST', headers: { 'X-CSRF-Token': account.csrf } }); }
  finally { clearViewer(); await boot(); }
});

$('#project-select').addEventListener('change', async event => {
  selectedProject = projects.find(project => project.id === event.target.value);
  viewerRequest++;
  viewer?.dispose(); viewer = null; selected = null;
  updateUrl();
  await loadModels();
});

$('#new-project').addEventListener('click', () => { $('#project-form').hidden = false; $('#project-name').focus(); });
$('#cancel-project').addEventListener('click', () => { $('#project-form').hidden = true; $('#project-form').reset(); });
$('#project-form').addEventListener('submit', async event => {
  event.preventDefault();
  try {
    const result = await request('/api/projects', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': account.csrf }, body: JSON.stringify({ name: $('#project-name').value }) });
    $('#project-form').hidden = true; $('#project-form').reset();
    viewerRequest++;
    viewer?.dispose(); viewer = null; selected = null;
    await loadProjects(result.id);
    updateUrl();
  } catch (error) { $('#list-error').textContent = error.message; }
});

$('#model-file').addEventListener('change', async event => {
  const file = event.target.files[0];
  if (!file) return;
  const progress = $('#upload-progress');
  const result = $('#upload-result');
  result.hidden = true;
  result.classList.remove('error');
  progress.hidden = false;
  $('#upload-label').textContent = `กำลังอัปโหลด ${file.name}`;
  $('#upload-bar').value = 0;
  try {
    if (!/\.(glb|spz)$/i.test(file.name)) throw new Error('รองรับไฟล์ .glb และ .spz เท่านั้น');
    const uploaded = await new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/models');
      xhr.setRequestHeader('X-CSRF-Token', account.csrf);
      xhr.setRequestHeader('X-Project-Id', selectedProject.id);
      xhr.setRequestHeader('X-Filename', encodeURIComponent(file.name));
      xhr.upload.onprogress = e => { if (e.lengthComputable) $('#upload-bar').value = e.loaded / e.total * 100; };
      xhr.upload.onload = () => { $('#upload-label').textContent = 'ส่งไฟล์ครบแล้ว กำลังตรวจสอบและบันทึกบนเซิร์ฟเวอร์…'; };
      xhr.onload = () => {
        let response;
        try { response = JSON.parse(xhr.responseText); } catch { /* A proxy can return an HTML error page. */ }
        if (xhr.status === 201 && response?.id) return resolve(response);
        const fallback = xhr.status === 413 ? 'ไฟล์ใหญ่เกินเพดานของเซิร์ฟเวอร์หรือ reverse proxy (HTTP 413)' :
          `อัปโหลดไม่สำเร็จ (HTTP ${xhr.status})`;
        reject(new Error(response?.error || fallback));
      };
      xhr.onerror = () => reject(new Error('การเชื่อมต่อขัดข้อง'));
      xhr.onabort = () => reject(new Error('การอัปโหลดถูกยกเลิก'));
      xhr.send(file);
    });
    clearViewer();
    await loadProjects(selectedProject.id, false);
    const saved = models.find(model => model.id === uploaded.id);
    if (!saved) throw new Error('เซิร์ฟเวอร์รับไฟล์แล้ว แต่ไม่พบใน Project ที่เลือก โปรดตรวจพื้นที่เก็บข้อมูล');
    result.textContent = `บันทึก “${saved.name}” ใน Project “${selectedProject.name}” แล้ว · คลิกโมเดลในรายการเพื่อเปิดดู`;
    result.hidden = false;
    $('#upload-label').textContent = 'บันทึกโมเดลแล้ว';
  } catch (error) {
    result.textContent = error.message;
    result.classList.add('error');
    result.hidden = false;
    $('#upload-label').textContent = error.message;
  }
  finally { event.target.value = ''; setTimeout(() => { progress.hidden = true; }, 3500); }
});

document.querySelectorAll('#mode-bar button').forEach(button => button.addEventListener('click', () => setMode(button.dataset.mode)));
navigator.serviceWorker?.addEventListener('message', event => {
  const { type, modelId, status } = event.data || {};
  if (type !== 'model-cache' || modelId !== selected?.id) return;
  $('#cache-info').textContent = {
    hit: 'ใช้ cache ในเครื่อง', downloading: 'กำลังดาวน์โหลดและเก็บ cache…',
    saved: 'เก็บ cache ในเครื่องแล้ว', unavailable: 'พื้นที่ cache ไม่พอ · ครั้งหน้าอาจต้องโหลดใหม่'
  }[status] || '';
});
$('#clear-model-cache').addEventListener('click', async () => {
  try {
    await caches.delete(modelCacheName);
    $('#cache-info').textContent = 'ล้าง cache โมเดลแล้ว';
  } catch { $('#cache-info').textContent = 'ล้าง cache ไม่สำเร็จ'; }
});
for (const id of viewerSettingIds) $(`#${id}`).addEventListener('change', applyViewerSettings);
for (const [buttonId, direction] of [['speed-down', -1], ['speed-up', 1]]) {
  $(`#${buttonId}`).addEventListener('click', () => {
    const select = $('#movement-speed');
    select.selectedIndex = Math.max(0, Math.min(select.options.length - 1, select.selectedIndex + direction));
    applyViewerSettings();
  });
}
restoreViewerSettings();
$('#reset-view').addEventListener('click', () => viewer?.reset());
$('#show-log').addEventListener('click', async () => {
  try {
    const events = (await request('/api/login-log')).events;
    const body = $('#log-body'); body.replaceChildren();
    for (const item of events) {
      const row = document.createElement('tr');
      for (const value of [item.at.replace('T', ' ').replace('+00:00', ''), item.username, item.ip, item.success ? 'สำเร็จ' : 'ไม่สำเร็จ']) {
        const cell = document.createElement('td'); cell.textContent = value; row.append(cell);
      }
      row.lastChild.className = item.success ? 'ok' : 'bad'; body.append(row);
    }
    $('#log-dialog').showModal();
  } catch (error) { $('#list-error').textContent = error.message; }
});
$('#close-log').addEventListener('click', () => $('#log-dialog').close());

document.documentElement.dataset.portalReady = '1';
boot().catch(error => { $('#login-error').textContent = error.message; });
