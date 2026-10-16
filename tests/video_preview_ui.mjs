// Exercise decode fallback, retry, timeline preservation and dialog lifecycle.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

class Element {
  constructor(){this.listeners={};this.children=[];this.style={};this.hidden=false;this.dataset={};this.currentTime=7;this.duration=65;this.paused=false;this.error=null;this.plays=0;}
  addEventListener(type,fn,options={}){(this.listeners[type] ||= []).push({fn,once:options.once});}
  fire(type,event={}){const items=this.listeners[type] || [];this.listeners[type]=items.filter(x=>!x.once);for(const x of items)x.fn(event);}
  append(...items){this.children.push(...items);}
  setAttribute(){}
  removeAttribute(name){delete this[name];}
  querySelectorAll(){return [];}
  closest(){return this.dialog || null;}
  insertAdjacentElement(position,panel){this.panel=panel;}
  pause(){this.paused=true;this.fire('pause');}
  load(){this.error=null;}
  play(){this.plays++;if(this.blocked)return Promise.reject(new Error('gesture required'));this.paused=false;this.fire('play');return Promise.resolve();}
}
let requests=[],responses=[],timers=new Map(),timerID=0;
const context=vm.createContext({WeakMap,URLSearchParams,AbortSignal,console,
  setTimeout(fn){timers.set(++timerID,fn);return timerID;},clearTimeout(id){timers.delete(id);},
  document:{querySelectorAll(){return [];},addEventListener(){},createElement(){return new Element();}},
  fetch:async(url,options)=>{requests.push({url,...options});const result=responses.shift();if(result instanceof Error)throw result;return {ok:true,json:async()=>result};}
});
vm.runInContext(fs.readFileSync(new URL('../video_preview.mjs',import.meta.url),'utf8'),context);
const settle=async()=>{for(let n=0;n<8;n++)await Promise.resolve();};
const attach=(video)=>{context.video=video;vm.runInContext("attachVideoPreview(video,'uploads:DJI & clip.mp4')",context);return video.panel;};
const tick=async()=>{const [id,fn]=timers.entries().next().value;timers.delete(id);fn();await settle();};

const video=new Element(),panel=attach(video),[message,button]=panel.children;
assert.equal(attach(video),panel,'one feedback panel per video');
assert.equal(requests.length,0,'native playback does not start a conversion');
responses.push({state:'queued'});
video.fire('play');video.error={code:3};video.fire('error');await settle();
assert.equal(requests[0].method,'POST');
assert.ok(requests[0].url.includes('DJI+%26+clip.mp4'));
assert.equal(button.hidden,true);
assert.ok(message.textContent.includes('รอคิว'));
responses.push({state:'running',progress:42.9});await tick();
assert.equal(requests[1].method,'GET');assert.ok(message.textContent.includes('42%'));
responses.push({state:'ready',url:'/api/video-preview/file?version=1'});await tick();
video.currentTime=0;video.fire('loadedmetadata');await settle();
assert.equal(video.currentTime,7,'fallback preserves the requested time');
assert.equal(video.plays,1,'a failed native play resumes when ready');
assert.equal(video.paused,false);
video.pause();assert.equal(video.paused,true);

const retry=new Element(),[retryMessage,retryButton]=attach(retry).children;
responses.push(new Error('network unavailable'));retryButton.fire('click');await settle();
assert.ok(retryMessage.textContent.includes('network unavailable'));assert.equal(retryButton.hidden,false);
responses.push({state:'ready',url:'/api/video-preview/file?version=2'});
retry.blocked=true;retryButton.fire('click');await settle();retry.fire('loadedmetadata');await settle();
assert.equal(retryButton.hidden,false,'autoplay rejection offers an explicit Play action');
retry.blocked=false;retryButton.fire('click');await settle();assert.equal(retry.paused,false);

const closed=new Element();closed.dialog=new Element();closed.dialog.open=true;
const closedPanel=attach(closed);responses.push({state:'running',progress:10});
closedPanel.children[1].fire('click');await settle();
closed.dialog.open=false;closed.dialog.fire('close');
responses.push({state:'ready',url:'/api/video-preview/file?version=3'});await tick();closed.fire('loadedmetadata');await settle();
assert.equal(closed.plays,0,'a closed trim dialog never autoplays');
assert.equal(timers.size,0,'ready previews stop polling');
console.log('Video preview UI passed: decode fallback, progress, retry, seek restoration, autoplay rejection and closed-dialog safety.');
