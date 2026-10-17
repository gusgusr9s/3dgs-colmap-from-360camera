// Progressive UI enhancements. Existing routes, forms and control IDs stay intact.
const body = document.body;
if (body.classList.contains('library-editor')) {
  const resources = [...document.querySelectorAll('.content .resource')];
  const search = document.querySelector('#library-search');
  const sort = document.querySelector('#library-sort');
  const clear = document.querySelector('#library-clear');
  if(sort)for(const option of sort.options)if(option.value.startsWith('size-'))
    option.textContent=(location.pathname==='/jobs'?'จำนวน Views':'ขนาดไฟล์')+(option.value==='size-desc'?'มาก → น้อย':'น้อย → มาก');
  const count = document.querySelector('#library-search-count');
  const tabs = [...document.querySelectorAll('[data-model-tab]')];
  const initialOrder=new Map(resources.map((row,index)=>[row,index]));
  function orderRows() {
    const mode=sort?.value || 'original';
    const lists=new Set(resources.map(row=>row.parentElement).filter(Boolean));
    const numeric=(row,key)=>{const number=Number(row.dataset[key]);return Number.isFinite(number)?number:Date.parse(row.dataset[key])||0;};
    const title=row=>(row.dataset.orderName||row.querySelector('.resource-title')?.textContent||row.textContent).trim();
    for(const list of lists) {
      const rows=resources.filter(row=>row.parentElement===list);
      // History headings stay attached to their original Views groups.
      if(list.querySelector(':scope > .view-group'))continue;
      rows.sort((a,b)=>{
        let result=0;
        if(mode==='recent'||mode==='oldest')result=(numeric(a,'orderTime')-numeric(b,'orderTime'))*(mode==='recent'?-1:1);
        if(mode==='size-desc'||mode==='size-asc')result=(numeric(a,'orderSize')-numeric(b,'orderSize'))*(mode==='size-desc'?-1:1);
        if(mode==='name-asc'||mode==='name-desc')result=title(a).localeCompare(title(b),'th',{numeric:true,sensitivity:'base'})*(mode==='name-desc'?-1:1);
        return result||initialOrder.get(a)-initialOrder.get(b);
      });
      for(const row of rows)list.append(row);
    }
  }
  function saveCollectionURL() {
    if(typeof URLSearchParams==='undefined')return;
    const params=new URLSearchParams(location.search);
    if(search?.value.trim())params.set('q',search.value.trim());else params.delete('q');
    if(sort?.value&&sort.value!=='recent')params.set('sort',sort.value);else params.delete('sort');
    const query=params.toString();history.replaceState(null,'',location.pathname+(query?'?'+query:'')+location.hash);
  }
  function filter() {
    const query = (search?.value || '').trim().toLocaleLowerCase();
    let visible = 0;
    let total = 0;
    for (const row of resources) {
      row.hidden = !!query && !row.textContent.toLocaleLowerCase().includes(query);
      if (!row.closest('[role="tabpanel"]')?.hidden) {
        ++total;
        if (!row.hidden) ++visible;
      }
    }
    if (count) count.textContent = total ? `${visible} / ${total} รายการ` : '';
    document.querySelector('#library-no-results')?.toggleAttribute('hidden', visible !== 0 || total === 0);
    if(clear)clear.hidden=!query;
  }
  function activateTab(tab, updateURL = true, focus = false) {
    for (const menu of document.querySelectorAll('.resource-menu[open]')) menu.open = false;
    for (const button of tabs) {
      const selected = button === tab;
      button.setAttribute('aria-selected', String(selected)); button.tabIndex = selected ? 0 : -1;
      const panel = document.getElementById(button.dataset.modelTab);
      if (panel) panel.hidden = !selected;
    }
    const panel = document.getElementById(tab.dataset.modelTab);
    for (const image of panel?.querySelectorAll('[data-preview-src]') || []) {
      image.src = image.dataset.previewSrc; image.removeAttribute('data-preview-src');
    }
    if (updateURL) history.replaceState(null, '', location.pathname + location.search + '#' + tab.dataset.modelTab);
    if (focus) tab.focus();
    filter();
  }
  if (tabs.length) {
    function tabFromURL() { return tabs.find(tab => '#' + tab.dataset.modelTab === location.hash) || tabs[0]; }
    activateTab(tabFromURL(), false);
    for (const tab of tabs) {
      tab.addEventListener('click', () => activateTab(tab));
      tab.addEventListener('keydown', event => {
        const index = tabs.indexOf(tab);
        const next = event.key === 'ArrowRight' ? (index + 1) % tabs.length : event.key === 'ArrowLeft' ? (index - 1 + tabs.length) % tabs.length : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : null;
        if (next !== null) { event.preventDefault(); activateTab(tabs[next], true, true); }
      });
    }
    for (const button of document.querySelectorAll('[data-model-tab-step]')) button.addEventListener('click', () => {
      const active = tabs.findIndex(tab => tab.getAttribute('aria-selected') === 'true');
      activateTab(tabs[(active + Number(button.dataset.modelTabStep) + tabs.length) % tabs.length]);
    });
    addEventListener('hashchange', () => activateTab(tabFromURL(), false));
  }
  if(typeof URLSearchParams!=='undefined') {
    const params=new URLSearchParams(location.search);
    if(search)search.value=params.get('q')||'';
    if(sort&&[...sort.options].some(o=>o.value===params.get('sort')))sort.value=params.get('sort');
  }
  search?.addEventListener('input', ()=>{filter();saveCollectionURL();});
  sort?.addEventListener('change',()=>{orderRows();saveCollectionURL();});
  clear?.addEventListener('click',()=>{search.value='';filter();saveCollectionURL();search.focus();});
  orderRows();
  filter();
  for (const row of resources) {
    const actions = row.querySelector(':scope > .actions');
    if (!actions || actions.children.length <= 3) continue;
    const children=[...actions.children];
    const main=children.find(child=>(child.matches('button')&&child.matches('.primary'))||child.querySelector('.primary')?.matches('button'))||
      children.find(child=>child.matches('.primary'))||children[0];
    const secondary=children.find(child=>child!==main&&(main.matches('a')?child.matches('a[download]'):child.matches('a')));
    const keep=new Set([main,...(secondary?[secondary]:[])]);
    const extra = [...actions.children].filter(child => !keep.has(child));
    if (!extra.length) continue;
    const details = document.createElement('details'); details.className = 'resource-menu';
    const summary = document.createElement('summary'); summary.textContent = '⋯ เพิ่มเติม';
    summary.setAttribute('aria-label', 'เครื่องมือเพิ่มเติมสำหรับรายการนี้');
    const menu = document.createElement('div'); menu.className = 'resource-menu-items';
    const groups=[['เปิดหน้าอื่น',[]],['ดาวน์โหลด',[]],['จัดการ',[]],['ลบรายการ',[]]];
    for(const child of extra) {
      const danger=child.matches('.danger')||child.querySelector('.danger');
      groups[danger?3:child.matches('a[download]')?1:child.matches('a')?0:2][1].push(child);
    }
    for(const [label,items] of groups)if(items.length) {
      const section=document.createElement('div');section.className='resource-menu-group';
      const heading=document.createElement('span');heading.className='action-group-label';heading.textContent=label;
      section.append(heading,...items);menu.append(section);
    }
    details.append(summary, menu);actions.append(main);if(secondary)actions.append(secondary);actions.append(details);
    details.addEventListener('toggle', () => {
      if (details.open) {
        for (const other of document.querySelectorAll('.resource-menu[open]')) if (other !== details) other.open = false;
        if(typeof menu.getBoundingClientRect==='function') {
          menu.style.position='fixed';const rect=summary.getBoundingClientRect(),size=menu.getBoundingClientRect();
          menu.style.left=Math.max(12,Math.min(innerWidth-size.width-12,rect.right-size.width))+'px';
          menu.style.top=Math.max(12,Math.min(innerHeight-size.height-12,rect.bottom+6))+'px';menu.style.right='auto';
        }
      }
    });
    menu.addEventListener('click', event => { if (event.target.closest('button,a')) details.open = false; });
  }
  document.addEventListener('click', event => {
    for (const menu of document.querySelectorAll('.resource-menu[open]')) if (!menu.contains(event.target)) menu.open = false;
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') for (const menu of document.querySelectorAll('.resource-menu[open]')) { menu.open = false; menu.querySelector('summary')?.focus(); }
  });
  const views = [...document.querySelectorAll('[data-library-view]')];
  function setView(view) {
    body.classList.toggle('library-grid', view === 'grid');
    for (const button of views) button.setAttribute('aria-pressed', String(button.dataset.libraryView === view));
    try { localStorage.setItem('360studio-library-view', view); } catch {}
  }
  for (const button of views) button.addEventListener('click', () => setView(button.dataset.libraryView));
  let saved = 'list';
  try { saved = localStorage.getItem('360studio-library-view') || saved; } catch {}
  setView(saved === 'grid' ? 'grid' : 'list');
}
if (body.classList.contains('pose-editor')) {
  const toggle = document.querySelector('#pose-inspector-toggle');
  const panel = document.querySelector('#pose-inspector');
  const media=matchMedia('(min-width:901px)');let open=media.matches;
  function apply() {
    body.classList.toggle('pose-inspector-closed', !open);
    toggle?.setAttribute('aria-expanded', String(open));
    if (panel) panel.inert = !open;
    dispatchEvent(new Event('resize'));
  }
  toggle?.addEventListener('click', () => { open = !open; apply(); });
  media.addEventListener?.('change',event=>{open=event.matches;apply();});
  apply();
}
if (body.classList.contains('model-editor')) {
  const inspector = document.querySelector('#inspector-toggle');
  const focus = document.querySelector('#focus-toggle');
  const panel = document.querySelector('#editor-inspector');
  const media=matchMedia('(min-width:901px)');let open=media.matches;
  function apply() {
    body.classList.toggle('inspector-open', open);
    body.classList.toggle('inspector-closed', !open);
    inspector?.setAttribute('aria-pressed', String(open));
    if (panel) panel.inert = !open || body.classList.contains('editor-focus');
    dispatchEvent(new Event('resize'));
  }
  inspector?.addEventListener('click', () => {
    if (body.classList.contains('editor-focus')) {
      body.classList.remove('editor-focus'); focus?.setAttribute('aria-pressed','false'); open = true;
    } else open = !open;
    apply();
  });
  focus?.addEventListener('click', () => {
    const enabled = body.classList.toggle('editor-focus');
    focus.setAttribute('aria-pressed', String(enabled));
    apply();
  });
  media.addEventListener?.('change',event=>{open=event.matches;apply();});
  apply();
}
