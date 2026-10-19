// End-to-end Cache Storage check against local Docker Compose using Chromium CDP.
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, join, resolve, sep } from 'node:path';
import { randomBytes } from 'node:crypto';

const base = 'http://127.0.0.1:8088';
const cwd = import.meta.dirname;
const user = `chromium_${randomBytes(6).toString('hex')}`;
const password = randomBytes(24).toString('base64url');
const sampleMB = Number(process.env.CHROMIUM_TEST_MB || 8);
const format = process.env.CHROMIUM_TEST_FORMAT || 'glb';
if (!['glb', 'spz'].includes(format)) throw new Error('CHROMIUM_TEST_FORMAT must be glb or spz');
if (!Number.isInteger(sampleMB) || sampleMB < 0 || sampleMB > 512) throw new Error('CHROMIUM_TEST_MB must be 0–512');
const chrome = process.env.CHROME_PATH || (process.platform === 'win32'
  ? 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe' : 'chromium');
const profile = mkdtempSync(join(tmpdir(), 'glb-portal-chromium-'));
let browserProcess;
let cdp;

function dockerPython(code, input) {
  const result = spawnSync('docker', ['compose', 'exec', '-T', 'portal', 'python', '-c', code],
    { cwd, input: JSON.stringify(input), encoding: 'utf8', windowsHide: true });
  if (result.status !== 0) throw new Error(result.stderr || result.error?.message || 'Docker command failed');
  return result.stdout.trim();
}

function glbBytes() {
  const binSize = sampleMB * 1048576;
  const json = Buffer.from(JSON.stringify({ asset: { version: '2.0' }, scenes: [{ nodes: [] }], scene: 0, ...(binSize ? { buffers: [{ byteLength: binSize }] } : {}) }));
  const padding = Buffer.alloc((4 - json.length % 4) % 4, 32);
  const header = Buffer.alloc(20);
  header.write('glTF', 0); header.writeUInt32LE(2, 4); header.writeUInt32LE(20 + json.length + padding.length + (binSize ? 8 + binSize : 0), 8);
  header.writeUInt32LE(json.length + padding.length, 12); header.write('JSON', 16);
  if (!binSize) return Buffer.concat([header, json, padding]);
  const binHeader = Buffer.alloc(8);
  binHeader.writeUInt32LE(binSize, 0); binHeader.write('BIN\0', 4);
  return Buffer.concat([header, json, padding, binHeader, Buffer.alloc(binSize)]);
}

async function waitFor(check, timeout = 30000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    try { if (await check()) return; } catch { /* Navigation may replace the JS context. */ }
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw new Error('Timed out waiting for Chromium');
}

class Cdp {
  constructor(url) {
    this.socket = new WebSocket(url);
    this.nextId = 0;
    this.pending = new Map();
    this.errors = [];
    this.socket.addEventListener('message', event => {
      const data = JSON.parse(event.data);
      if (data.method === 'Runtime.exceptionThrown') this.errors.push(data.params.exceptionDetails.exception?.description || data.params.exceptionDetails.text);
      if (data.method === 'Runtime.consoleAPICalled' && data.params.type === 'error') this.errors.push(data.params.args.map(arg => arg.description || arg.value).join(' '));
      if (!data.id) return;
      const pending = this.pending.get(data.id);
      if (!pending) return;
      this.pending.delete(data.id);
      data.error ? pending.reject(new Error(data.error.message)) : pending.resolve(data.result);
    });
  }
  async open() {
    await new Promise((resolve, reject) => {
      this.socket.addEventListener('open', resolve, { once: true });
      this.socket.addEventListener('error', reject, { once: true });
    });
  }
  send(method, params = {}) {
    const id = ++this.nextId;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }
  async evaluate(expression) {
    const result = await this.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
    return result.result.value;
  }
}

try {
  if (!existsSync(chrome) && process.platform === 'win32') throw new Error(`Chromium browser not found: ${chrome}`);
  const create = `import json,sys,server
x=json.load(sys.stdin); server.init_db(); db=server.connect()
db.execute('INSERT INTO users VALUES(?,?,?,?)',(x['user'],server.password_hash(x['password']),'admin',server.now()))
db.commit(); db.close()`;
  dockerPython(create, { user, password });
  const login = await fetch(`${base}/api/login`, { method: 'POST', headers: { Origin: base, 'Content-Type': 'application/json' }, body: JSON.stringify({ username: user, password }) });
  if (login.status !== 200) throw new Error(`HTTP login failed: ${login.status}`);
  const cookie = login.headers.get('set-cookie').split(';')[0];
  const me = await (await fetch(`${base}/api/me`, { headers: { Cookie: cookie } })).json();
  const bytes = format === 'spz' ? readFileSync(join(cwd, 'test_splat.spz')) : glbBytes();
  const upload = await fetch(`${base}/api/models`, { method: 'POST', headers: { Origin: base, Cookie: cookie, 'X-CSRF-Token': me.csrf, 'X-Filename': `chromium-test.${format}` }, body: bytes });
  if (upload.status !== 201) throw new Error(`Model upload failed: ${upload.status}`);
  const modelId = (await upload.json()).id;

  browserProcess = spawn(chrome, ['--headless=new', '--no-first-run', '--no-default-browser-check', '--enable-unsafe-swiftshader', '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'],
    { stdio: 'ignore', windowsHide: true });
  const activePort = join(profile, 'DevToolsActivePort');
  await waitFor(() => existsSync(activePort), 15000);
  const port = Number(readFileSync(activePort, 'utf8').split('\n')[0]);
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const page = targets.find(target => target.type === 'page');
  if (!page) throw new Error('No Chromium page target');
  cdp = new Cdp(page.webSocketDebuggerUrl);
  await cdp.open();
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  await cdp.send('Page.navigate', { url: base });
  await waitFor(() => cdp.evaluate('document.documentElement.dataset.portalReady === "1"'));
  await cdp.evaluate(`document.querySelector('[name=username]').value=${JSON.stringify(user)}; document.querySelector('[name=password]').value=${JSON.stringify(password)}; document.querySelector('#login-form').requestSubmit(); true`);
  await waitFor(() => cdp.evaluate('document.querySelector("#workspace")?.hidden === false'));
  await waitFor(() => cdp.evaluate('!!navigator.serviceWorker.controller'));
  try {
    await waitFor(() => cdp.evaluate(`document.querySelector('#render-info')?.textContent?.includes(${JSON.stringify(format.toUpperCase())}) && document.querySelector('#viewport-status')?.hidden === true`));
  } catch (error) {
    const state = await cdp.evaluate(`({status:document.querySelector('#viewport-status')?.textContent, info:document.querySelector('#render-info')?.textContent, models:document.querySelector('#model-count')?.textContent, canvas:document.querySelector('#canvas-host')?.innerHTML.slice(0,300)})`);
    throw new Error(`Viewer did not render ${format}: ${JSON.stringify(state)}; errors=${JSON.stringify(cdp.errors)}`, { cause: error });
  }
  const modelUrl = `/api/models/${modelId}/file`;
  const loaded = await cdp.evaluate(`(async () => { const r = await fetch(${JSON.stringify(modelUrl)}); return [r.status, (await r.arrayBuffer()).byteLength]; })()`);
  if (loaded[0] !== 200 || loaded[1] !== bytes.length) throw new Error(`First Chromium load failed: ${loaded}`);
  await waitFor(() => cdp.evaluate(`(async () => !!(await (await caches.open('glb-portal-models-v1')).match(${JSON.stringify(modelUrl)})))()`));
  await cdp.send('Page.reload');
  await waitFor(() => cdp.evaluate('document.querySelector("#workspace")?.hidden === false'));
  await cdp.evaluate('window.__modelCacheEvents=[]; navigator.serviceWorker.addEventListener("message", e => window.__modelCacheEvents.push(e.data)); true');
  const reused = await cdp.evaluate(`(async () => { const r = await fetch(${JSON.stringify(modelUrl)}); return [r.status, (await r.arrayBuffer()).byteLength]; })()`);
  await waitFor(() => cdp.evaluate(`window.__modelCacheEvents?.some(e => e.type === 'model-cache' && e.modelId === ${JSON.stringify(modelId)} && e.status === 'hit')`));
  if (reused[0] !== 200 || reused[1] !== bytes.length) throw new Error(`Cached Chromium load failed: ${reused}`);
  const denied = await cdp.evaluate(`(async () => { const me = await (await fetch('/api/me')).json(); await fetch('/api/logout', { method: 'POST', headers: { 'X-CSRF-Token': me.csrf } }); return (await fetch(${JSON.stringify(modelUrl)})).status; })()`);
  if (denied !== 401) throw new Error(`Cached model escaped logout: HTTP ${denied}`);
  console.log(`Chromium (${format.toUpperCase()}, ${bytes.length} bytes): viewer, cache save, cache hit after reload, and logout protection OK`);
} finally {
  try { await cdp?.send('Browser.close'); } catch { /* Browser may already be closed. */ }
  cdp?.socket.close();
  browserProcess?.kill();
  const cleanup = `import json,sys,server
name=json.load(sys.stdin); db=server.connect()
for row in db.execute('SELECT id,filename FROM models WHERE uploaded_by=?',(name,)).fetchall():
    if server.MODEL_ID.fullmatch(row['id']) and row['filename'] in (row['id']+'.glb',row['id']+'.spz'):
        (server.DATA/'models'/row['filename']).unlink(missing_ok=True)
db.execute('DELETE FROM models WHERE uploaded_by=?',(name,))
db.execute('DELETE FROM sessions WHERE username=?',(name,))
db.execute('DELETE FROM login_log WHERE username=?',(name,))
db.execute('DELETE FROM users WHERE username=?',(name,))
db.commit(); db.close()`;
  try { dockerPython(cleanup, user); } catch (error) { console.error(`Temporary test account cleanup failed: ${error.message}`); }
  const tempRoot = resolve(tmpdir()) + sep;
  const safeProfile = resolve(profile);
  if (safeProfile.startsWith(tempRoot) && basename(safeProfile).startsWith('glb-portal-chromium-')) {
    try { rmSync(safeProfile, { recursive: true, force: true }); } catch { /* Chrome may still be closing. */ }
  }
}
