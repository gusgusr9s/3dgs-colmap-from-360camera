// Progressive UI enhancements. Existing routes, forms and control IDs stay intact.
const body = document.body;
if (body.classList.contains('library-editor')) {
  const resources = [...document.querySelectorAll('.content .resource')];
  const search = document.querySelector('#library-search');
  const count = document.querySelector('#library-search-count');
  const tabs = [...document.querySelectorAll('[data-model-tab]')];
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
  search?.addEventListener('input', filter);
  filter();
  for (const row of resources) {
    const actions = row.querySelector(':scope > .actions');
    if (!actions || actions.children.length <= 3) continue;
    const keep = new Set();
    for (const child of [...actions.children]) {
      if (keep.size < 1 || child.matches('a[download],.primary') || child.querySelector('.primary')) keep.add(child);
    }
    const extra = [...actions.children].filter(child => !keep.has(child));
    if (!extra.length) continue;
    const details = document.createElement('details'); details.className = 'resource-menu';
    const summary = document.createElement('summary'); summary.textContent = '⋯';
    summary.setAttribute('aria-label', 'เครื่องมือเพิ่มเติมสำหรับรายการนี้');
    const menu = document.createElement('div'); menu.className = 'resource-menu-items';
    menu.append(...extra); details.append(summary, menu); actions.append(details);
    details.addEventListener('toggle', () => {
      if (details.open) for (const other of document.querySelectorAll('.resource-menu[open]')) if (other !== details) other.open = false;
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
  let open = matchMedia('(min-width:901px)').matches;
  function apply() {
    body.classList.toggle('pose-inspector-closed', !open);
    toggle?.setAttribute('aria-expanded', String(open));
    if (panel) panel.inert = !open;
    dispatchEvent(new Event('resize'));
  }
  toggle?.addEventListener('click', () => { open = !open; apply(); });
  apply();
}
if (body.classList.contains('model-editor')) {
  const inspector = document.querySelector('#inspector-toggle');
  const focus = document.querySelector('#focus-toggle');
  const panel = document.querySelector('#editor-inspector');
  let open = matchMedia('(min-width:901px)').matches;
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
  apply();
}
