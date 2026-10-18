import assert from 'node:assert/strict';
import {routes,selectRoute} from '../assets/app/tutorial.mjs';
class Element {
  constructor(){this.attributes={};this.textContent='';this.children={};}
  setAttribute(key,value){this.attributes[key]=String(value);}
  querySelector(key){return this.children[key];}
}
const nodes=new Map();
for(const key of ['#tutorial-route-path','#tutorial-route-title','#tutorial-route-description','#tutorial-route-caption','#tutorial-start','#tutorial-start-label','#tutorial-room-walls']) nodes.set(key,new Element());
nodes.get('#tutorial-room-walls').children.path=new Element();nodes.get('#tutorial-room-walls').children.rect=new Element();
const buttons=Object.keys(routes).map(route=>Object.assign(new Element(),{dataset:{route}}));
const root={querySelector:key=>nodes.get(key),querySelectorAll:()=>buttons};
for(const [key,route] of Object.entries(routes)) {
  assert.equal(selectRoute(root,key),true);
  assert.equal(nodes.get('#tutorial-route-path').attributes.d,route.path);
  assert.equal(nodes.get('#tutorial-route-caption').textContent,route.caption);
  assert.equal(buttons.filter(button=>button.attributes['aria-pressed']==='true').length,1);
  assert.equal(buttons.find(button=>button.dataset.route===key).attributes['aria-pressed'],'true');
}
assert.equal(selectRoute(root,'invalid'),false);
assert.match(routes.repeat.caption,/เยื้องแนว/);
assert.match(routes.rooms.caption,/ประตู/);
console.log('Tutorial route controls passed');
