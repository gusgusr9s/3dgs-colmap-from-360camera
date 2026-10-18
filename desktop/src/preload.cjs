const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('studioDesktop',{
  retry:()=>ipcRenderer.invoke('desktop:retry'),
  chooseWorkspace:()=>ipcRenderer.invoke('desktop:workspace'),
  openLogs:()=>ipcRenderer.invoke('desktop:logs'),
  onStatus:callback=>{const listener=(_,value)=>callback(value);ipcRenderer.on('desktop:status',listener);return()=>ipcRenderer.removeListener('desktop:status',listener);}
});
