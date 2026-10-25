import { setupWorkspaceTools } from './workspace_tools.js';

const assetVersion = new URL(import.meta.url).searchParams.get('v') || '';
const viewerUrl = new URL('./viewer.js', import.meta.url);
if (assetVersion) viewerUrl.searchParams.set('v', assetVersion);
// Keep login and the library usable while the 3D modules are still downloading.
let viewerModule;
async function createViewer(...args) {
  viewerModule ||= import(viewerUrl.href);
  return (await viewerModule).createViewer(...args);
}

const $ = selector => document.querySelector(selector);
const iconPaths = {
  arrow:'M5 12h14m-6-6 6 6-6 6', plus:'M12 5v14M5 12h14', close:'m6 6 12 12M18 6 6 18',
  library:'M3 5h18v14H3zM3 10h18M9 10v9', search:'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16m6-2 5 5',
  upload:'M12 16V3m-5 5 5-5 5 5M4 15v6h16v-6', download:'M12 3v13m-5-5 5 5 5-5M4 16v5h16v-5',
  user:'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8M4 21v-2a8 6 0 0 1 16 0v2', chevron:'m6 9 6 6 6-6',
  history:'M3 11a9 9 0 1 1 2 7M3 4v7h7m2-4v5l3 2', logout:'M9 4H4v16h5m5-13 5 5-5 5M8 12h12',
  lock:'M6 10h12v11H6zm3 0V6a3 3 0 0 1 6 0v4', more:'M5 12h.01M12 12h.01M19 12h.01',
  refresh:'M20 9a8 8 0 1 0-1 9m1-15v6h-6', reset:'M3 11a9 9 0 1 1 3 8M3 4v7h7',
  settings:'M4 7h16M4 17h16M8 4v6m8 4v6', edit:'m4 16 12-12 4 4L8 20H4zm9-9 4 4',
  trash:'M3 6h18M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7m4-7v7',
  focus:'M8 3H3v5m13-5h5v5M3 16v5h5m8 0h5v-5M12 8v8m-4-4h8',
  link:'m10 14 4-4m-6 6-2 2a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m2 0 2-2a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0',
  orbit:'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18M3 12h18M12 3c-5 5-5 13 0 18 5-5 5-13 0-18',
  walk:'M13 5h.01M9 22l3-7m4 7-3-8-1-6-4 4H5m7-4 4 4h4',
  fly:'m3 10 18-7-7 18-3-8-8-3zm8 3 10-10', expand:'M8 3H3v5m13-5h5v5M3 16v5h5m8 0h5v-5',
  cube:'m12 2 9 5v10l-9 5-9-5V7zm0 0v10m-9-5 9 5 9-5m-9 5v10',
  select:'M4 4h16v16H4zm3 8 3 3 7-7',pin:'M12 22s8-7 8-13a8 8 0 0 0-16 0c0 6 8 13 8 13zm0-10a3 3 0 1 0 0-6 3 3 0 0 0 0 6',
  image:'M3 3h18v18H3zm0 14 6-6 4 4 3-3 5 5M8 8h.01'
};
function icon(name) { return `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${iconPaths[name] || iconPaths.cube}"/></svg>`; }
document.querySelectorAll('[data-icon]').forEach(element => { element.innerHTML = icon(element.dataset.icon); });
function closeMenus() { document.querySelectorAll('.menu[open]').forEach(menu => { menu.open = false; }); }
function openDialog(id) { closeMenus(); document.exitPointerLock?.(); $(id).showModal(); }
document.querySelectorAll('[data-close-dialog]').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
document.addEventListener('click', event => {
  document.querySelectorAll('.menu[open]').forEach(menu => { if (!menu.contains(event.target) || event.target.closest('.menu-content button,.menu-content a')) menu.open = false; });
});
document.querySelectorAll('.menu').forEach(menu => menu.addEventListener('toggle', () => {
  if (menu.open) document.querySelectorAll('.menu[open]').forEach(other => { if (other !== menu) other.open = false; });
}));
function setLibrary(open) {
  const mobile = matchMedia('(max-width:760px)').matches;
  $('#library').classList.toggle('open', open && mobile);
  $('#library-backdrop').hidden = !open || !mobile;
  $('#library-toggle').setAttribute('aria-expanded', String(open && mobile));
  $('.viewer-panel').inert = open && mobile;
  $('.topbar').inert = open && mobile;
  if (open && mobile) { $('#library').setAttribute('role', 'dialog'); $('#library').setAttribute('aria-modal', 'true'); }
  else { $('#library').removeAttribute('role'); $('#library').removeAttribute('aria-modal'); }
  if (mobile && open) $('#library-close').focus();
  if (mobile && !open && $('#library').contains(document.activeElement)) $('#library-toggle').focus();
}
$('#library-toggle').addEventListener('click', () => setLibrary(!$('#library').classList.contains('open')));
for (const id of ['#library-close', '#library-backdrop']) $(id).addEventListener('click', () => setLibrary(false));
$('#empty-library').addEventListener('click', () => setLibrary(true));
matchMedia('(max-width:760px)').addEventListener('change', () => setLibrary(false));
document.addEventListener('keydown', event => {
  if (event.key === 'Tab' && $('#library').classList.contains('open') && !document.querySelector('dialog[open]')) {
    const items = [...$('#library').querySelectorAll('button,input,select,a')].filter(item => !item.disabled && item.getClientRects().length);
    const first = items[0], last = items.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
  if (event.key !== 'Escape' || document.querySelector('dialog[open]')) return;
  const menu = $('.menu[open]');
  if (menu) { menu.open = false; menu.querySelector('summary').focus(); event.preventDefault(); }
  else setLibrary(false);
});
$('#settings-open').addEventListener('click', () => openDialog('#settings-dialog'));
$('#fullscreen-view').addEventListener('click', async () => {
  try { if (document.fullscreenElement) await document.exitFullscreen(); else await $('#viewport').requestFullscreen(); }
  catch { $('#portal-notice').textContent = 'เบราว์เซอร์นี้ไม่สามารถเปิดเต็มจอได้'; }
});
let account = null;
let selected = null;
let viewer = null;
let selectedDetails = null;
let models = [];
let projects = [];
let selectedProject = null;
let viewerRequest = 0;
let renameTarget = null;
let modelListRequest = 0;
const viewerSettingIds = ['movement-speed', 'look-sensitivity', 'zoom-sensitivity', 'render-quality'];
const viewerSettingsKey = 'glb-portal-viewer-settings-v1';
const viewerSettingsVersion = 2;
const modelOrientationKey = 'glb-portal-model-orientation-v1';
const modelCacheName = 'glb-portal-models-v1';
const autoOpenMaxBytes = 64 * 1024 * 1024;
let modelCacheSetup = null;
let moveStickPointerId = null;
const moveStickKeyboard = new Set();
const workspaceTools = setupWorkspaceTools({
  state: () => ({account,selected,viewer,models,projects,selectedProject,details:selectedDetails}),
  request,openDialog,clearViewer,renderModels,icon,
  refresh: id => loadProjects(id,false),notice: message => { $('#portal-notice').textContent=message; }
});

function ensureModelCache() {
  if (modelCacheSetup) return modelCacheSetup;
  modelCacheSetup = (async () => {
    if (!window.isSecureContext || !('serviceWorker' in navigator) || !('caches' in window)) return false;
    try {
      const workerUrl = new URL('/model-cache-sw.js', location.origin);
      if (assetVersion) workerUrl.searchParams.set('v', assetVersion);
      await navigator.serviceWorker.register(workerUrl.href);
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
    zoomSensitivity: Number($('#zoom-sensitivity').value),
    renderQuality: Number($('#render-quality').value)
  };
}

function restoreViewerSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(viewerSettingsKey) || '{}');
    if (saved.version !== viewerSettingsVersion && ['0.15', '0.35'].includes(saved['zoom-sensitivity'])) saved['zoom-sensitivity'] = '1';
    for (const id of viewerSettingIds) {
      const select = $(`#${id}`);
      if ([...select.options].some(option => option.value === saved[id])) select.value = saved[id];
    }
    saved.version = viewerSettingsVersion;
    localStorage.setItem(viewerSettingsKey, JSON.stringify(saved));
  } catch { /* Settings are optional when browser storage is unavailable. */ }
}

