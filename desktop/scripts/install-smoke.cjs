// Real per-user install/upgrade. Keeps all QA data in an explicitly owned workspace.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const {spawnSync}=require('node:child_process');
const {extractFile}=require('@electron/asar');
const {Backend}=require('../src/backend.cjs');
const root=path.resolve(__dirname,'../..');
const installed=path.join(process.env.LOCALAPPDATA,'Programs/360 Studio');
const target=require('../package.json').version;
const installer=path.join(root,`desktop/dist/360 Studio Setup ${target}.exe`);
const base=path.join(root,'tools/desktop-qa');
function snapshot(folder){
 const result={};
 function walk(directory){if(!fs.existsSync(directory))return;for(const entry of fs.readdirSync(directory,{withFileTypes:true})){
  const file=path.join(directory,entry.name);if(entry.isDirectory())walk(file);else result[path.relative(folder,file)]=crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
 }}
 for(const name of ['uploads','jobs','models'])walk(path.join(folder,'web_data',name));return result;
}
(async()=>{
 const from=JSON.parse(extractFile(path.join(installed,'resources/app.asar'),'package.json')).version;
 const evidence=path.join(base,'install-upgrade.json');
 if(fs.existsSync(evidence)){
  const previous=JSON.parse(fs.readFileSync(evidence,'utf8'));
  const archive=path.join(base,`install-upgrade-${previous.from}-${previous.to}.json`);
  if(!fs.existsSync(archive))fs.copyFileSync(evidence,archive);
 }
 const backend=new Backend({root,packaged:true,resourcesPath:path.join(installed,'resources'),
  userData:path.join(base,'installer-guard-profile'),workspace:path.join(base,'installer-guard-workspace')});
 await backend.start();
 const job=path.join(backend.workspace,'web_data/jobs/installer-qa-busy');fs.mkdirSync(job,{recursive:true});
 fs.writeFileSync(path.join(job,'.task.lock'),JSON.stringify({pid:process.pid,token:'qa'}));
 try{
  assert.equal((await backend.busy()).busy,true);
  const blocked=spawnSync(installer,['/S'],{windowsHide:true,timeout:30000});
  assert.equal(blocked.status,2,'Installer must refuse a live app/backend, without killing it');
  assert.equal((await backend.busy()).busy,true);
 }finally{
  fs.unlinkSync(path.join(job,'.task.lock'));fs.rmdirSync(job);await backend.shutdown();
 }
 const workspace=path.join(base,'electron-installed/workspace'),before=snapshot(workspace);
 assert.ok(Object.keys(before).length>0,'Run installed --exercise smoke before the upgrade');
 const applied=spawnSync(installer,['/S'],{windowsHide:true,timeout:300000});
 assert.equal(applied.status,0,applied.error?.message||'Upgrade failed');
 assert.deepEqual(snapshot(workspace),before,'Upgrade changed an existing source/job/model');
 fs.writeFileSync(evidence,JSON.stringify({state:'passed',from,to:target,
  active_backend_install_blocked:true,installer_exit:applied.status,preserved_files:Object.keys(before).length,workspace},null,2));
 console.log(`Installer guard and real ${from} -> ${target} install passed; existing workspace bytes preserved`);
})().catch(error=>{console.error(error);process.exitCode=1;});
