// Pointer gestures produce a camera snapshot and CSS-pixel path. No model data is changed here.
export function matrixRows(matrix) {
  const e=matrix.elements;
  return Array.from({length:4},(_,r)=>Array.from({length:4},(_,c)=>e[4*c+r]));
}
export function compactPath(points,limit=512) {
  if(points.length<=limit) return points.map(p=>[...p]);
  return Array.from({length:limit},(_,i)=>points[Math.round(i*(points.length-1)/(limit-1))]);
}
export class PointPainter {
  constructor({overlay,camera,controls,onStroke,onMode}) {
    Object.assign(this,{overlay,camera,controls,onStroke,onMode});
    this.tool='navigate';this.radius=24;this.frontOnly=true;this.depthTolerance=.02;this.busy=false;this.stroke=null;
    this.ctx=overlay.getContext('2d');
    overlay.addEventListener('pointerdown',e=>this.down(e));
    overlay.addEventListener('pointermove',e=>this.move(e));
    overlay.addEventListener('pointerup',e=>this.up(e));
    overlay.addEventListener('pointercancel',()=>this.cancel());
    overlay.addEventListener('pointerleave',()=>{if(!this.stroke)this.clear();});
    overlay.addEventListener('wheel',e=>e.preventDefault(),{passive:false});
    overlay.addEventListener('contextmenu',e=>e.preventDefault());
  }
  resize(width,height) {
    if(this.stroke) this.cancel();
    const ratio=Math.min(globalThis.devicePixelRatio || 1,2);
    this.width=Math.round(width);this.height=Math.round(height);this.overlay.width=Math.round(width*ratio);this.overlay.height=Math.round(height*ratio);
    this.ctx.setTransform(ratio,0,0,ratio,0,0);this.clear();
  }
  setTool(tool) {
    if(!['navigate','brush','lasso'].includes(tool))return;
    this.cancel();this.tool=tool;this.controls.enabled=tool==='navigate'&&!this.busy;
    this.overlay.style.pointerEvents=tool==='navigate'?'none':'auto';this.overlay.style.cursor=tool==='navigate'?'auto':'crosshair';
    this.onMode?.(tool);
  }
  setBusy(value) {this.busy=value;this.controls.enabled=this.tool==='navigate'&&!value;}
  clear() {this.ctx.clearRect(0,0,this.width || 1,this.height || 1);}
  cancel() {this.stroke=null;this.clear();}
  point(e) {
    const box=this.overlay.getBoundingClientRect();
    return [Math.max(0,Math.min(this.width,e.clientX-box.x)),Math.max(0,Math.min(this.height,e.clientY-box.y))];
  }
  down(e) {
    if(this.busy||this.tool==='navigate'||e.button!==0||this.stroke)return;
    e.preventDefault();this.camera.updateMatrixWorld(true);
    this.stroke={pointer:e.pointerId,points:[this.point(e)],tool:this.tool,radius:this.radius,front_only:this.frontOnly,
      depth_tolerance:this.depthTolerance,viewport:[Math.round(this.width),Math.round(this.height)],
      view:matrixRows(this.camera.matrixWorldInverse),
      view_projection:matrixRows(this.camera.projectionMatrix.clone().multiply(this.camera.matrixWorldInverse))};
    this.overlay.setPointerCapture(e.pointerId);this.draw(this.stroke.points);
  }
  move(e) {
    if(this.busy||this.tool==='navigate')return;
    const point=this.point(e);
    if(this.stroke&&this.stroke.pointer===e.pointerId) {
      const last=this.stroke.points.at(-1);
      if(Math.hypot(point[0]-last[0],point[1]-last[1])>=2) {
        this.stroke.points.push(point);
        if(this.stroke.points.length>4096)this.stroke.points=compactPath(this.stroke.points,2048);
      }
      this.draw(this.stroke.points);
    } else if(!this.stroke) this.draw([point]);
  }
  up(e) {
    if(!this.stroke||this.stroke.pointer!==e.pointerId)return;
    const stroke=this.stroke;stroke.points.push(this.point(e));this.stroke=null;this.clear();
    if(stroke.tool==='lasso'&&stroke.points.length<3)return;
    delete stroke.pointer;stroke.points=compactPath(stroke.points);
    this.onStroke(stroke);
  }
  draw(points) {
    this.clear();if(!points.length)return;
    const c=this.ctx;c.strokeStyle='#ff7569';c.fillStyle='#ff75692a';c.lineCap='round';c.lineJoin='round';
    c.beginPath();c.moveTo(...points[0]);for(const p of points.slice(1))c.lineTo(...p);
    if(this.tool==='brush') {
      c.lineWidth=this.radius*2;c.strokeStyle='#ff756955';c.stroke();
      c.beginPath();c.arc(...points.at(-1),this.radius,0,2*Math.PI);c.lineWidth=1.5;c.strokeStyle='#ffb5a9';c.stroke();
    } else {
      if(points.length>2){c.closePath();c.fill('evenodd');}c.lineWidth=2;c.stroke();
    }
  }
}