function modelIsFlipped(modelId) {
  try { return JSON.parse(localStorage.getItem(modelOrientationKey) || '{}')[modelId] === true; }
  catch { return false; }
}

function saveModelOrientation(modelId, flipped) {
  try {
    const saved = JSON.parse(localStorage.getItem(modelOrientationKey) || '{}');
    saved[modelId] = flipped;
    localStorage.setItem(modelOrientationKey, JSON.stringify(saved));
  } catch { /* Orientation can still be changed for the current view. */ }
}

function applyViewerSettings() {
  viewer?.setControls(readViewerSettings());
  try {
    localStorage.setItem(viewerSettingsKey, JSON.stringify({ version: viewerSettingsVersion, ...Object.fromEntries(viewerSettingIds.map(id => [id, $(`#${id}`).value])) }));
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
    modelListRequest++;
    models = []; selectedProject = null; selected = null;
    document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close());
    setLibrary(false);
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
  $('#upload-open').hidden = me.role !== 'admin';
  $('#upload-limit').textContent = `สูงสุด ${formatBytes(me.max_upload_bytes)} ต่อไฟล์`;
  $('#show-log').hidden = me.role !== 'admin';
  $('#new-project').hidden = me.role !== 'admin';
  if (!selected) $('#model-menu').hidden = true;
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
  const requestId = ++modelListRequest;
  const projectId = selectedProject.id;
  try {
    const result = await request(`/api/models?project=${encodeURIComponent(projectId)}`);
    if (requestId !== modelListRequest || selectedProject.id !== projectId) return;
    models = result.models;
    $('#list-error').textContent = '';
    renderModels();
    const modelFromUrl = new URL(location.href).searchParams.get('model');
    if (selected && !models.some(model => model.id === selected.id)) clearViewer();
    if (autoSelect) {
      const next = models.find(m => m.id === selected?.id) || models.find(m => m.id === modelFromUrl) || models[0];
      if (next && next.bytes <= autoOpenMaxBytes && next.id !== selected?.id) await selectModel(next);
      if (!next || (next.bytes > autoOpenMaxBytes && !selected)) clearViewer();
    }
  } catch (error) {
    if (requestId === modelListRequest) $('#list-error').textContent = error.message;
  }
}

