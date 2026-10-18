const test=require('node:test');const assert=require('node:assert/strict');
const {canInstall,validFeed}=require('../src/update-policy.cjs');
test('updates fail closed during uploads, training, queues or unavailable backend',()=>{
  assert.equal(canInstall({backend:{busy:false}}),true);
  assert.equal(canInstall({backend:{busy:true}}),false);
  assert.equal(canInstall({backend:{busy:false},rendererBusy:true}),false);
  assert.equal(canInstall({backend:null}),false);
});
test('update feed accepts HTTPS, with loopback HTTP reserved for explicit QA',()=>{
  assert.equal(validFeed(),null);
  assert.equal(validFeed('https://updates.example.test/releases'),'https://updates.example.test/releases');
  for(const value of ['file:///tmp/update','http://example.test/update','https://user:pass@example.test/update','javascript:alert(1)'])assert.throws(()=>validFeed(value));
  assert.throws(()=>validFeed('http://127.0.0.1:1234/'));
  assert.equal(validFeed('http://127.0.0.1:1234/',{allowLocal:true}),'http://127.0.0.1:1234/');
});
