const root = document.querySelector('#glb-export');
if (root) {
  const status = document.querySelector('#glb-status'), progress = document.querySelector('#glb-progress');
  const button = document.querySelector('#glb-start'), ready = document.querySelector('#glb-ready');
  const details = document.querySelector('#glb-details');
  let state = JSON.parse(document.querySelector('#glb-initial').textContent);
  function paint() {
    const busy = ['queued', 'running'].includes(state.state);
    button.disabled = busy || state.state === 'ready';
    button.textContent = state.state === 'error' ? 'ลองส่งออกอีกครั้ง' : state.state === 'ready' ? 'เตรียม GLB แล้ว' : 'เตรียม GLB';
    status.textContent = ({idle: 'พร้อมส่งออก', queued: 'รอคิวส่งออก', running: `กำลังส่งออก ${state.progress || 0}%`,
      ready: 'ส่งออกสำเร็จ พร้อมดาวน์โหลด', error: state.error || 'ส่งออกไม่สำเร็จ'})[state.state];
    progress.hidden = !busy;
    if (state.state === 'queued') progress.removeAttribute('value');
    else progress.value = state.progress || 0;
    ready.hidden = state.state !== 'ready';
    details.textContent = state.state === 'ready' ? `${(state.bytes / 1048576).toFixed(1)} MB · ${state.splats.toLocaleString()} splats · SH ${state.sh_degree}` : '';
    return busy;
  }
  async function poll() {
    if (!['queued', 'running'].includes(state.state)) return;
    try {
      if (!document.hidden) {
        const response = await fetch(`/api/glb-export/${root.dataset.id}`, {cache: 'no-store', signal: AbortSignal.timeout(15000)});
        if (!response.ok) throw new Error('อ่านสถานะไม่ได้');
        const next = await response.json();
        // A worker may be replacing its progress file as this request reads it.
        if (next.state === 'running' && state.state === 'running') next.progress = Math.max(state.progress || 0, next.progress || 0);
        state = next; paint();
      }
    } catch { status.textContent = 'กำลังเชื่อมต่อสถานะใหม่ งานส่งออกยังทำต่อได้'; }
    if (['queued', 'running'].includes(state.state)) setTimeout(poll, 2000);
  }
  if (paint()) setTimeout(poll, 1000);
}
