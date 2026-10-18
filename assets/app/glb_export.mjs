const root = document.querySelector('#glb-export');
if (root) {
  const status = document.querySelector('#glb-status'), progress = document.querySelector('#glb-progress');
  const button = document.querySelector('#glb-start'), ready = document.querySelector('#glb-ready');
  const details = document.querySelector('#glb-details');
  let state = JSON.parse(document.querySelector('#glb-initial').textContent);
  let packageState = JSON.parse(document.querySelector('#package-initial')?.textContent || '{"state":"unavailable"}');
  const packageButton=document.querySelector('#package-start'), packageStatus=document.querySelector('#package-status');
  const packageProgress=document.querySelector('#package-progress'), packageDownload=document.querySelector('#package-download');
  let packageTimer;
  function paintPackage() {
    if(!packageButton)return;
    const busy=['queued','running'].includes(packageState.state), complete=packageState.state==='ready';
    packageButton.disabled=busy||complete||state.state!=='ready';
    packageButton.hidden=complete;
    packageButton.textContent=complete?'เตรียม ZIP แล้ว':packageState.state==='error'?'ลองเตรียม ZIP อีกครั้ง':'เตรียม ZIP พร้อมเปิด';
    packageStatus.textContent=({unavailable:'เตรียม GLB ให้เสร็จก่อน',idle:'พร้อมรวมโมเดลและตัวอย่างเป็น ZIP',queued:'รอคิวเตรียม ZIP',
      running:`กำลังเตรียม ZIP ${packageState.progress||0}%`,ready:`พร้อมดาวน์โหลด · ${(packageState.bytes/1048576).toFixed(1)} MB`,
      error:packageState.error||'เตรียม ZIP ไม่สำเร็จ'})[packageState.state];
    packageProgress.hidden=!busy;
    if(packageState.state==='queued')packageProgress.removeAttribute('value');else packageProgress.value=packageState.progress||0;
    packageDownload.hidden=!complete;
    if(complete)packageDownload.href=`/glb-package-download/${root.dataset.id}/${packageState.key}.zip`;
  }
  async function pollPackage() {
    clearTimeout(packageTimer);
    packageTimer=null;
    try {
      if(!document.hidden) {
        const response=await fetch(`/api/glb-package/${root.dataset.id}`,{cache:'no-store',signal:AbortSignal.timeout(15000)});
        if(!response.ok)throw new Error();
        packageState=await response.json();paintPackage();
      }
    } catch {if(packageStatus)packageStatus.textContent='กำลังเชื่อมต่อสถานะ ZIP ใหม่ งานยังทำต่อได้';}
    if(['queued','running'].includes(packageState.state) ||
        (packageState.state==='unavailable'&&['queued','running','ready'].includes(state.state)))packageTimer=setTimeout(pollPackage,2000);
  }
  function paint() {
    const busy = ['queued', 'running'].includes(state.state);
    button.disabled = busy || state.state === 'ready';
    button.hidden = state.state === 'ready';
    button.textContent = state.state === 'error' ? 'ลองส่งออกอีกครั้ง' : state.state === 'ready' ? 'เตรียม GLB แล้ว' : 'เตรียม GLB';
    status.textContent = ({idle: 'พร้อมส่งออก', queued: 'รอคิวส่งออก', running: `กำลังส่งออก ${state.progress || 0}%`,
      ready: 'ส่งออกสำเร็จ พร้อมดาวน์โหลด', error: state.error || 'ส่งออกไม่สำเร็จ'})[state.state];
    progress.hidden = !busy;
    if (state.state === 'queued') progress.removeAttribute('value');
    else progress.value = state.progress || 0;
    ready.hidden = state.state !== 'ready';
    details.textContent = state.state === 'ready' ? `${(state.bytes / 1048576).toFixed(1)} MB · ${state.splats.toLocaleString()} splats · SH ${state.sh_degree}` : '';
    paintPackage();
    if(packageButton&&state.state==='ready'&&packageState.state==='unavailable'&&!packageTimer)packageTimer=setTimeout(pollPackage,1000);
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
  if(packageButton&&!packageTimer&&(['queued','running'].includes(packageState.state)||
      (packageState.state==='unavailable'&&['queued','running'].includes(state.state))))packageTimer=setTimeout(pollPackage,1000);
}
