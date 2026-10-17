// Run the real UI module against a small DOM to check interaction regressions.
// This does not simulate CSS layout or GPU rendering.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

class Element {
  constructor(tag = 'div', text = '') {
    this.tag = tag; this.text = text; this.children = []; this.parent = null;
    this.attributes = {}; this.listeners = {}; this.dataset = {}; this.hidden = false;
    const names = new Set();
    this.classList = { contains: n => names.has(n), add: n => names.add(n), remove: n => names.delete(n),
      toggle(n, force = !names.has(n)) { if (force) names.add(n); else names.delete(n); return force; } };
  }
  set className(value) { for (const name of value.split(' ')) this.classList.add(name); }
  get textContent() { return this.text + this.children.map(child => child.textContent).join(' '); }
  set textContent(value) { this.text = value; }
  append(...nodes) {
    for (const node of nodes) {
      if (node.parent) node.parent.children = node.parent.children.filter(child => child !== node);
      node.parent = this; this.children.push(node);
    }
  }
  matches(selector) {
    return selector.split(',').some(s => s === '[role="tabpanel"]' ? this.attributes.role === 'tabpanel' : s === '.primary' ? this.classList.contains('primary') : s === 'a[download]' ? this.tag === 'a' && 'download' in this.attributes : s === this.tag);
  }
  querySelector(selector) {
    return this.children.find(child => selector === ':scope > .actions' ? child.classList.contains('actions') : child.matches(selector)) ||
      (selector === '.primary' ? this.children.map(child => child.querySelector(selector)).find(Boolean) : null);
  }
  closest(selector) { return this.matches(selector) ? this : this.parent?.closest(selector); }
  contains(node) { return node === this || this.children.some(child => child.contains(node)); }
  setAttribute(key, value) { this.attributes[key] = value; }
  getAttribute(key) { return this.attributes[key]; }
  removeAttribute(key) { delete this.attributes[key]; if (key === 'data-preview-src') delete this.dataset.previewSrc; }
  querySelectorAll(selector) { return selector === '[data-preview-src]' ? this.children.filter(child => child.dataset.previewSrc) : []; }
  toggleAttribute(key, force) { if (force) this.attributes[key] = ''; else delete this.attributes[key]; }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
  fire(type, event = {}) { for (const handler of this.listeners[type] || []) handler({ target: this, ...event }); }
  focus() { this.focused = true; }
}
const script = fs.readFileSync(new URL('../editor_ui.mjs', import.meta.url), 'utf8');
function run(kind, nodes = {}, rows = [], desktop = true, hash = '') {
  const body = new Element(); body.classList.add(kind);
  const document = new Element(); document.body = body;
  document.createElement = tag => new Element(tag);
  document.querySelector = selector => nodes[selector];
  document.getElementById = id => nodes['#' + id];
  const menus = () => rows.flatMap(row => row.children.flatMap(a => a.children.filter(c => c.classList.contains('resource-menu'))));
  document.querySelectorAll = selector => selector === '.content .resource' ? rows : selector === '.resource-menu[open]' ? menus().filter(m => m.open) : selector === '[data-library-view]' ? nodes.views || [] : selector === '[data-model-tab]' ? nodes.tabs || [] : selector === '[data-model-tab-step]' ? nodes.steps || [] : [];
  const storage = new Map(); let resizes = 0;
  const location = { pathname: '/models', search: '?job=concourse', hash }, events = {};
  vm.runInNewContext(script, { document, localStorage: { getItem: k => storage.get(k), setItem: (k,v) => storage.set(k,v) },
    location, history: { replaceState: (_, __, url) => { location.hash = '#' + url.split('#')[1]; } }, addEventListener: (type, fn) => { events[type] = fn; },
    matchMedia: () => ({ matches: desktop }), Event: class { constructor(type) { this.type = type; } }, dispatchEvent: event => { if (event.type === 'resize') resizes++; } });
  return { body, document, menus, storage, location, events, get resizes() { return resizes; } };
}
const inspector = new Element(), focus = new Element(), panel = new Element();
const desktop = run('model-editor', { '#inspector-toggle': inspector, '#focus-toggle': focus, '#editor-inspector': panel });
assert.equal(panel.inert, false);
focus.fire('click'); assert.equal(panel.inert, true); assert(desktop.body.classList.contains('editor-focus'));
inspector.fire('click'); assert.equal(panel.inert, false); assert(!desktop.body.classList.contains('editor-focus'));
inspector.fire('click'); assert.equal(panel.inert, true); assert(desktop.body.classList.contains('inspector-closed'));
assert.equal(desktop.resizes, 4, 'each dock change updates the actual viewport size');
const mobilePanel = new Element(), mobileToggle = new Element();
run('model-editor', { '#editor-inspector': mobilePanel, '#inspector-toggle': mobileToggle }, [], false);
assert.equal(mobilePanel.inert, true); mobileToggle.fire('click'); assert.equal(mobilePanel.inert, false);
const posePanel = new Element(), poseToggle = new Element();
const pose = run('pose-editor', { '#pose-inspector': posePanel, '#pose-inspector-toggle': poseToggle }, [], false);
assert(posePanel.inert && pose.body.classList.contains('pose-inspector-closed'));
poseToggle.fire('click'); assert(!posePanel.inert); assert.equal(poseToggle.getAttribute('aria-expanded'),'true');
assert.equal(pose.resizes,2,'opening Camera Inspector resizes the canvas');

