// Decorative line icons follow the main app's existing controls.
const paths={
 back:'M20 12H4m6-6-6 6 6 6',cube:'m12 3 9 5v8l-9 5-9-5V8zM3 8l9 5 9-5m-9 5v8',plus:'M12 5v14M5 12h14',
 save:'M4 3h13l4 4v14H3V3zM7 3v6h9V3M7 21v-8h10v8',download:'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5',upload:'M12 16V4m-5 5 5-5 5 5M4 16v5h16v-5',close:'m6 6 12 12M6 18 18 6',
 undo:'M9 4 3 10l6 6M3 10h10a7 7 0 0 1 7 7',redo:'m15 4 6 6-6 6M21 10H11a7 7 0 0 0-7 7',
 move:'M12 3v18M3 12h18m-12-6 3-3 3 3m-6 12 3 3 3-3M6 9l-3 3 3 3m12-6 3 3-3 3',rotate:'M20 8a8 8 0 1 0 0 8M20 3v5h-5',scale:'M4 10V4h6m4 16h6v-6M4 4l6 6m4 4 6 6',
 expand:'M9 3H3v6m12-6h6v6M3 15v6h6m6 0h6v-6',focus:'M8 3H3v5m13-5h5v5M3 16v5h5m8 0h5v-5M8 12a4 4 0 1 0 8 0 4 4 0 1 0-8 0',
 eye:'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12m13 0a3 3 0 1 1-6 0 3 3 0 0 1 6 0',eyeOff:'m3 3 18 18M10 5a12 12 0 0 1 12 7 16 16 0 0 1-3 4M6 6a16 16 0 0 0-4 6s4 7 10 7a12 12 0 0 0 5-1M9 9a4 4 0 0 0 6 6',
 settings:'M4 7h16M4 17h16M9 4v6m6 4v6',globe:'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18',camera:'M3 7h4l2-3h6l2 3h4v14H3zM12 10a4 4 0 1 0 0 8 4 4 0 0 0 0-8',
 walk:'M13 3h.01M10 8l3-2 3 4 4 1M4 12l4-1 2-3m3-2-2 8-5 7m5-7 5 1 2 6',floor:'m12 5 10 7-10 7-10-7zM5 16l7 5 7-5',wall:'M4 3h16v18H4zM4 9h16M4 15h16M10 3v6m4 0v6m-4 0v6',
 align:'M5 3v18m14-18v18M9 8h6M9 16h6m-3-5 3-3-3-3m0 14-3-3 3-3',ruler:'m3 16 13-13 5 5L8 21zM7 12l3 3m1-7 3 3m1-7 3 3',pointer:'m4 3 15 10-7 1-4 7z',crosshair:'M12 2v5m0 10v5M2 12h5m10 0h5M18 12a6 6 0 1 1-12 0 6 6 0 0 1 12 0',
 copy:'M8 8h13v13H8zM16 8V3H3v13h5',trash:'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7',reset:'M3 4v6h6M4 10a8 8 0 1 1 1 8',layers:'m12 3 10 6-10 6L2 9zM2 14l10 6 10-6M2 19l10 5 10-5',
 ground:'M3 20h18M12 3v13m-4-4 4 4 4-4',bounds:'M8 3H3v5m13-5h5v5M3 16v5h5m8 0h5v-5M7 7h10v10H7z',package:'m12 3 9 5v8l-9 5-9-5V8zM3 8l9 5 9-5m-9 5v8M7 5l9 5v5',file:'M14 2H4v20h16V8zM14 2v6h6M8 13h8m-8 4h8',
 pin:'M12 22s8-8 8-13a8 8 0 0 0-16 0c0 5 8 13 8 13m3-13a3 3 0 1 1-6 0 3 3 0 0 1 6 0',info:'M12 11v6m0-10h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',search:'M10 4a6 6 0 1 0 0 12 6 6 0 0 0 0-12m5 11 6 6',
};
function decorate(node){
 const name=node.dataset.icon;if(!paths[name])return;
 const existing=node.querySelector(':scope > .scene-icon');if(existing?.dataset.name===name)return;
 existing?.remove();const icon=document.createElementNS('http://www.w3.org/2000/svg','svg');
 for(const [key,value] of Object.entries({viewBox:'0 0 24 24',class:'scene-icon','aria-hidden':'true',focusable:'false'}))icon.setAttribute(key,value);
 icon.dataset.name=name;const path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('d',paths[name]);icon.append(path);node.prepend(icon);
}
function scan(node){if(node.nodeType!==1)return;if(node.dataset.icon)decorate(node);node.querySelectorAll('[data-icon]').forEach(decorate);}
export function setupSceneUI(){
 let dialogTrigger=null,panelTrigger=null;
 const panel=value=>{
  if(value)document.body.dataset.panel=value;else delete document.body.dataset.panel;
  document.querySelector('#panel-backdrop').hidden=!value;
  document.querySelectorAll('[data-panel-toggle]').forEach(button=>button.setAttribute('aria-expanded',String(button.dataset.panelToggle===value)));
 };
 const tab=(name,focus=false)=>{
  document.querySelectorAll('[data-inspector-tab]').forEach(button=>{const active=button.dataset.inspectorTab===name;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;if(active&&focus)button.focus();});
  for(const item of ['object','align','walk'])document.querySelector('#panel-'+item).hidden=item!==name;
 };
 document.querySelectorAll('[data-inspector-tab]').forEach(button=>{
  button.addEventListener('click',()=>tab(button.dataset.inspectorTab));
  button.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();event.stopPropagation();const names=['object','align','walk'],current=names.indexOf(button.dataset.inspectorTab);tab(event.key==='Home'?names[0]:event.key==='End'?names[2]:names[(current+(event.key==='ArrowRight'?1:2))%3],true);});
 });
 document.querySelectorAll('[data-dialog-open]').forEach(button=>button.addEventListener('click',()=>{
  const dialog=document.getElementById(button.dataset.dialogOpen);if(dialog.open)return;dialogTrigger=button;panel(null);
  document.querySelectorAll('.toolbar-menu[open]').forEach(menu=>menu.open=false);dialog.showModal();
  if(dialog.id==='library-dialog')document.querySelector('#asset-search').focus();
  if(dialog.id==='export-dialog')document.dispatchEvent(new CustomEvent('scene-export-opened'));
 }));
 document.querySelectorAll('dialog').forEach(dialog=>{
  dialog.addEventListener('click',event=>{if(event.target!==dialog)return;const rect=dialog.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)dialog.close();});
  dialog.addEventListener('close',()=>{dialogTrigger?.focus({preventScroll:true});dialogTrigger=null;});
 });
 document.querySelectorAll('[data-dialog-close]').forEach(button=>button.addEventListener('click',()=>button.closest('dialog').close()));
 document.querySelectorAll('[data-panel-toggle]').forEach(button=>button.addEventListener('click',()=>{panelTrigger=button;panel(document.body.dataset.panel===button.dataset.panelToggle?null:button.dataset.panelToggle);}));
 const closePanel=()=>{panel(null);panelTrigger?.focus({preventScroll:true});};
 document.querySelectorAll('[data-panel-close]').forEach(button=>button.addEventListener('click',closePanel));
 document.querySelector('#panel-backdrop').addEventListener('click',closePanel);
 document.addEventListener('click',event=>document.querySelectorAll('.toolbar-menu[open]').forEach(menu=>{if(!menu.contains(event.target))menu.open=false;}));
 document.addEventListener('keydown',event=>{
  if(event.key!=='Escape')return;
  const dialog=document.querySelector('dialog[open]'),menus=[...document.querySelectorAll('.toolbar-menu[open]')];
  if(dialog||menus.length||document.body.dataset.panel){event.preventDefault();event.stopImmediatePropagation();if(dialog)dialog.close();else if(menus.length)menus.forEach(menu=>menu.open=false);else closePanel();}
 },true);
 document.addEventListener('scene-assets-added',()=>document.querySelector('#library-dialog').close());
 for(const id of ['align-pick-source','align-pick-target','calibration-a','calibration-b'])document.getElementById(id).addEventListener('click',()=>panel(null));
 document.addEventListener('scene-point-picked',()=>{if(matchMedia('(max-width:900px)').matches){tab('align');panel('inspector');}});
 scan(document.body);
 new MutationObserver(records=>{for(const record of records){if(record.type==='attributes'){decorate(record.target);continue;}if(record.target.nodeType===1&&record.target.dataset.icon)decorate(record.target);for(const node of record.addedNodes)scan(node);}}).observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['data-icon']});
}
if(typeof document!=='undefined'&&document.querySelector('#scene-config'))setupSceneUI();
