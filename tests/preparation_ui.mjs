import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
let change;
const floor = {value:'standard',addEventListener:(name, handler)=>change=handler};
const images = [{dataset:{floorPreview:'/preview?frame=a'}},{dataset:{floorPreview:'/preview?frame=b'}}];
vm.runInNewContext(fs.readFileSync(new URL('../preparation.mjs',import.meta.url),'utf8'),{
  document:{querySelector:()=>floor,querySelectorAll:()=>images},encodeURIComponent,
});
floor.value='more_floor';change();
assert.equal(images[0].src,'/preview?frame=a&floor=more_floor');
assert.equal(images[1].src,'/preview?frame=b&floor=more_floor');
floor.value='person_only';change();
assert(images.every(image=>image.src.endsWith('&floor=person_only')));
console.log('Preparation preview updates every displayed image from one selector.');
