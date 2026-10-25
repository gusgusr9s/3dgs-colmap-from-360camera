import * as THREE from 'three';

// Horn's absolute orientation with a Jacobi eigensolver. Returns a proper
// rotation and optional uniform scale, never reflection or nonuniform shear.
export function solveSceneAlignment(source, target, scaleEnabled = true) {
  if (!Array.isArray(source) || source.length < 3 || source.length !== target?.length || source.length > 24
      || [...source, ...target].some(p => !Array.isArray(p) || p.length !== 3 || !p.every(Number.isFinite))) throw new Error('เลือกจุดคู่ที่ตรงกันอย่างน้อย 3 คู่');
  const average = points => points.reduce((sum, p) => sum.add(new THREE.Vector3(...p)), new THREE.Vector3()).multiplyScalar(1 / points.length);
  const a = average(source), b = average(target);
  const xs = source.map(p => new THREE.Vector3(...p).sub(a)), ys = target.map(p => new THREE.Vector3(...p).sub(b));
  for (const points of [xs, ys]) {
    let cross = 0, length = 0;
    for (const p of points) { length += p.lengthSq(); for (const q of points) cross = Math.max(cross, p.clone().cross(q).lengthSq()); }
    if (length < 1e-16 || cross < length * length * 1e-10) throw new Error('จุดอยู่ใกล้กันหรือเรียงเป็นเส้นตรง เลือกมุมที่กระจายบนพื้นที่');
  }
  const s = Array.from({length:3}, () => [0,0,0]);
  xs.forEach((p,i) => p.toArray().forEach((v,r) => ys[i].toArray().forEach((w,c) => { s[r][c] += v*w; })));
  const [[xx,xy,xz],[yx,yy,yz],[zx,zy,zz]] = s;
  const n = [[xx+yy+zz,yz-zy,zx-xz,xy-yx],[yz-zy,xx-yy-zz,xy+yx,zx+xz],
    [zx-xz,xy+yx,-xx+yy-zz,yz+zy],[xy-yx,zx+xz,yz+zy,-xx-yy+zz]];
  const eigen = Array.from({length:4}, (_,r) => Array.from({length:4}, (_,c) => Number(r===c)));
  for (let step=0;step<80;step++) {
    let p=0,q=1;
    for(let r=0;r<4;r++) for(let c=r+1;c<4;c++) if(Math.abs(n[r][c])>Math.abs(n[p][q])) {p=r;q=c;}
    if(Math.abs(n[p][q])<1e-13*Math.max(1,...n.map((row,i)=>Math.abs(row[i])))) break;
    const angle=.5*Math.atan2(2*n[p][q],n[q][q]-n[p][p]),cos=Math.cos(angle),sin=Math.sin(angle);
    const pp=n[p][p],qq=n[q][q],pq=n[p][q];
    for(let r=0;r<4;r++) if(r!==p&&r!==q) {const rp=n[r][p],rq=n[r][q];n[r][p]=n[p][r]=cos*rp-sin*rq;n[r][q]=n[q][r]=sin*rp+cos*rq;}
    n[p][p]=cos*cos*pp-2*sin*cos*pq+sin*sin*qq;n[q][q]=sin*sin*pp+2*sin*cos*pq+cos*cos*qq;n[p][q]=n[q][p]=0;
    for(let r=0;r<4;r++) {const rp=eigen[r][p],rq=eigen[r][q];eigen[r][p]=cos*rp-sin*rq;eigen[r][q]=sin*rp+cos*rq;}
  }
  const index=n.reduce((best,row,i)=>row[i]>n[best][best]?i:best,0);
  const quaternion=new THREE.Quaternion(eigen[1][index],eigen[2][index],eigen[3][index],eigen[0][index]).normalize();
  const variance=xs.reduce((sum,p)=>sum+p.lengthSq(),0);
  const scale=scaleEnabled?xs.reduce((sum,p,i)=>sum+ys[i].dot(p.clone().applyQuaternion(quaternion)),0)/variance:1;
  if(!Number.isFinite(scale)||scale<1e-6||scale>1e6) throw new Error('สเกลที่คำนวณได้ไม่ถูกต้อง ตรวจลำดับจุดคู่');
  const translation=b.clone().sub(a.clone().applyQuaternion(quaternion).multiplyScalar(scale));
  const matrix=new THREE.Matrix4().compose(translation,quaternion,new THREE.Vector3().setScalar(scale));
  const rms=Math.sqrt(source.reduce((sum,p,i)=>sum+new THREE.Vector3(...p).applyMatrix4(matrix).distanceToSquared(new THREE.Vector3(...target[i])),0)/source.length);
  return {matrix,scale,rms};
}
