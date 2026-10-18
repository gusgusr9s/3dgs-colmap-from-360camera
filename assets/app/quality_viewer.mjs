const data = JSON.parse(document.getElementById('config').textContent);
const $ = id => document.getElementById(id);
let controller, generation = 0;
let selected = 0;
const tabs = [...document.querySelectorAll('[data-quality-tab]')];
let activePanel = 'quality-split';
const metrics = new Map();
const reference = $('quality-reference');
function referenceUrl(item) { return reference?.value === 'raw' && item.raw_source ? item.raw_source : item.source; }
for (let i = 0; i < data.length; i++) {
  const option = document.createElement('option'); option.value = i; option.textContent = data[i].name;
  $('view').append(option);
}
function updateImages() {
  const item = data[selected]; if (!item) return;
  if (activePanel === 'quality-split') {
    $('source').src = referenceUrl(item); $('render').src = item.render;
  } else if (activePanel === 'quality-wipe') {
    $('compare-source').src = referenceUrl(item); $('overlay').src = item.render;
  } else {
    if (item.previous) $('previous').src = item.previous;
    if (item.mask) $('mask').src = item.mask;
  }
}
function activateTab(tab, focus = false) {
  activePanel = tab.dataset.qualityTab;
  for (const button of tabs) {
    const current = button === tab;
    button.setAttribute('aria-selected', String(current)); button.tabIndex = current ? 0 : -1;
    $(button.dataset.qualityTab).hidden = !current;
  }
  if (focus) tab.focus();
  updateImages();
}
for (const tab of tabs) {
  tab.addEventListener('click', () => activateTab(tab));
  tab.addEventListener('keydown', event => {
    const index = tabs.indexOf(tab);
    const next = event.key === 'ArrowRight' ? (index + 1) % tabs.length : event.key === 'ArrowLeft' ? (index - 1 + tabs.length) % tabs.length : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : null;
    if (next !== null) { event.preventDefault(); activateTab(tabs[next], true); }
  });
}
function metricLabel(value) {
  if (value.psnr == null) return 'มุมนี้ถูก Mask ทั้งภาพ จึงไม่มีคะแนน · ลองเลือกภาพถัดไป';
  return 'PSNR รอบนี้ ' + value.psnr.toFixed(2) + ' dB' +
    (value.previous_psnr != null ? ' · Model เดิมที่มุมนี้ ' + value.previous_psnr.toFixed(2) + ' dB' : ' · ไม่มีภาพมุมนี้จาก model เดิมสำหรับเทียบ') +
    (value.valid_pixels_percent != null ? ` · ใช้พื้นที่ภาพ ${value.valid_pixels_percent.toFixed(1)}%` : '');
}
async function select(index) {
  const item = data[index]; if (!item) return;
  selected = Number(index); $('view').value = String(selected);
  for (const [id, variant] of [['quality-full', 'full'], ['quality-fast', 'fast']]) {
    const link = $(id);
    if (link) {
      link.hidden = !item.viewer_url || (variant === 'fast' && !item.fast_available);
      if (!link.hidden) link.href = item.viewer_url + '&variant=' + variant;
    }
  }
  $('quality-counter').textContent = `${selected + 1} / ${data.length}`;
  $('quality-previous').disabled = selected === 0;
  $('quality-next').disabled = selected === data.length - 1;
  const token = ++generation;
  controller?.abort(); controller = new AbortController();
  $('previous-card').hidden = !item.previous; $('mask-card').hidden = !item.mask;
  $('quality-details-empty').hidden = !!(item.previous || item.mask);
  updateImages();
  $('metrics').setAttribute('aria-busy', 'true');
  $('metrics').textContent = 'กำลังคำนวณคะแนนเฉพาะมุมนี้…';
  try {
    let value = metrics.get(item.metrics_url);
    if (!value) {
      const response = await fetch(item.metrics_url, { signal: controller.signal });
      if (!response.ok) throw new Error('Comparison metrics unavailable');
      value = await response.json(); metrics.set(item.metrics_url, value);
    }
    if (token === generation) $('metrics').textContent = metricLabel(value);
  } catch (error) {
    if (token === generation && error.name !== 'AbortError') $('metrics').textContent = 'อ่านคะแนนมุมนี้ไม่ได้ · เลือกมุมอีกครั้งเพื่อลองใหม่';
  } finally {
    if (token === generation) $('metrics').setAttribute('aria-busy', 'false');
  }
}
$('view').addEventListener('change', event => select(event.target.value));
$('quality-previous').addEventListener('click', () => select(selected - 1));
$('quality-next').addEventListener('click', () => select(selected + 1));
document.addEventListener('keydown', event => {
  if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || event.target.closest('input,select,textarea,button,a,[contenteditable],[role="tablist"]')) return;
  if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
    event.preventDefault(); select(selected + (event.key === 'ArrowLeft' ? -1 : 1));
  }
});
$('wipe').addEventListener('input', event => { $('overlay').style.clipPath = 'inset(0 ' + (100 - Number(event.target.value)) + '% 0 0)'; });
reference?.addEventListener('change', () => {
  if ($('quality-source-label')) $('quality-source-label').textContent = reference.value === 'raw' ? 'วิดีโอ 360 ต้นฉบับ · ฉายมุมเดียวกัน' : 'ภาพอ้างอิงที่เตรียมไว้';
  if ($('quality-image-status')) $('quality-image-status').textContent = reference.value === 'raw' ? 'กำลังอ่านภาพจากต้นฉบับ… คะแนนยังเทียบกับภาพอ้างอิงที่เตรียมไว้' : 'คะแนนด้านบนเทียบกับภาพอ้างอิงที่เตรียมไว้เสมอ';
  updateImages();
});
for (const id of ['source', 'compare-source']) {
  $(id).addEventListener('error', () => { if ($('quality-image-status')) $('quality-image-status').textContent = 'อ่านภาพอ้างอิงไม่ได้ อาจไม่มี Source เดิม · เลือกภาพอ้างอิงที่เตรียมไว้เพื่อดูต่อ'; });
  $(id).addEventListener('load', () => { if ($('quality-image-status')) $('quality-image-status').textContent = 'คะแนนด้านบนเทียบกับภาพอ้างอิงที่เตรียมไว้เสมอ · เลื่อนภาพซ้าย/ขวาเพื่อดูรายละเอียด'; });
}
$('quality-zoom')?.addEventListener('change', event => {
  for (const element of document.querySelectorAll('.quality-zoom-area')) element.style.setProperty('--review-zoom', event.target.value + '%');
});
for (const id of ['source', 'render']) {
  const area = $(id).parentElement;
  area?.addEventListener('scroll', () => {
    const other = $(id === 'source' ? 'render' : 'source').parentElement;
    if (other && (other.scrollLeft !== area.scrollLeft || other.scrollTop !== area.scrollTop)) {
      other.scrollLeft = area.scrollLeft; other.scrollTop = area.scrollTop;
    }
  });
}
if (data.length) { $('view').disabled = false; select(0); }
else { $('metrics').textContent = 'ไม่มีภาพทดสอบสำหรับ Model รุ่นนี้'; $('quality-counter').textContent = '0 / 0'; }
