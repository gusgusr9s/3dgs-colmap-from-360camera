// End-to-end QA of an actual Electron process, with a private workspace/profile.
const { _electron }=require('playwright-core');
const fs=require('node:fs');const path=require('node:path');const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..');
const args=process.argv.slice(2),exeIndex=args.indexOf('--exe');
const executablePath=exeIndex>=0?path.resolve(args[exeIndex+1]):require('electron');
const packaged=exeIndex>=0;
const label=args.includes('--installed')?'installed':packaged?'packaged':'development';
const output=path.join(root,'tools/desktop-qa/electron-'+label);fs.mkdirSync(output,{recursive:true});
const profile=path.join(output,'profile'),workspace=path.join(output,'workspace');
const wait=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 let electron;
 try{
  const env={...process.env,STUDIO_DESKTOP_DATA:profile,STUDIO_WORKSPACE:workspace,STUDIO_HEADLESS:'1'};
  // Installed runtime must find its own Python/FFmpeg/Node/COLMAP/Brush.
  if(packaged)env.PATH=path.join(process.env.SystemRoot,'System32');
  if(args.includes('--exercise'))env.VIEW_DEVICE='cpu';
  delete env.ELECTRON_RUN_AS_NODE;
  electron=await _electron.launch({executablePath,args:packaged?[]:[path.join(root,'desktop')],cwd:root,env,timeout:90000});
  const page=await electron.firstWindow(),errors=[];page.on('pageerror',error=>errors.push(String(error)));
  await page.waitForURL('http://127.0.0.1:*/',{timeout:90000});
  const record=JSON.parse(fs.readFileSync(path.join(profile,'backend.json'),'utf8'));
  const url=`http://127.0.0.1:${record.port}`;
  const headers={'X-Studio-Token':record.token};
  async function request(route,method='GET'){
   const response=await fetch(url+route,{method,headers});return {status:response.status,body:await response.json()};
  }
  let verification;
  for(let i=0;i<90;i++){verification=(await request('/api/runtime-verification')).body;if(verification.state!=='running')break;await wait(1000);}
  assert.equal(verification.state,'done',JSON.stringify(verification));assert.equal(verification.ready,true,JSON.stringify(verification));
  assert.equal((await fetch(url+'/api/desktop/health')).status,403);
  const preferences=await electron.evaluate(({BrowserWindow})=>{const w=BrowserWindow.getAllWindows()[0];const p=w.webContents.getLastWebPreferences();return {sandbox:p.sandbox,contextIsolation:p.contextIsolation,nodeIntegration:p.nodeIntegration};});
  assert.deepEqual(preferences,{sandbox:true,contextIsolation:true,nodeIntegration:false});
  for(const route of ['/','/sources','/jobs','/models','/tutorial','/project-overview']){
   const response=await page.goto(url+route);assert.equal(response.status(),200);await wait(250);
  }
  let workflow;
  if(args.includes('--exercise')){
    const version=await electron.evaluate(({app})=>app.getVersion());
    const filename=`desktop-source-${version.replaceAll('.','')}.png`;
    await page.goto(url+'/sources');
    const upload=path.join(workspace,'web_data/uploads',filename);
    if(!fs.existsSync(upload)){
      await page.locator('button[data-modal="upload-source"]').first().click();
      await page.locator('#upload-source-form input[type=file]').setInputFiles({name:filename,mimeType:'image/png',buffer:fs.readFileSync(path.join(root,'desktop/tests/fixtures/panorama.png'))});
      await page.locator('button[form="upload-source-form"]').click();
      await page.getByRole('link',{name:filename,exact:true}).first().waitFor();
    }
    const submitted=await page.evaluate(async filename=>{
      const data=new URLSearchParams({action:'generate',source:'uploads:'+filename,width:'160',height:'160',views:'4',
        fov:'90',quality:'preview',sample_every:'1',projection:'panorama',auto_model:'0',label:'Desktop QA'});
      const result=await fetch('/actions',{method:'POST',body:data});return result.url;
    },filename);
    const job=new URL(submitted).pathname.split('/').pop();assert.match(job,/^[a-f0-9]{10}$/);
    let status;
    for(let i=0;i<120;i++){status=(await request('/api/job/'+job)).body;if(['done','ready','error'].includes(status.state))break;await wait(250);}
    assert.equal(status.state,'ready',JSON.stringify(status));
    const transforms=JSON.parse(fs.readFileSync(path.join(workspace,'web_data/jobs',job,'transforms.json'),'utf8'));
    assert.equal(transforms.frames.length,4);
    const model=`desktop-model-${version.replaceAll('.','')}.ply`;
    await page.evaluate(async({name,bytes})=>{
      const data=new FormData();data.append('action','upload_model');data.append('return_to','/models');
      data.append('model',new File([Uint8Array.from(bytes)],name));
      const response=await fetch('/actions',{method:'POST',body:data});if(!response.ok)throw new Error('Model upload failed');
    },{name:model,bytes:[...fs.readFileSync(path.join(root,'desktop/tests/fixtures/splat.ply'))]});
    await page.goto(url+'/model/'+model);await wait(1800);
    assert.equal(await page.locator('canvas').count()>0,true);
    workflow={uploaded_source:filename,job,views:4,uploaded_model:model,viewer_canvas:true};
  }
  // A live owned job lock exercises the same busy check used by queued/native work.
  const fixture=path.join(workspace,'web_data/jobs/desktop-qa-busy');fs.mkdirSync(fixture,{recursive:true});
  const lock=path.join(fixture,'.task.lock');fs.writeFileSync(lock,JSON.stringify({pid:process.pid,token:'qa'}));
  assert.equal((await request('/api/desktop/prepare-quit','POST')).status,409);
  const busy=await request('/api/desktop/busy');assert.equal(busy.body.busy,true);
  await electron.evaluate(({dialog,BrowserWindow})=>{dialog.showMessageBox=async()=>({response:1});BrowserWindow.getAllWindows()[0].close();});
  await wait(500);assert.equal((await request('/api/desktop/health')).status,200);
  fs.unlinkSync(lock);fs.rmdirSync(fixture);
  await page.goto(url+'/');
  const screenshot=await electron.evaluate(async({BrowserWindow})=>{
    const image=await BrowserWindow.getAllWindows()[0].webContents.capturePage(undefined,{stayHidden:true,stayAwake:true});
    return image.toPNG().toString('base64');
  });
  fs.writeFileSync(path.join(output,'dashboard.png'),Buffer.from(screenshot,'base64'));
  assert.deepEqual(errors,[]);
  const result={state:'passed',label,version:await electron.evaluate(({app})=>app.getVersion()),verification,preferences,
    busy_shutdown_blocked:true,unauthenticated_request_blocked:true,renderer_errors:errors,workspace,port:record.port,workflow};
  fs.writeFileSync(path.join(output,'result.json'),JSON.stringify(result,null,2));
  await electron.close();electron=null;
  console.log(JSON.stringify({state:'passed',label,workspace,ready:verification.ready}));
 }finally{if(electron)await electron.close().catch(()=>{});}
})().catch(error=>{console.error(error);process.exitCode=1;});
