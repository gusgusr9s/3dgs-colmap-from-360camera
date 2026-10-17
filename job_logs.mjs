// A fixed-height console: polling never replaces controls or reloads the page.
export function applySnapshot(root, data) {
  const output = root.querySelector('[data-log-output]');
  const select = root.querySelector('[data-log-source]');
  const options = [{key:'auto',label:'ติดตามงานปัจจุบันอัตโนมัติ'}, ...data.sources];
  const key = JSON.stringify(options);
  if (select.dataset.options !== key) {
    const selected = select.value;
    select.replaceChildren(...options.map(item => {
      const option = document.createElement('option'); option.value=item.key; option.textContent=item.label; return option;
    }));
    select.value=options.some(item=>item.key===selected)?selected:'auto'; select.dataset.options=key;
  }
  const set = (selector, text) => { const node=root.querySelector(selector); if(node.textContent!==text)node.textContent=text; };
  set('[data-log-step]', data.step || 'ยังไม่มีข้อมูลขั้นตอน');
  const modified=data.modified_at?new Date(data.modified_at).toLocaleString('th-TH',{timeZone:'Asia/Bangkok'}):'—';
  set('[data-log-info]', (data.path || 'ยังไม่มีไฟล์ log ของขั้นตอนนี้')+' · ข้อความล่าสุด '+modified+' (UTC+7)');
  if (!data.unchanged) {
    const text=data.text || 'ยังไม่มีข้อความ log · รอขั้นตอนเริ่มทำงาน';
    if(output.textContent!==text) {
      const atBottom=output.scrollHeight-output.clientHeight-output.scrollTop<35;
      const top=output.scrollTop;
      output.textContent=text;
      output.scrollTop=atBottom?output.scrollHeight:top;
    }
  }
  const link=root.querySelector('[data-log-file]'); link.hidden=!data.path;
  if(data.path)link.href='/jobs/'+encodeURIComponent(root.dataset.liveLog)+'/'+data.path.split('/').map(encodeURIComponent).join('/');
  return data.cursor || '';
}

export function mountConsole(root) {
  const details=root.querySelector('details'), output=root.querySelector('[data-log-output]');
  const select=root.querySelector('[data-log-source]'), pause=root.querySelector('[data-log-pause]');
  const note=root.querySelector('[data-log-status]'), retry=root.querySelector('[data-log-retry]');
  let paused=false, cursor='', controller=null, timer=null, generation=0, stopped=false;
  const selectedText=()=>{
    const selection=window.getSelection();
    return Boolean(selection?.toString() && output.contains(selection.anchorNode));
  };
  const readingPast=()=>output.scrollHeight-output.clientHeight-output.scrollTop>=35;
  const schedule=(delay)=>{clearTimeout(timer); if(!stopped)timer=setTimeout(()=>poll(),delay);};
  const cancel=()=>{generation++;controller?.abort();controller=null;clearTimeout(timer);};
  async function poll(force=false) {
    if(stopped)return;
    if(controller)return;
    if(document.hidden || !details.open || paused || selectedText() || (!force && readingPast())) {
      if(!paused && details.open && !document.hidden && (selectedText() || readingPast()))note.textContent='พักการแสดงขณะอ่านข้อความเดิม · กดข้อความล่าสุดเพื่อติดตามต่อ';
      schedule(2500);return;
    }
    const current=++generation;
    const requestController=new AbortController();controller=requestController;
    const timeout=setTimeout(()=>requestController.abort(),10000);
    let delay=5000;
    try {
      const query=new URLSearchParams({source:select.value,cursor:force?'':cursor});
      const response=await fetch('/api/job-log/'+encodeURIComponent(root.dataset.liveLog)+'?'+query,{cache:'no-store',signal:requestController.signal});
      if(!response.ok)throw new Error('HTTP '+response.status);
      const data=await response.json();
      if(current!==generation || paused || !details.open)return;
      // Do not disturb text selection created while the request was in flight.
      if(selectedText() || (!force && readingPast()))return;
      cursor=applySnapshot(root,data);
      note.textContent=data.path ? (data.busy?'ติดตามการทำงานอยู่':'โหลด log แล้ว') : 'รอ log ของขั้นตอนนี้';
      retry.hidden=true; delay=data.busy?2500:15000;
    } catch(error) {
      if(current!==generation)return;
      note.textContent='โหลด log ไม่สำเร็จ · จะลองเชื่อมต่อใหม่'; retry.hidden=false;
    } finally {
      clearTimeout(timeout);
      if(current===generation){controller=null;schedule(delay);}
    }
  }
  select.addEventListener('change',()=>{cancel();cursor='';poll(true);});
  pause.addEventListener('click',()=>{
    paused=!paused;cancel(); pause.setAttribute('aria-pressed',String(paused));
    pause.textContent=paused?'ติดตามต่อ':'พักการอัปเดต';
    note.textContent=paused?'พักการแสดง log · งานประมวลผลยังทำต่อ':'กำลังเชื่อมต่อ…';
    if(!paused)poll();
  });
  root.querySelector('[data-log-latest]').addEventListener('click',()=>{output.scrollTop=output.scrollHeight;if(!paused){cancel();poll();}});
  retry.addEventListener('click',()=>{cancel();poll(true);});
  details.addEventListener('toggle',()=>{cancel();if(details.open&&!paused)poll();});
  document.addEventListener('visibilitychange',()=>{cancel();if(!document.hidden)poll();});
  window.addEventListener('pagehide',()=>{stopped=true;cancel();},{once:true});
  poll();
}

if(typeof document!=='undefined')document.querySelectorAll('[data-live-log]').forEach(mountConsole);