function renderModels() {
  const list = $('#model-list');
  list.replaceChildren();
  const query = $('#model-search').value.trim().toLocaleLowerCase();
  const format = $('#format-filter').value;
  const visible = models.filter(model => (!query || model.name.toLocaleLowerCase().includes(query)) && (format === 'all' || model.format === format));
  const order = $('#model-sort').value;
  visible.sort((a,b) => order === 'name' ? a.name.localeCompare(b.name, 'th') : order === 'size' ? b.bytes-a.bytes : b.uploaded_at.localeCompare(a.uploaded_at) || b.id.localeCompare(a.id));
  workspaceTools.update(visible);
  $('#model-count').textContent = visible.length;
  $('#list-summary').textContent = query || format !== 'all' ? `พบ ${visible.length} จาก ${models.length} โมเดล` : `${models.length} โมเดลใน Project`;
  $('#library-empty').hidden = visible.length !== 0;
  $('#library-empty strong').textContent = models.length ? 'ไม่พบโมเดลที่ค้นหา' : 'ยังไม่มีโมเดลใน Project นี้';
  $('#library-empty p').textContent = models.length ? 'ลองเปลี่ยนคำค้นหรือชนิดไฟล์' : account?.role === 'admin' ? 'เพิ่มไฟล์เพื่อเริ่มเก็บพื้นที่ของคุณ' : 'ผู้ดูแลจะเพิ่มโมเดลให้ Project นี้';
  for (const model of visible) {
    const item = document.createElement('div');
    item.className = `model-item${model.id === selected?.id ? ' active' : ''}`;
    const open = document.createElement('button');
    open.className = 'open-model'; open.type = 'button';
    open.setAttribute('aria-pressed', String(model.id === selected?.id));
    const symbol = document.createElement('span'); symbol.className = 'file-symbol'; symbol.innerHTML = icon('cube');
    const title = document.createElement('strong');
    title.textContent = model.name;
    title.title = model.name;
    const detail = document.createElement('small');
    detail.textContent = `${model.format.toUpperCase()} · ${formatBytes(model.bytes)} · ${new Date(model.uploaded_at).toLocaleDateString('th-TH')}`;
    const text = document.createElement('span'); text.className = 'model-text'; text.append(title, detail);
    if (model.metadata?.points || model.metadata?.vertices || model.metadata?.object_count>1) {
      const stats=document.createElement('small');
      stats.textContent=model.metadata.points?`${model.metadata.points.toLocaleString()} จุด`:model.metadata.vertices?`${model.metadata.vertices.toLocaleString()} จุดยอด`:`${model.metadata.object_count} โมเดลในฉาก`;
      text.append(stats);
    }
    open.append(symbol, text); open.addEventListener('click', () => { setLibrary(false); selectModel(model); });
    item.append(open);
    const row = document.createElement('span'); row.className = 'model-row-actions';
    if (account.role === 'admin') {
      const rename = document.createElement('button');
      rename.className = 'rename-model'; rename.type = 'button'; rename.innerHTML = icon('edit');
      rename.title = `เปลี่ยนชื่อ ${model.name}`;
      rename.setAttribute('aria-label', `เปลี่ยนชื่อ ${model.name}`);
      rename.addEventListener('click', () => openRename(model));
      const remove = document.createElement('button');
      remove.className = 'delete-model'; remove.type = 'button'; remove.innerHTML = icon('trash');
      remove.title = `ลบ ${model.name}`;
      remove.setAttribute('aria-label', `ลบ ${model.name}`);
      remove.addEventListener('click', async event => {
        event.stopPropagation();
        if (!confirm(`ลบโมเดล “${model.name}” หรือไม่?`)) return;
        try {
          await request(`/api/models/${model.id}`, { method: 'DELETE', headers: { 'X-CSRF-Token': account.csrf } });
          try { const cache=await caches.open(modelCacheName);for(const entry of await cache.keys())if(new URL(entry.url).pathname.startsWith(`/api/models/${model.id}/`))await cache.delete(entry); } catch { /* Browser cache is optional. */ }
          if (selected?.id === model.id) clearViewer();
          await loadProjects(selectedProject.id);
        } catch (error) { $('#list-error').textContent = error.message; }
      });
      row.append(rename, remove);
    }
    item.append(row);
    workspaceTools.attach(item,model);
    list.append(item);
  }
}

