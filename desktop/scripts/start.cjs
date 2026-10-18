const path=require('node:path');const {spawn}=require('node:child_process');
const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
const child=spawn(require('electron'),[path.resolve(__dirname,'..')],{env,stdio:'inherit',windowsHide:true});
child.on('exit',code=>process.exit(code??1));child.on('error',error=>{console.error(error);process.exit(1);});
