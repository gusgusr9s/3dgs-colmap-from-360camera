// Only the Verify button starts a check. Opening the dashboard does not probe the GPU.
const panel = document.querySelector('[data-runtime-verify]');
if (panel) {
  const button=panel.querySelector('[data-verify-start]'), note=panel.querySelector('[data-verify-note]');
  const execute=panel.querySelector('[data-verify-execute]');
  const result=panel.querySelector('[data-verify-result]');
  let pending=false, timer;
  function table(headings, rows) {
    const element=document.createElement('table'), body=document.createElement('tbody');
    const head=document.createElement('thead'),headRow=document.createElement('tr');
    for(const value of headings){const cell=document.createElement('th');cell.textContent=value;headRow.append(cell);}
    head.append(headRow);element.append(head);
    for(const [state,values] of rows) {
      const row=document.createElement('tr');row.dataset.state=state;
      for(const value of values){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}
      body.append(row);
    }
    element.append(body);return element;
  }
  function executionMarkup(report) {
    const nodes=[];
    if(!report.execution_requested)return nodes;
    const heading=document.createElement('h3');heading.textContent='ผลรันจริง CPU/GPU';nodes.push(heading);
    if(report.execution_state==='blocked'){const blocked=document.createElement('p');blocked.textContent=report.execution_reason;nodes.push(blocked);return nodes;}
    const label={passed:'ผ่าน',failed:'ไม่ผ่าน',not_tested:'ยังไม่ได้ทดสอบ'};
    nodes.push(table(['ขั้นตอน','ขอใช้','ใช้จริง','ผล','เวลา','รายละเอียด'],(report.execution_checks||[]).map(check=>[
      check.state==='passed'?'available':check.state==='failed'?'error':'warning',
      [check.label,check.requested_backend,check.actual_backend,label[check.state]||check.state,
       `${Number(check.seconds||0).toFixed(2)} s`,check.detail||'']])));
    if(report.execution_scope){const scope=document.createElement('p');scope.textContent=report.execution_scope;nodes.push(scope);}
    return nodes;
  }
  function show(report) {
    const running=report.state==='running';button.disabled=running;if(execute)execute.disabled=running;
    if(running) {note.textContent=report.execution_requested
      ?`กำลังทดสอบรันจริงบน CPU/GPU · ${Math.floor(report.elapsed_seconds||0)} วินาที · fixture แยก ไม่แตะ Job`
      :`กำลังตรวจเครื่อง · ${Math.floor(report.elapsed_seconds||0)} วินาที · ไม่รัน Model หรือ inference`;return;}
    if(report.state==='idle')return;
    if(report.state==='error'){note.textContent='ตรวจไม่สำเร็จ · '+report.error+' · กด Verify เพื่อลองใหม่';return;}
    note.textContent=(report.ready?'ผ่านการตรวจเบื้องต้น':'พบส่วนที่ต้องติดตั้งหรือแก้ไข')+
      (report.execution_requested?(report.execution_ready?' · รันจริงผ่านทุกขั้น':' · รันจริงมีขั้นที่ไม่ผ่าน/ถูกข้าม'):'')+
      ' · '+new Date(report.checked_at).toLocaleString('th-TH');
    result.replaceChildren();
    const env=document.createElement('p');env.textContent=`${report.os} · Python ${report.python_version} · ${report.python}`+
      (report.cpu_runtime?` · CPU ${report.cpu_runtime.mode}: ${report.cpu_runtime.threads}/${report.cpu_runtime.available} threads`:'');
    const gpu=document.createElement('p');
    gpu.textContent='GPU: '+([...new Set([...(report.gpu.hardware||[]).map(a=>a.name),...(report.gpu.adapters||[]).map(a=>a.device)])].join(' · ')||'ไม่พบ hardware adapter');
    const table=document.createElement('table'), body=document.createElement('tbody');
    const head=document.createElement('thead'),headRow=document.createElement('tr');
    for(const value of ['ขั้นตอน','ใช้ประมวลผลด้วย','ผลตรวจ','เหตุผล']){const cell=document.createElement('th');cell.textContent=value;headRow.append(cell);}
    head.append(headRow);table.append(head);
    for(const stage of report.stages) {
      const row=document.createElement('tr');
      row.dataset.state=stage.state;
      const reason=stage.reason+(stage.version?' · '+stage.version:'')+
        (stage.walkthrough?' · วิดีโอปกติ: '+stage.walkthrough.backend+' / '+stage.walkthrough.version:'');
      for(const value of [stage.label,stage.backend,({available:'พร้อมเบื้องต้น',warning:'ต้องยืนยันเมื่อรัน',error:'ไม่พร้อม'})[stage.state],reason]) {
        const cell=document.createElement('td');cell.textContent=value;row.append(cell);
      }
      body.append(row);
    }
    table.append(body);const scope=document.createElement('p');scope.textContent=report.scope;
    const missing=document.createElement('p');missing.textContent=report.missing_packages.length?'ต้องติดตั้ง: '+report.missing_packages.join(', '):'';
    result.append(env,gpu,table,missing,scope,...executionMarkup(report));
  }
  async function poll(start=false, run=false) {
    if(pending)return;pending=true;clearTimeout(timer);
    try {
      const response=await fetch('/api/runtime-verification'+(start&&run?'?execute=1':''),{method:start?'POST':'GET',cache:'no-store',signal:AbortSignal.timeout(8000)});
      const report=await response.json();if(!response.ok)throw new Error(report.error||'เชื่อมต่อไม่ได้');
      show(report);if(report.state==='running')timer=setTimeout(()=>poll(),1000);
    } catch(error){button.disabled=false;note.textContent='อ่านผล Verify ไม่ได้ · '+error.message+' · กดเพื่อลองใหม่';}
    finally{pending=false;}
  }
  button.addEventListener('click',()=>{if(pending)return;button.disabled=true;note.textContent='กำลังเริ่มตรวจเครื่อง…';poll(true);});
  execute?.addEventListener('click',()=>{if(pending)return;button.disabled=execute.disabled=true;
    note.textContent='กำลังเริ่มทดสอบรันจริง CPU/GPU…';poll(true,true);});
  poll();
}
