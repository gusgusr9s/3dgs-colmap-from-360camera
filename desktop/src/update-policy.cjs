function canInstall({backend,rendererBusy=false}) {
  return Boolean(backend&&!backend.busy&&!rendererBusy);
}
function validFeed(value,{allowLocal=false}={}) {
  if(!value)return null;
  const url=new URL(value);
  const local=allowLocal&&url.protocol==='http:'&&url.hostname==='127.0.0.1';
  if(url.protocol!=='https:'&&!local)throw new Error('Update channel must use HTTPS');
  if(url.username||url.password||url.hash)throw new Error('Invalid update channel');
  return url.href;
}
module.exports={canInstall,validFeed};
