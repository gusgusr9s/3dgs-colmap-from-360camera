// Step changes only affect the form. Processing starts with explicit actions.
document.querySelectorAll('form[data-settings-source]').forEach(form=>{
  const wizard=form.querySelector('[data-capture-wizard]');
  if(!wizard)return;
  const panels=[...wizard.querySelectorAll('[data-wizard-step]')];
  const tabs=[...wizard.querySelectorAll('.capture-steps [data-wizard-go]')];
  const submits=[...form.elements].filter(input=>input.type==='submit');
  const footer=form.closest('dialog')?.querySelector('.modal-actions');
  let previous,next;
  if(footer){
    previous=document.createElement('button');previous.type='button';previous.textContent='← ย้อนกลับ';
    next=document.createElement('button');next.type='button';next.className='primary';
    footer.insertBefore(previous,submits[0]||null);footer.insertBefore(next,submits[0]||null);
    previous.addEventListener('click',()=>navigate(step-1));next.addEventListener('click',()=>navigate(step+1));
    wizard.querySelectorAll('.wizard-navigation').forEach(node=>{node.hidden=true;});
  }
  let step=1;
  function review(){
    const field=key=>form.elements.namedItem(key);
    const target=field('pipeline_mode');
    const preview={ready:'มีผลทดสอบแล้ว · ตรวจช่วงเลี้ยวเพิ่มเติมก่อนใช้ทั้งคลิป',stale:'ค่าเปลี่ยนหลังทดสอบ · ควรทดสอบใหม่',running:'กำลังทดสอบแผนที่',error:'ทดสอบยังไม่สำเร็จ'}[form.dataset.previewState]||'ยังไม่ได้ทดสอบแผนที่';
    wizard.querySelector('[data-wizard-review]').textContent=
      `เก็บทุก ${field('sample_every').value} เฟรม · ${field('sampling_guard').value==='manual'?'ใช้ค่าที่กรอกจริง':'จำกัดระยะเวลาอัตโนมัติ'} · ${target.options[target.selectedIndex]?.textContent||''}\n${preview}`;
  }
  function show(stepNumber,focus=false){
    step=Math.max(1,Math.min(3,Number(stepNumber)));
    panels.forEach(panel=>{panel.hidden=Number(panel.dataset.wizardStep)!==step;});
    tabs.forEach(tab=>{
      const current=Number(tab.dataset.wizardGo)===step;
      if(current)tab.setAttribute('aria-current','step');else tab.removeAttribute('aria-current');
    });
    submits.forEach(button=>{button.hidden=step!==3;});
    if(previous){previous.hidden=step===1;next.hidden=step===3;next.textContent=step===1?'ถัดไป · ทดสอบแผนที่ →':'ถัดไป · ขั้นสูงและสร้าง →';}
    form.dataset.wizardStep=String(step);review();
    if(focus){const heading=panels[step-1].querySelector('h3');heading.tabIndex=-1;heading.focus({preventScroll:true});heading.scrollIntoView({block:'nearest',behavior:'smooth'});}
  }
  function navigate(next){
    if(next>step){
      const invalid=[...panels[step-1].querySelectorAll('input,select')].find(input=>!input.checkValidity());
      if(invalid){invalid.reportValidity();return;}
    }
    show(next,true);
  }
  wizard.querySelectorAll('[data-wizard-go]').forEach(button=>button.addEventListener('click',()=>navigate(Number(button.dataset.wizardGo))));
  form.addEventListener('invalid',event=>{
    const panel=event.target.closest('[data-wizard-step]');
    if(!panel)return;
    show(panel.dataset.wizardStep);
    for(let parent=event.target.parentElement;parent&&parent!==form;parent=parent.parentElement)if(parent.tagName==='DETAILS')parent.open=true;
  },true);
  form.addEventListener('submit',event=>{
    if(step<3){event.preventDefault();show(step+1,true);}
  });
  for(const event of ['input','change','preview-state'])form.addEventListener(event,review);
  show(1);
});
