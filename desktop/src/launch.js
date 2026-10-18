const status=document.querySelector('#status'),retry=document.querySelector('#retry');
window.studioDesktop.onStatus(value=>{status.textContent=value.message;document.querySelector('#workspace').textContent=value.workspace||'';retry.hidden=!value.error;});
retry.onclick=()=>window.studioDesktop.retry();
document.querySelector('#choose').onclick=()=>window.studioDesktop.chooseWorkspace();
document.querySelector('#logs').onclick=()=>window.studioDesktop.openLogs();
