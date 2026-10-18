const {app,BrowserWindow,Menu,dialog,shell,session,ipcMain,Tray}=require('electron');
const path=require('node:path');
const fs=require('node:fs');
const {pathToFileURL}=require('node:url');
const {Backend,readJSON,writeJSON}=require('./backend.cjs');
const {canInstall,validFeed}=require('./update-policy.cjs');

if(process.env.STUDIO_DESKTOP_DATA)app.setPath('userData',path.resolve(process.env.STUDIO_DESKTOP_DATA));
const root=path.resolve(__dirname,'../..'),launchURL=pathToFileURL(path.join(__dirname,'launch.html')).href;
const partition='persist:studio360';
let win,backend,tray,bootPromise,quitting=false,closing=false,pendingUpdate=false,updater;
const settingsFile=()=>path.join(app.getPath('userData'),'settings.json');
function workspace(){return process.env.STUDIO_WORKSPACE||readJSON(settingsFile())?.workspace||path.join(app.getPath('documents'),'360 Studio Workspace');}
function notify(message,error=false){if(win&&!win.isDestroyed())win.webContents.send('desktop:status',{message,error,workspace:backend?.workspace||workspace()});}
function webPreferences(){return {preload:path.join(__dirname,'preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true,webSecurity:true,partition};}
function ownedURL(url){try{return new URL(url).origin===`http://127.0.0.1:${backend?.record?.port}`;}catch{return false;}}
function safeExternal(url){try{if(['https:','http:'].includes(new URL(url).protocol))shell.openExternal(url);}catch{}}
function secureContents(contents){
  contents.on('will-navigate',(event,url)=>{if(url!==launchURL&&!ownedURL(url)){event.preventDefault();safeExternal(url);}});
  contents.setWindowOpenHandler(({url})=>{
    if(ownedURL(url))return {action:'allow',overrideBrowserWindowOptions:{webPreferences:webPreferences()}};
    safeExternal(url);return {action:'deny'};
  });
}
async function rendererBusy(){
  if(!win||win.isDestroyed()||!ownedURL(win.webContents.getURL()))return false;
  return win.webContents.executeJavaScript("Boolean(document.querySelector('form[data-action-busy]'))");
}
async function openLogs(){const folder=path.join(app.getPath('userData'),'logs');fs.mkdirSync(folder,{recursive:true});await shell.openPath(folder);}
async function boot(){
  if(bootPromise)return bootPromise;
  bootPromise=(async()=>{
    try{
      backend=new Backend({workspace:workspace(),userData:app.getPath('userData'),resourcesPath:process.resourcesPath,
        root,packaged:app.isPackaged,notify});
      const record=await backend.start(),url=`http://127.0.0.1:${record.port}`;
      const ses=session.fromPartition(partition);
      await ses.cookies.set({url,name:record.cookie,value:record.token,httpOnly:true,sameSite:'strict',path:'/'});
      const interactions=new Set(['pointerLock','keyboardLock','fullscreen','clipboard-sanitized-write']);
      ses.setPermissionRequestHandler((contents,permission,callback)=>callback(Boolean(contents&&ownedURL(contents.getURL())&&interactions.has(permission))));
      ses.setPermissionCheckHandler((contents,permission)=>Boolean(contents&&ownedURL(contents.getURL())&&interactions.has(permission)));
      // Start inventory before opening the Dashboard; the existing Verify panel
      // polls and presents the result. No training or inference runs here.
      try{await backend.request('/api/runtime-verification','POST');}catch(error){notify('ตรวจเครื่องไม่สำเร็จ: '+error.message);}
      if(win&&!win.isDestroyed())await win.loadURL(url+'/');
      return record;
    }catch(error){notify(error.message,true);throw error;}
  })();
  try{return await bootPromise;}finally{bootPromise=null;}
}
function showWindow(){if(win&&!win.isDestroyed()){win.show();win.focus();}}
function background(){
  if(!tray){tray=new Tray(path.join(__dirname,'icon.png'));tray.setToolTip('360 Studio · งานกำลังทำต่อ');
    tray.setContextMenu(Menu.buildFromTemplate([{label:'เปิด 360 Studio',click:showWindow},{label:'ออกเมื่อไม่มีงาน',click:requestClose}]));
    tray.on('double-click',showWindow);}
  win.hide();
}
async function requestClose(){
  if(closing||quitting)return;closing=true;
  try{
    if(bootPromise){notify('กำลังรอ runtime พร้อมก่อนปิด…');try{await bootPromise;}catch{}}
    if(backend?.record){
      const status=await backend.busy();
      if(!canInstall({backend:status,rendererBusy:await rendererBusy()})){
        const answer=await dialog.showMessageBox(win,{type:'info',title:'ยังมีงานกำลังทำอยู่',
          message:'เก็บแอปทำงานต่อในเบื้องหลังได้',detail:status.reasons.join('\n')||'กำลังส่งหรือบันทึกข้อมูล',
          buttons:['ทำงานต่อเบื้องหลัง','กลับหน้าแอป'],defaultId:1,cancelId:1});
        if(answer.response===0)background();return;
      }
      await backend.shutdown();
    }
    quitting=true;tray?.destroy();app.quit();
  }catch(error){await dialog.showMessageBox(win,{type:'warning',message:'ยังปิดอย่างปลอดภัยไม่ได้',detail:error.message+'\nแอปจะเก็บงานที่กำลังทำไว้',buttons:['กลับหน้าแอป','เปิด Log']}).then(answer=>{if(answer.response===1)openLogs();});}
  finally{closing=false;}
}
async function chooseWorkspace(){
  if(bootPromise)return;
  if(backend?.record){const busy=await backend.busy();if(busy.busy||await rendererBusy())throw new Error('รอให้งานและการอัปโหลดเสร็จก่อนเปลี่ยน Workspace');}
  const result=await dialog.showOpenDialog(win,{title:'เลือกโฟลเดอร์ Workspace',properties:['openDirectory','createDirectory'],defaultPath:workspace()});
  if(result.canceled)return;
  const selected=path.resolve(result.filePaths[0]);if(selected===path.resolve(workspace()))return;
  if(backend?.record)await backend.shutdown();
  writeJSON(settingsFile(),{workspace:selected});delete process.env.STUDIO_WORKSPACE;
  await win.loadURL(launchURL);await boot();
}
async function installUpdate(){
  if(!pendingUpdate||!updater)return;
  const status=await backend.busy();
  if(!canInstall({backend:status,rendererBusy:await rendererBusy()})){
    await dialog.showMessageBox(win,{type:'info',message:'รอให้งานและการอัปโหลดเสร็จก่อนอัปเดต',detail:status.reasons.join('\n')});return;
  }
  await backend.shutdown();quitting=true;updater.quitAndInstall(false,true);
}
async function checkUpdates(){
  const feed=validFeed(process.env.STUDIO_UPDATE_URL,{allowLocal:process.env.STUDIO_UPDATE_TEST==='1'});
  if(!feed){await dialog.showMessageBox(win,{message:'ยังไม่ได้ตั้งค่าช่องอัปเดต',detail:'ตัวติดตั้งนี้ยังเป็นรุ่นทดสอบ สามารถติดตั้งรุ่นใหม่ทับได้เมื่อไม่มีงาน โดยเก็บ Workspace เดิมไว้'});return;}
  if(!app.isPackaged){await dialog.showMessageBox(win,{message:'ตรวจอัปเดตได้จากแอปที่ติดตั้งแล้ว'});return;}
  if(!updater){
    updater=require('electron-updater').autoUpdater;updater.autoDownload=false;updater.autoInstallOnAppQuit=false;
    updater.allowDowngrade=false;updater.setFeedURL({provider:'generic',url:feed});
    updater.on('error',error=>dialog.showMessageBox(win,{type:'error',message:'อัปเดตไม่สำเร็จ',detail:error.message}));
    updater.on('update-downloaded',()=>{pendingUpdate=true;dialog.showMessageBox(win,{message:'ดาวน์โหลดอัปเดตแล้ว',detail:'เลือก Help → ติดตั้งอัปเดต เมื่อไม่มีงานกำลังทำอยู่'});});
  }
  const result=await updater.checkForUpdates();
  if(result?.updateInfo?.version===app.getVersion()){await dialog.showMessageBox(win,{message:'ใช้เวอร์ชันล่าสุดแล้ว'});return;}
  const answer=await dialog.showMessageBox(win,{message:'มีอัปเดต '+result.updateInfo.version,buttons:['ดาวน์โหลด','ภายหลัง'],defaultId:1,cancelId:1});
  if(answer.response===0)await updater.downloadUpdate();
}
function guarded(action){return()=>Promise.resolve().then(action).catch(error=>dialog.showMessageBox(win,{type:'error',message:error.message}));}

if(!app.requestSingleInstanceLock()){app.quit();}
else{
  app.on('second-instance',showWindow);
  app.on('before-quit',event=>{if(!quitting){event.preventDefault();requestClose();}});
  app.on('web-contents-created',(_event,contents)=>secureContents(contents));
  app.whenReady().then(async()=>{
    win=new BrowserWindow({width:1450,height:1000,minWidth:850,minHeight:650,title:'360 Studio',
      icon:path.join(__dirname,'icon.png'),webPreferences:webPreferences(),show:process.env.STUDIO_HEADLESS!=='1'});
    win.on('close',event=>{if(!quitting){event.preventDefault();requestClose();}});
    for(const [channel,action] of [['desktop:retry',()=>boot()],['desktop:workspace',chooseWorkspace],['desktop:logs',openLogs]]){
      ipcMain.handle(channel,(event)=>{
        if(event.sender!==win.webContents||event.senderFrame!==win.webContents.mainFrame)throw new Error('Invalid desktop IPC sender');
        return action();
      });
    }
    Menu.setApplicationMenu(Menu.buildFromTemplate([
      {label:'Workspace',submenu:[{label:'เลือก Workspace…',click:guarded(chooseWorkspace)},
        {label:'เปิดโฟลเดอร์ Workspace',click:()=>shell.openPath(workspace())},{type:'separator'},
        {label:'ออกอย่างปลอดภัย',click:requestClose}]},
      {role:'editMenu'},{role:'viewMenu'},
      {label:'Help',submenu:[{label:'ตรวจเครื่อง (Verify)',click:guarded(async()=>{await backend.request('/api/runtime-verification','POST');await win.loadURL(`http://127.0.0.1:${backend.record.port}/`);})},
        {label:'เปิด Log',click:openLogs},{label:'ตรวจอัปเดต',click:guarded(checkUpdates)},
        {label:'ติดตั้งอัปเดต',click:guarded(installUpdate)},
        {label:'เกี่ยวกับ',click:()=>dialog.showMessageBox(win,{message:'360 Studio '+app.getVersion(),detail:'Workspace: '+workspace()})}]}]));
    await win.loadURL(launchURL);boot().catch(()=>{});
  });
}
