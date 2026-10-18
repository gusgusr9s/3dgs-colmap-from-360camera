const {spawnSync}=require('node:child_process');
const path=require('node:path');
const fs=require('node:fs');
const root=path.resolve(__dirname,'../..');
const python=process.env.STUDIO_BUILD_PYTHON||path.join(root,process.platform==='win32'?'.venv-win/Scripts/python.exe':'.venv/bin/python');
if(!fs.existsSync(python))throw new Error('Run app_1_install first, or set STUDIO_BUILD_PYTHON.');
const result=spawnSync(python,[path.join(__dirname,'build_backend.py')],{cwd:root,stdio:'inherit',windowsHide:true});
if(result.error)throw result.error;
process.exit(result.status??1);
