const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const {spawn}=require('node:child_process');

const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function readJSON(file){try{return JSON.parse(fs.readFileSync(file,'utf8'));}catch{return null;}}
function writeJSON(file,value){fs.mkdirSync(path.dirname(file),{recursive:true});const temp=file+'.tmp';fs.writeFileSync(temp,JSON.stringify(value,null,2),{mode:0o600});fs.renameSync(temp,file);}

class Backend {
  constructor({workspace,userData,resourcesPath,root,packaged,notify=()=>{}}){
    Object.assign(this,{workspace:path.resolve(workspace),userData,resourcesPath,root,packaged,notify});
    this.stateFile=path.join(userData,'backend.json');this.record=null;
  }
  async request(route,method='GET'){
    if(!this.record)throw new Error('Backend ยังไม่พร้อม');
    const response=await fetch(`http://127.0.0.1:${this.record.port}${route}`,{
      method,headers:{'X-Studio-Token':this.record.token},signal:AbortSignal.timeout(8000)});
    const result=await response.json();
    if(route.startsWith('/api/desktop/')&&(result.instance!==this.record.instance||path.resolve(result.workspace||'')!==this.workspace))throw new Error('Backend identity changed');
    if(!response.ok){const error=new Error(result.error||result.reasons?.join(', ')||`HTTP ${response.status}`);error.status=response.status;error.result=result;throw error;}
    return result;
  }
  async start(){
    const saved=readJSON(this.stateFile);
    if(saved?.token&&saved.workspace===this.workspace){
      this.record=saved;
      try{const status=await this.request('/api/desktop/health');
        if(status.instance===saved.instance&&path.resolve(status.workspace)===this.workspace){this.notify('กลับมาเชื่อมกับงานเดิมแล้ว');return saved;}
      }catch{}
      let alive=false;try{process.kill(saved.pid,0);alive=true;}catch{}
      if(alive)throw new Error('Backend เดิมยังอยู่แต่เชื่อมต่อไม่ได้ กรุณาเปิด log ก่อนเริ่มซ้ำ');
    }
    const instance=crypto.randomUUID().replaceAll('-',''),token=crypto.randomBytes(32).toString('hex');
    const readyFile=path.join(this.userData,'backend-ready-'+instance+'.json');
    const logs=path.join(this.userData,'logs');fs.mkdirSync(logs,{recursive:true});
    this.logFile=path.join(logs,'backend-'+instance+'.log');
    const fd=fs.openSync(this.logFile,'a');
    const executable=this.packaged?path.join(this.resourcesPath,'backend',process.platform==='win32'?'studio360-backend.exe':'studio360-backend'):
      process.env.STUDIO_DEV_PYTHON||path.join(this.root,process.platform==='win32'?'.venv-win/Scripts/python.exe':'.venv/bin/python');
    if(!fs.existsSync(executable)){fs.closeSync(fd);throw new Error('ไม่พบ runtime: '+executable);}
    const args=[...(this.packaged?[]:['-m','app.desktop.backend']),'--workspace',this.workspace,'--state-file',readyFile,'--instance',instance];
    let failure;
    const child=spawn(executable,args,{cwd:this.packaged?path.dirname(executable):this.root,
      env:{...process.env,PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8',STUDIO_WORKSPACE:this.workspace,STUDIO_SESSION_TOKEN:token},
      windowsHide:true,stdio:['ignore',fd,fd]});
    fs.closeSync(fd);child.on('error',error=>{failure=error;});child.unref();this.child=child;
    const deadline=Date.now()+90000;
    this.notify('กำลังเปิด runtime และ workspace…');
    while(Date.now()<deadline){
      if(failure)throw failure;
      const state=readJSON(readyFile);
      if(state?.instance===instance){
        this.record={...state,token,logFile:this.logFile};
        const status=await this.request('/api/desktop/health');
        if(status.instance!==instance)throw new Error('Backend identity mismatch');
        writeJSON(this.stateFile,this.record);fs.unlinkSync(readyFile);return this.record;
      }
      if(child.exitCode!==null)throw new Error('เปิด runtime ไม่สำเร็จ ดู log: '+this.logFile);
      await pause(200);
    }
    throw new Error('Runtime ยังไม่ตอบรับ กรุณาดู log: '+this.logFile);
  }
  async busy(){return this.request('/api/desktop/busy');}
  async shutdown(){
    await this.request('/api/desktop/prepare-quit','POST');
    await this.request('/api/desktop/shutdown','POST');
    const deadline=Date.now()+15000;
    while(Date.now()<deadline){
      let alive=false;try{process.kill(this.record.pid,0);alive=true;}catch{}
      if(!alive){fs.unlinkSync(this.stateFile);this.record=null;return;}
      await pause(100);
    }
    throw new Error('Backend กำลังปิด กรุณารอก่อน ไม่ส่งคำสั่ง kill งาน');
  }
}
module.exports={Backend,readJSON,writeJSON};
