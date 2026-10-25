// Organization and navigation work with every supported model format.
const $ = selector => document.querySelector(selector);

export function setupWorkspaceTools({state,request,openDialog,refresh,clearViewer,renderModels,icon,notice}) {
  const selectedIds = new Set();
  let selecting = false, visibleModels = [];
  function update(visible) {
    visibleModels=visible;
    const current=state(),admin=current.account?.role==='admin';
    for(const id of [...selectedIds])if(!current.models.some(model=>model.id===id))selectedIds.delete(id);
    $('#project-manage').hidden=!admin;$('#selection-toggle').hidden=!admin;
    $('#selection-bar').hidden=!admin||!selecting;
    $('#selection-toggle').setAttribute('aria-pressed',String(selecting));
    $('#selection-count').textContent=`เลือก ${selectedIds.size} รายการ`;
    $('#bulk-open').disabled=!selectedIds.size;
  }
  function attach(item,model) {
    item.dataset.modelId=model.id;
    if(model.thumbnail) {
      const image=document.createElement('img');image.className='model-thumbnail';image.alt='';image.loading='lazy';
      image.src=`/api/models/${model.id}/thumbnail${model.thumbnailVersion?`?v=${model.thumbnailVersion}`:''}`;
      const symbol=item.querySelector('.file-symbol');symbol.replaceChildren(image);
    }
    if(selecting&&state().account?.role==='admin') {
      const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.className='model-check';
      checkbox.setAttribute('aria-label',`เลือก ${model.name}`);checkbox.checked=selectedIds.has(model.id);
      checkbox.addEventListener('change',()=>{if(checkbox.checked)selectedIds.add(model.id);else selectedIds.delete(model.id);update(visibleModels);});
      item.prepend(checkbox);
    }
  }
  function resetSelection(){selectedIds.clear();selecting=false;renderModels();}
  $('#selection-toggle').addEventListener('click',()=>{selecting=!selecting;if(!selecting)selectedIds.clear();renderModels();});
  $('#select-visible').addEventListener('click',()=>{visibleModels.forEach(model=>selectedIds.add(model.id));renderModels();});
  $('#project-manage').addEventListener('click',()=>{
    $('#project-rename-name').value=state().selectedProject.name;$('#project-error').textContent='';openDialog('#project-dialog');$('#project-rename-name').focus();
  });
  $('#project-rename-form').addEventListener('submit',async event=>{
    event.preventDefault();const current=state(),button=event.submitter;button.disabled=true;
    try {
      await request(`/api/projects/${current.selectedProject.id}`,{method:'PATCH',headers:{'Content-Type':'application/json','X-CSRF-Token':current.account.csrf},body:JSON.stringify({name:$('#project-rename-name').value})});
      $('#project-dialog').close();await refresh(current.selectedProject.id);
    }catch(error){$('#project-error').textContent=error.message;}finally{button.disabled=false;}
  });
  $('#bulk-open').addEventListener('click',()=>{
    $('#bulk-error').textContent='';$('#bulk-summary').textContent=`เลือก ${selectedIds.size} รายการ · ย้ายหรือลบพร้อมกัน`;
    const select=$('#bulk-project');select.replaceChildren();
    for(const project of state().projects){const option=document.createElement('option');option.value=project.id;option.textContent=project.name;select.append(option);}
    select.value=state().projects.find(project=>project.id!==state().selectedProject.id)?.id||state().selectedProject.id;
    openDialog('#bulk-dialog');
  });
  async function batch(action) {
    if(action==='delete'&&!confirm(`ลบ ${selectedIds.size} รายการที่เลือกหรือไม่?`))return;
    const current=state(),ids=[...selectedIds];$('#bulk-move').disabled=true;$('#bulk-delete').disabled=true;
    try {
      await request('/api/models/batch',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':current.account.csrf},body:JSON.stringify({ids,action,project_id:$('#bulk-project').value})});
      if(ids.includes(current.selected?.id))clearViewer();
      if(action==='delete'&&'caches' in window){try{const cache=await caches.open('glb-portal-models-v1');for(const entry of await cache.keys())if(ids.includes(new URL(entry.url).pathname.split('/')[3]))await cache.delete(entry);}catch{/* Server-side deletion is already complete. */}}
      resetSelection();$('#bulk-dialog').close();await refresh(current.selectedProject.id);notice(action==='move'?`ย้าย ${ids.length} รายการแล้ว`:`ลบ ${ids.length} รายการแล้ว`);
    }catch(error){$('#bulk-error').textContent=error.message;}finally{$('#bulk-move').disabled=false;$('#bulk-delete').disabled=false;}
  }
  $('#bulk-move').addEventListener('click',()=>batch('move'));$('#bulk-delete').addEventListener('click',()=>batch('delete'));

  async function saveCover(manual=false) {
    const current=state(),model=current.selected,instance=current.viewer;
    if(!model||!instance||current.account?.role!=='admin')return;
    try {
      const blob=await instance.thumbnail();if(!blob||state().viewer!==instance||state().account!==current.account)return;
      await request(`/api/models/${model.id}/thumbnail`,{method:'POST',headers:{'Content-Type':'image/png','X-CSRF-Token':current.account.csrf},body:blob});
      const listed=state().models.find(item=>item.id===model.id);if(listed){listed.thumbnail=true;listed.thumbnailVersion=Date.now();}
      renderModels();if(manual)notice('บันทึกภาพปกแล้ว');
    }catch(error){if(manual)$('#places-error').textContent=error.message;}
  }
  $('#cover-save').addEventListener('click',()=>saveCover(true));
  function ready() {
    const current=state(),count=current.details?.metadata?.collider_count||0;
    $('#collision-info').textContent=count?`ใช้พื้นและกำแพง ${count} กล่อง · โหมดเดินเริ่มที่จุดที่บันทึกในฉาก`:'ไฟล์นี้ไม่มีข้อมูลตรวจชน · โหมดเดินรักษาระดับความสูง';
    $('#collision-helpers').disabled=!count;$('#collision-helpers').checked=false;
    if(current.account?.role==='admin'&&!current.selected.thumbnail)saveCover();
  }
  $('#collision-helpers').addEventListener('change',event=>state().viewer?.collisionVisibility(event.target.checked));

  function renderPlaces() {
    const current=state(),list=$('#places-list'),admin=current.account?.role==='admin';list.replaceChildren();
    function row(label,go,remove) {
      const item=document.createElement('div');item.className='place-row';
      const button=document.createElement('button');button.className='place-go';const text=document.createElement('span');text.textContent=label;button.innerHTML=icon('pin');button.append(text);
      button.addEventListener('click',()=>{$('#places-dialog').close();go();$('#canvas-host canvas')?.focus({preventScroll:true});});item.append(button);
      if(remove){const button=document.createElement('button');button.className='icon-button place-delete';button.innerHTML=icon('trash');button.setAttribute('aria-label','ลบจุด '+label);button.addEventListener('click',remove);item.append(button);}
      list.append(item);
    }
    if(current.details?.scene)row('จุดเริ่มเดิน',()=>current.viewer.respawn());
    for(const point of current.viewer?.fileViews||[])row(point.name+' · กล้องในไฟล์',()=>current.viewer.gotoView(point));
    const points=current.details?.viewpoints||[];
    for(const point of points)row(point.name,()=>current.viewer.gotoView(point),admin?()=>savePlaces(points.filter(item=>item.id!==point.id)):null);
    $('#places-empty').hidden=!!points.length||!!current.details?.scene||!!current.viewer?.fileViews.length;
    $('#place-form').hidden=!admin;$('#cover-save').hidden=!admin;
  }
  async function savePlaces(points) {
    const current=state(),button=$('#place-form button');button.disabled=true;
    try {
      const saved=await request(`/api/models/${current.selected.id}/viewpoints`,{method:'PATCH',headers:{'Content-Type':'application/json','X-CSRF-Token':current.account.csrf},
        body:JSON.stringify({viewpoints:points,revision:current.details.viewpoints_revision})});
      if(state().selected?.id!==current.selected.id)return;
      Object.assign(current.details,saved);$('#place-name').value='';$('#places-error').textContent='';renderPlaces();
    }catch(error){$('#places-error').textContent=error.message;}finally{button.disabled=false;}
  }
  $('#place-form').addEventListener('submit',event=>{
    event.preventDefault();const current=state();if(!current.viewer)return;
    savePlaces([...(current.details.viewpoints||[]),{id:crypto.randomUUID().replaceAll('-',''),name:$('#place-name').value.trim(),...current.viewer.getView()}]);
  });
  $('#places-open').addEventListener('click',async()=>{
    const current=state();if(!current.viewer)return;
    $('#places-error').textContent='';renderPlaces();openDialog('#places-dialog');
    try {const latest=await request(`/api/models/${current.selected.id}/details`);if(state().selected?.id===current.selected.id&&latest.viewpoints_revision>=current.details.viewpoints_revision){current.details.viewpoints=latest.viewpoints;current.details.viewpoints_revision=latest.viewpoints_revision;renderPlaces();}}
    catch(error){$('#places-error').textContent=error.message;}
  });

  function previewImport(staged,project,defaultName) {
    const dialog=$('#import-dialog');$('#import-name').value=defaultName.slice(0,80);$('#import-error').textContent='';
    const count=staged.objects.length;$('#import-summary').textContent=`${count} โมเดล · ${staged.files.length} ไฟล์หลัก · ${staged.metadata.collider_count} กล่องตรวจชน · Project “${project.name}”`;
    const body=$('#import-objects');body.replaceChildren();
    for(const object of staged.objects){const row=document.createElement('tr');for(const value of [object.name,object.position.map(number=>Number(number.toFixed(3))).join(' / ')]){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}body.append(row);}
    const map=$('#import-layout');map.replaceChildren();
    const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 440 170');svg.setAttribute('role','img');svg.setAttribute('aria-label','ตำแหน่งโมเดลตามแกน X และ Z');
    const xs=staged.objects.map(item=>item.position[0]),zs=staged.objects.map(item=>item.position[2]);
    const minX=Math.min(...xs),minZ=Math.min(...zs),extent=Math.max(Math.max(...xs)-minX,Math.max(...zs)-minZ,1);
    for(const object of staged.objects){const circle=document.createElementNS(ns,'circle');circle.setAttribute('cx',String(35+(object.position[0]-minX)/extent*340));circle.setAttribute('cy',String(30+(object.position[2]-minZ)/extent*100));circle.setAttribute('r','7');circle.setAttribute('fill','#a0e1c3');const title=document.createElementNS(ns,'title');title.textContent=object.name;circle.append(title);svg.append(circle);}
    map.append(svg);openDialog('#import-dialog');
    return new Promise((resolve,reject)=>{
      let settled=false,committing=false;
      const commit=$('#import-commit'),cancel=$('#import-cancel');
      function cleanup(){commit.removeEventListener('click',save);cancel.removeEventListener('click',close);dialog.removeEventListener('close',closed);dialog.removeEventListener('cancel',cancelling);}
      function close(){if(!committing)dialog.close();}
      function cancelling(event){if(committing)event.preventDefault();}
      async function closed(){if(settled)return;settled=true;cleanup();try{await request(`/api/imports/${staged.id}`,{method:'DELETE',headers:{'X-CSRF-Token':state().account.csrf}});}catch{}reject(new Error('ยกเลิกนำเข้า ZIP'));}
      async function save(){
        if(committing)return;committing=true;dialog.querySelectorAll('button').forEach(button=>{button.disabled=true;});
        try{
          const saved=await request(`/api/imports/${staged.id}/commit`,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':state().account.csrf},body:JSON.stringify({name:$('#import-name').value,project_id:project.id})});
          settled=true;cleanup();dialog.close();resolve(saved);
        }catch(error){$('#import-error').textContent=error.message;}finally{committing=false;dialog.querySelectorAll('button').forEach(button=>{button.disabled=false;});}
      }
      commit.addEventListener('click',save);cancel.addEventListener('click',close);dialog.addEventListener('close',closed);dialog.addEventListener('cancel',cancelling);
    });
  }
  return {update,attach,resetSelection,ready,previewImport};
}
