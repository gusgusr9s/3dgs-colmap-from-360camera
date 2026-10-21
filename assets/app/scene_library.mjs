const form = document.querySelector('#scene-create');
const status = document.querySelector('#scene-library-status');
let busy = false;
async function post(path, value) {
  const response = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(value)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'บันทึกไม่สำเร็จ');
  return result;
}
async function create(payload) {
  if (busy) return;
  busy = true;
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  status.textContent = 'กำลังสร้างฉาก…';
  try {
    const scene = await post('/api/scenes', payload);
    location.href = '/scene/' + scene.id;
  } catch (error) {
    status.textContent = error.message;
    busy = false;
    button.disabled = false;
  }
}
form.addEventListener('submit', event => {
  event.preventDefault();
  const payload = {name: document.querySelector('#scene-new-name').value};
  const model = new URL(location.href).searchParams.get('model');
  if (model) payload.objects = [{asset: {kind: 'ply', ref: model}, name: model}];
  create(payload);
});
document.querySelector('#scene-import').addEventListener('change', async event => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    if (file.size > 2 * 1024 * 1024) throw new Error('ฉาก JSON ต้องมีขนาดไม่เกิน 2 MB');
    const payload = JSON.parse(await file.text());
    await create(payload);
  } catch (error) { status.textContent = error.message; }
  event.target.value = '';
});
document.querySelectorAll('[data-scene-delete]').forEach(button => button.addEventListener('click', async () => {
  if (!confirm('ลบฉากนี้และข้อมูลการจัดวาง?')) return;
  button.disabled = true;
  try {
    await post('/api/scenes/' + button.dataset.sceneDelete + '/delete', {revision: Number(button.dataset.revision)});
    location.reload();
  } catch (error) { status.textContent = error.message; button.disabled = false; }
}));