function formatBytes(bytes) { return bytes >= 1073741824 ? `${(bytes / 1073741824).toFixed(1)} GB` : bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : bytes >= 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${bytes} B`; }

$('#model-search').addEventListener('input', renderModels);
for (const id of ['#format-filter', '#model-sort']) $(id).addEventListener('change', renderModels);
$('#refresh-models').addEventListener('click', () => loadProjects(selectedProject.id, false));

function clearViewer() {
  viewerRequest++;
  releaseVirtualStick();
  viewer?.dispose(); viewer = null; selected = null; selectedDetails=null;
  $('#viewer-title').textContent = 'เลือกพื้นที่ที่อยากสำรวจ';
  $('#edit-selected-name').hidden = true;
  $('#selected-name').value = '';
  $('#model-meta').textContent = '';
  $('#empty-state').hidden = false;
  $('#mode-bar').hidden = true;
  $('#viewer-controls').hidden = true;
  $('#viewport-status').hidden = true;
  $('#controls-tip').hidden = true;
  $('#crosshair').hidden = true;
  $('#touch-controls').hidden = true;
  $('#reset-view').disabled = true;
  $('#center-view').hidden = true;
  $('#center-view').disabled = true;
  $('#download-model').hidden = true;
  $('#copy-link').hidden = true;
  $('#model-menu').hidden = true;
  $('#viewer-orientation').hidden = true;
  $('#viewer-orientation').disabled = true;
  $('#render-info').textContent = '';
  $('#cache-info').textContent = '';
  $('#collision-helpers').disabled=true;$('#collision-helpers').checked=false;
  $('#collision-info').textContent='เลือกโมเดลเพื่อดูข้อมูลตรวจชน';
  updateUrl();
  renderModels();
}

async function selectModel(model) {
  const requestId = ++viewerRequest;
  releaseVirtualStick();
  viewer?.dispose(); viewer = null;
  selectedDetails=null;
  selected = model;
  renderModels();
  updateUrl(model.id);
  $('#viewer-title').textContent = model.name;
  $('#edit-selected-name').hidden = account.role !== 'admin';
  $('#selected-name').value = model.name;
  $('#model-meta').textContent = `${model.format.toUpperCase()} · ${formatBytes(model.bytes)} · อัปโหลดโดย ${model.uploaded_by}`;
  $('#empty-state').hidden = true;
  $('#download-model').href = `/api/models/${model.id}/file`;
  $('#download-model').download = `${model.name}.${model.format}`;
  $('#download-model').hidden = false;
  $('#copy-link').hidden = false;
  $('#model-menu').hidden = false;
  $('#viewer-orientation').hidden = true;
  $('#viewer-orientation').disabled = true;
  $('#viewer-orientation').textContent = modelIsFlipped(model.id) ? 'หมุนกลับ 180°' : 'หมุนโมเดล 180°';
  $('#mode-bar').hidden = true;
  $('#viewer-controls').hidden = true;
  $('#touch-controls').hidden = true;
  $('#reset-view').disabled = true;
  $('#center-view').hidden = true;
  $('#center-view').disabled = true;
  $('#viewport-status').hidden = false;
  $('#viewport-status').textContent = 'กำลังโหลดโมเดล…';
  $('#cache-info').textContent = navigator.serviceWorker?.controller ? 'กำลังตรวจ cache…' : 'Cache ไม่พร้อมในเบราว์เซอร์นี้';
  try {
    const details=await request(`/api/models/${model.id}/details`);
    if(viewerRequest!==requestId)return;
    selectedDetails=details;model.metadata=details.metadata;
    renderModels();
    $('#viewer-orientation').hidden=!['spz','splat'].includes(model.format)&&!(model.format==='ply'&&details.metadata.representation==='gaussian');
    const instance = await createViewer($('#canvas-host'), `/api/models/${model.id}/file`, {
      format: model.format,
      details,
      flipSpz: modelIsFlipped(model.id),
      isCurrent: () => viewerRequest === requestId,
      settings: readViewerSettings(),
      progress: message => { if (viewerRequest === requestId) $('#viewport-status').textContent = message; },
      error: error => {if(viewerRequest===requestId){$('#viewport-status').hidden=false;$('#viewport-status').textContent=`แสดงโมเดลไม่ได้: ${error.message}`;}},
      ready: info => {
        if (viewerRequest !== requestId) return;
        $('#viewport-status').hidden = true;
        $('#mode-bar').hidden = false;
        $('#viewer-controls').hidden = false;
        $('#viewer-orientation').disabled = false;
        $('#reset-view').disabled = false;
        $('#center-view').disabled = false;
        $('#render-info').textContent = info;
        setMode('orbit');
      },
      mode: mode => {
        updateModeUI(mode);
        $('#crosshair').hidden = mode === 'orbit';
        $('#touch-controls').hidden = mode === 'orbit';
        $('#touch-altitude').hidden = mode !== 'free';
        $('#center-view').hidden = mode !== 'orbit';
        if (mode === 'orbit') releaseVirtualStick();
        tip(mode);
      }
    });
    if (viewerRequest === requestId) {viewer = instance;workspaceTools.ready();}
    else instance.dispose();
  } catch (error) {
    if (viewerRequest === requestId) {
      $('#viewport-status').textContent = `เปิดโมเดลไม่ได้: ${error.message}`;
      const retry = document.createElement('button'); retry.className = 'retry-view'; retry.textContent = 'ลองเปิดอีกครั้ง';
      retry.addEventListener('click', () => selectModel(model)); $('#viewport-status').append(retry);
    }
  }
}

function tip(mode) {
  const element = $('#controls-tip');
  const touch = navigator.maxTouchPoints > 0 || matchMedia('(pointer: coarse)').matches;
  element.hidden = false;
  if (touch) {
    element.textContent = mode === 'orbit'
      ? 'ลากเพื่อหมุน · จีบนิ้วเพื่อซูม / เลื่อน'
      : mode === 'walk'
        ? 'ลากเพื่อมอง · ใช้จอยเพื่อเดิน'
        : 'ลากเพื่อมอง · ใช้จอยเพื่อบิน · ปุ่มขึ้น / ลงปรับระดับ';
    return;
  }
  element.textContent = mode === 'orbit'
    ? 'ลากเพื่อหมุน · Scroll เพื่อซูม · คลิกขวาลากเพื่อเลื่อน'
    : mode === 'walk'
      ? 'คลิกภาพเพื่อเริ่ม · WASD เดิน · Shift เร่ง · Esc ปล่อยเมาส์'
      : 'คลิกภาพเพื่อเริ่ม · WASD บิน · Q / E ลง / ขึ้น · Esc ปล่อยเมาส์';
}
function setMode(mode) {
  viewer?.setMode(mode);
  updateModeUI(mode);
}
function updateModeUI(mode) {
  $('#touch-controls').hidden = mode === 'orbit';
  $('#touch-altitude').hidden = mode !== 'free';
  $('#center-view').hidden = mode !== 'orbit';
  document.querySelectorAll('#mode-bar button').forEach(button => { button.classList.toggle('active', button.dataset.mode === mode); button.setAttribute('aria-pressed', String(button.dataset.mode === mode)); });
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
  $('#model-search').value = ''; $('#format-filter').value = 'all';
  models = [];
  workspaceTools.resetSelection();
  clearViewer();
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
    $('#model-search').value = ''; $('#format-filter').value = 'all';
    models = [];
    workspaceTools.resetSelection();
    clearViewer();
    await loadProjects(result.id);
    updateUrl();
  } catch (error) { $('#list-error').textContent = error.message; }
});

$('#model-file').addEventListener('change', event => {
  const files = [...event.target.files];
  event.target.value = '';
  uploadFiles(files);
});

let uploadActive = false;
let stopUploadQueue = false;
function sendUpload(file,endpoint,uploadAccount,targetProject,customName,status,index,total) {
  return new Promise((resolve,reject)=>{
    const xhr=new XMLHttpRequest();xhr.open('POST',endpoint);
    xhr.setRequestHeader('X-CSRF-Token',uploadAccount.csrf);xhr.setRequestHeader('X-Project-Id',targetProject.id);
    xhr.setRequestHeader('X-Filename',encodeURIComponent(file.name));xhr.setRequestHeader('X-Model-Name',encodeURIComponent(customName));
    xhr.timeout=30*60*1000;
    xhr.upload.onprogress=event=>{if(event.lengthComputable)$('#upload-bar').value=event.loaded/event.total*100;};
    xhr.upload.onload=()=>{status.textContent='ตรวจไฟล์…';$('#upload-label').textContent=`${index+1} / ${total} · กำลังตรวจสอบ…`;};
    xhr.onload=()=>{
      let response;try{response=JSON.parse(xhr.responseText);}catch{}
      if(xhr.status===201&&response?.id)return resolve(response);
      reject(new Error(response?.error||(xhr.status===413?'ไฟล์ใหญ่เกินเพดานของเซิร์ฟเวอร์หรือ reverse proxy (HTTP 413)':`อัปโหลดไม่สำเร็จ (HTTP ${xhr.status})`)));
    };
    xhr.onerror=()=>reject(new Error('การเชื่อมต่อขัดข้อง'));xhr.ontimeout=()=>reject(new Error('หมดเวลาอัปโหลด โปรดรีเฟรชเพื่อตรวจสอบไฟล์'));xhr.onabort=()=>reject(new Error('การอัปโหลดถูกยกเลิก'));xhr.send(file);
  });
}
$('#upload-open').addEventListener('click', () => {
  $('#upload-project').textContent = `บันทึกใน Project “${selectedProject.name}”`;
  openDialog('#upload-dialog');
});
$('#upload-cancel').addEventListener('click', () => {
  stopUploadQueue = true;
  $('#upload-cancel').disabled = true;
  $('#upload-label').textContent = 'กำลังบันทึกไฟล์ปัจจุบัน แล้วหยุดคิว';
});
async function uploadFiles(files) {
  if (!files.length || uploadActive || account?.role !== 'admin') return;
  uploadActive = true;
  stopUploadQueue = false;
  const targetProject = { ...selectedProject };
  const uploadAccount = account;
  const customName = files.length === 1 ? $('#model-name').value.trim() : '';
  const progress = $('#upload-progress');
  const result = $('#upload-result');
  result.hidden = true;
  result.classList.remove('error');
  progress.hidden = false;
  $('#upload-cancel').disabled = false;
  $('#upload-cancel').textContent = 'หยุดคิวหลังไฟล์นี้';
  for (const id of ['#model-file', '#model-name', '#project-select', '#new-project']) $(id).disabled = true;
  const queue = $('#upload-queue'); queue.replaceChildren();
  const rows = files.map(file => {
    const row = document.createElement('li'), name = document.createElement('strong'), status = document.createElement('small');
    name.textContent = file.name; status.textContent = 'รออัปโหลด'; row.dataset.status = 'queued'; row.append(name,status); queue.append(row);
    return { row, status };
  });
  let savedCount = 0, errors = 0;
  try {
    for (const [index, file] of files.entries()) {
      const { row, status } = rows[index];
      if (stopUploadQueue || account !== uploadAccount) { row.dataset.status = 'cancelled'; status.textContent = 'ยกเลิก'; continue; }
      row.dataset.status = 'uploading'; status.textContent = 'กำลังส่ง…';
      $('#upload-label').textContent = `${index + 1} / ${files.length} · ${file.name}`;
      $('#portal-notice').textContent = `กำลังเพิ่มไฟล์ ${index + 1} / ${files.length}`;
      $('#upload-bar').value = 0;
      try {
        const format = file.name.split('.').pop().toLowerCase();
        if (!uploadAccount.formats.includes(format)) throw new Error('รองรับ GLB, glTF, OBJ, PLY, SPZ, SPLAT และ ZIP');
        if (file.size < 20 || file.size > uploadAccount.max_upload_bytes) throw new Error(`ขนาดไฟล์ต้องอยู่ระหว่าง 20 B ถึง ${formatBytes(uploadAccount.max_upload_bytes)}`);
        const uploaded=await sendUpload(file,format==='zip'?'/api/imports':'/api/models',uploadAccount,targetProject,customName,status,index,files.length);
        if(account!==uploadAccount)throw new Error('การเข้าสู่ระบบเปลี่ยนแล้ว โปรดตรวจรายการหลังเข้าสู่ระบบใหม่');
        if(format==='zip'){
          status.textContent='รอตรวจรายการก่อนนำเข้า';
          await workspaceTools.previewImport(uploaded,targetProject,customName||file.name.replace(/\.zip$/i,''));
        }
        savedCount++; row.dataset.status = 'saved'; status.textContent = 'บันทึกแล้ว';
      } catch (error) { errors++; row.dataset.status = 'error'; status.textContent = error.message; }
    }
    if (account !== uploadAccount) return;
    await loadProjects(selectedProject.id, false);
    $('#model-name').value = '';
    result.textContent = `บันทึก ${savedCount} / ${files.length} ไฟล์ใน Project “${targetProject.name}”${errors ? ` · ไม่สำเร็จ ${errors} ไฟล์` : ''}${stopUploadQueue ? ' · หยุดคิวแล้ว' : ''} · คลิกโมเดลในคลังเพื่อเปิดดู`;
    result.classList.toggle('error', errors > 0);
    result.hidden = false;
    $('#portal-notice').textContent = `บันทึก ${savedCount} / ${files.length} ไฟล์${errors ? ` · ไม่สำเร็จ ${errors} ไฟล์` : ''}`;
  } catch (error) {
    result.textContent = error.message;
    result.classList.add('error');
    result.hidden = false;
    $('#upload-label').textContent = error.message;
  }
  finally {
    uploadActive = false; progress.hidden = true;
    for (const id of ['#model-file', '#model-name', '#project-select', '#new-project']) $(id).disabled = false;
  }
}

const uploadZone = document.querySelector('.upload-zone');
uploadZone.addEventListener('keydown', event => {
  if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); $('#model-file').click(); }
});
for (const type of ['dragenter', 'dragover']) uploadZone.addEventListener(type, event => {
  event.preventDefault();
  uploadZone.classList.add('drag-over');
});
for (const type of ['dragleave', 'dragend']) uploadZone.addEventListener(type, event => {
  if (type === 'dragleave' && uploadZone.contains(event.relatedTarget)) return;
  uploadZone.classList.remove('drag-over');
});
uploadZone.addEventListener('drop', event => {
  event.preventDefault();
  uploadZone.classList.remove('drag-over');
  const files = [...(event.dataTransfer?.files || [])];
  uploadFiles(files);
});
window.addEventListener('dragover', event => { if (event.dataTransfer?.types.includes('Files')) event.preventDefault(); });
window.addEventListener('drop', event => { if (event.dataTransfer?.types.includes('Files') && !uploadZone.contains(event.target)) event.preventDefault(); });

document.querySelectorAll('#mode-bar button').forEach(button => button.addEventListener('click', () => setMode(button.dataset.mode)));
function openRename(model) {
  if (!model || account.role !== 'admin') return;
  renameTarget = model;
  $('#rename-error').textContent = '';
  $('#selected-name').value = model.name;
  openDialog('#rename-dialog');
  $('#selected-name').focus();
  $('#selected-name').select();
}
$('#edit-selected-name').addEventListener('click', () => openRename(selected));
$('#cancel-selected-name').addEventListener('click', () => {
  $('#rename-dialog').close();
});
$('#selected-name-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!renameTarget || account.role !== 'admin') return;
  const model = renameTarget;
  const name = $('#selected-name').value.trim();
  if (!name) return $('#selected-name').focus();
  const save = event.submitter;
  if (save) save.disabled = true;
  try {
    const updated = await request(`/api/models/${model.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': account.csrf },
      body: JSON.stringify({ name })
    });
    model.name = updated.name;
    if (selected?.id === model.id) {
      $('#viewer-title').textContent = updated.name;
      $('#download-model').download = `${updated.name}.${model.format}`;
    }
    $('#selected-name').value = updated.name;
    $('#rename-dialog').close();
    renderModels();
  } catch (error) {
    $('#rename-error').textContent = error.message;
  } finally {
    if (save) save.disabled = false;
  }
});
$('#copy-link').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText(location.href); $('#portal-notice').textContent = 'คัดลอกลิงก์แล้ว · ผู้เปิดต้องเข้าสู่ระบบ'; }
  catch { $('#portal-notice').textContent = 'คัดลอกลิงก์จากแถบที่อยู่ของเบราว์เซอร์ได้'; }
});
for (const button of document.querySelectorAll('[data-touch-key]')) {
  const release = () => {
    if (!button.classList.contains('pressed')) return;
    viewer?.setTouchKey(button.dataset.touchKey, false);
    button.classList.remove('pressed');
  };
  button.addEventListener('pointerdown', event => {
    event.preventDefault();
    button.setPointerCapture?.(event.pointerId);
    viewer?.setTouchKey(button.dataset.touchKey, true);
    button.classList.add('pressed');
  });
  button.addEventListener('pointerup', release);
  button.addEventListener('pointercancel', release);
  button.addEventListener('lostpointercapture', release);
}
const moveStick = $('#move-stick');
function moveStickRadius() { return (Math.min(moveStick.clientWidth, moveStick.clientHeight) - $('#move-stick-knob').offsetWidth) / 2; }
function renderMoveStick(x, y, dx = 0, dy = 0) {
  const knob = $('#move-stick-knob');
  knob.style.setProperty('--stick-x', `${dx}px`);
  knob.style.setProperty('--stick-y', `${dy}px`);
  moveStick.classList.toggle('active', Math.hypot(x, y) > 0.04);
  moveStick.setAttribute('aria-valuenow', String(Math.round(-y * 100)));
  const direction = [];
  if (y < -0.25) direction.push('เดินหน้า');
  if (y > 0.25) direction.push('ถอยหลัง');
  if (x < -0.25) direction.push('ซ้าย');
  if (x > 0.25) direction.push('ขวา');
  moveStick.setAttribute('aria-valuetext', direction.join(' / ') || 'หยุด');
  viewer?.setTouchMove(x, y);
}
function releaseVirtualStick() {
  const pointerId = moveStickPointerId;
  moveStickPointerId = null;
  moveStickKeyboard.clear();
  renderMoveStick(0, 0);
  if (pointerId !== null && moveStick.hasPointerCapture(pointerId)) moveStick.releasePointerCapture(pointerId);
}
function updateKeyboardStick() {
  if (moveStickPointerId !== null) return;
  const x = Number(moveStickKeyboard.has('ArrowRight')) - Number(moveStickKeyboard.has('ArrowLeft'));
  const y = Number(moveStickKeyboard.has('ArrowDown')) - Number(moveStickKeyboard.has('ArrowUp'));
  const length = Math.hypot(x, y) || 1;
  const unitX = x / length, unitY = y / length, radius = moveStickRadius() * 0.65;
  renderMoveStick(unitX, unitY, unitX * radius, unitY * radius);
}
moveStick.addEventListener('pointerdown', event => {
  event.preventDefault();
  moveStickPointerId = event.pointerId;
  moveStickKeyboard.clear();
  moveStick.setPointerCapture(event.pointerId);
  updateVirtualStickFromPointer(event);
});
function updateVirtualStickFromPointer(event) {
  if (moveStickPointerId !== event.pointerId) return;
  const rect = moveStick.getBoundingClientRect();
  const radius = moveStickRadius();
  let dx = event.clientX - (rect.left + rect.width / 2);
  let dy = event.clientY - (rect.top + rect.height / 2);
  const distance = Math.hypot(dx, dy);
  if (distance > radius) { dx *= radius / distance; dy *= radius / distance; }
  renderMoveStick(dx / radius, dy / radius, dx, dy);
  event.preventDefault();
}
moveStick.addEventListener('pointermove', updateVirtualStickFromPointer);
moveStick.addEventListener('pointerup', releaseVirtualStick);
moveStick.addEventListener('pointercancel', releaseVirtualStick);
moveStick.addEventListener('lostpointercapture', () => {
  if (moveStickPointerId !== null) releaseVirtualStick();
});
moveStick.addEventListener('keydown', event => {
  if (!['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(event.key)) return;
  event.preventDefault();
  moveStickKeyboard.add(event.key);
  updateKeyboardStick();
});
moveStick.addEventListener('keyup', event => {
  if (!moveStickKeyboard.delete(event.key)) return;
  event.preventDefault();
  updateKeyboardStick();
});
window.addEventListener('blur', () => {
  releaseVirtualStick();
  for (const button of document.querySelectorAll('[data-touch-key].pressed')) {
    viewer?.setTouchKey(button.dataset.touchKey, false);
    button.classList.remove('pressed');
  }
});
$('#viewer-orientation').addEventListener('click', () => {
  const flipped = viewer?.toggleOrientation();
  if (typeof flipped !== 'boolean' || !selected) return;
  saveModelOrientation(selected.id, flipped);
  $('#viewer-orientation').textContent = flipped ? 'หมุนกลับ 180°' : 'หมุนโมเดล 180°';
});
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
$('#center-view').addEventListener('click', () => viewer?.recenter());
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
    openDialog('#log-dialog');
  } catch (error) { $('#list-error').textContent = error.message; }
});
$('#close-log').addEventListener('click', () => $('#log-dialog').close());

document.documentElement.dataset.portalReady = '1';
boot().catch(error => { $('#login-error').textContent = error.message; });