const row = new Element('article', 'Concourse 6K'), other = new Element('article', 'Corridor 2K');
const actions = new Element(); actions.classList.add('actions'); row.append(actions);
const open = new Element('a', 'Open model'), ply = new Element('a', 'PLY'), spz = new Element('a', 'SPZ');
ply.setAttribute('download',''); spz.setAttribute('download','');
const refine = new Element('button', 'Refine'), form = new Element('form'), train = new Element('button', 'Train');
train.classList.add('primary'); form.append(train);
let modalClicks = 0; refine.addEventListener('click', () => modalClicks++);
actions.append(open, ply, spz, refine, form);
const search = new Element(), count = new Element(), empty = new Element(), grid = new Element(), list = new Element();
grid.dataset.libraryView = 'grid'; list.dataset.libraryView = 'list';
const library = run('library-editor', { '#library-search': search, '#library-search-count': count, '#library-no-results': empty, views: [grid,list] }, [row,other]);
assert(!actions.children.includes(ply) && !actions.children.includes(spz), 'secondary downloads move into a labeled menu');
assert(actions.children.includes(form), 'primary training action stays visible');
assert(actions.children.includes(open), 'navigation stays beside the primary action');
assert.equal(library.menus().length, 1);
assert(library.menus()[0].contains(ply)&&library.menus()[0].contains(spz),'download actions remain reachable');
refine.fire('click'); assert.equal(modalClicks, 1, 'moving an action preserves its original listeners');
search.value = 'corridor'; search.fire('input'); assert(row.hidden); assert(!other.hidden); assert.equal(count.textContent, '1 / 2 รายการ');
search.value = 'absent'; search.fire('input'); assert(!('hidden' in empty.attributes));
search.value = ''; search.fire('input'); assert(!row.hidden && !other.hidden);
grid.fire('click'); assert(library.body.classList.contains('library-grid')); assert.equal(library.storage.get('360studio-library-view'), 'grid');
const menu = library.menus()[0]; menu.open = true; library.document.fire('keydown', { key: 'Escape' });
assert.equal(menu.open, false); assert(menu.querySelector('summary').focused);
const originalTab = new Element('button'), compressedTab = new Element('button'), previousTab = new Element('button');
originalTab.dataset.modelTab = 'models-original'; compressedTab.dataset.modelTab = 'compressed-models'; previousTab.dataset.modelTabStep = '-1';
const originalPanel = new Element(), compressedPanel = new Element();
for (const p of [originalPanel, compressedPanel]) p.setAttribute('role', 'tabpanel');
const fullRow = new Element('article', 'Concourse'), compressedRow = new Element('article', 'Corridor');
originalPanel.append(fullRow); compressedPanel.append(compressedRow);
const deferred = new Element('img'); deferred.dataset.previewSrc = '/api/resource-preview?kind=model'; compressedPanel.append(deferred);
const tabSearch = new Element(), tabCount = new Element(), noResults = new Element();
const tabLibrary = run('library-editor', { '#models-original': originalPanel, '#compressed-models': compressedPanel, '#library-search': tabSearch,
  '#library-search-count': tabCount, '#library-no-results': noResults, tabs: [originalTab,compressedTab], steps: [previousTab] }, [fullRow,compressedRow]);
assert.equal(compressedPanel.hidden, true); assert.equal(deferred.src, undefined, 'hidden tab never starts a thumbnail request');
assert.equal(tabCount.textContent, '1 / 1 รายการ');
compressedTab.fire('click'); assert(originalPanel.hidden && !compressedPanel.hidden); assert.equal(deferred.src, '/api/resource-preview?kind=model');
assert.equal(tabLibrary.location.hash, '#compressed-models');
tabSearch.value = 'Concourse'; tabSearch.fire('input'); assert.equal(tabCount.textContent, '0 / 1 รายการ');
let prevented = false;
compressedTab.fire('keydown', { key: 'ArrowLeft', preventDefault() { prevented = true; } });
assert(prevented && originalTab.focused); assert.equal(tabCount.textContent, '1 / 1 รายการ');
assert.equal(originalTab.tabIndex, 0); assert.equal(compressedTab.tabIndex, -1);
previousTab.fire('click'); assert(!compressedPanel.hidden, 'left arrow wraps to previous tab');
tabLibrary.location.hash = '#models-original'; tabLibrary.events.hashchange(); assert(!originalPanel.hidden);
const linked = run('library-editor', { '#models-original': originalPanel, '#compressed-models': compressedPanel, tabs: [originalTab,compressedTab] }, [], true, '#compressed-models');
assert(!compressedPanel.hidden && originalPanel.hidden, 'existing compressed-record links activate the compressed tab');
console.log('Editor interactions passed: tabs, URL links, keyboard/stepper, deferred images, scoped search, menus, downloads, card view, focus, inspector.');
