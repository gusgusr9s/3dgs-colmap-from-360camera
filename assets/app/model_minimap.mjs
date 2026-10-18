// Top-down projection in the model's world-up plane. No dependency on Three.
const dot = (a, b) => a.reduce((sum, v, i) => sum + v * b[i], 0);
const cross = (a, b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
const unit = a => { const n = Math.hypot(...a); return n > 1e-10 ? a.map(v => v/n) : null; };
const finite3 = a => Array.isArray(a) && a.length === 3 && a.every(Number.isFinite);
export function mapBasis(worldUp) {
  const up = finite3(worldUp) && unit(worldUp) || [0, 1, 0];
  const reference = Math.abs(up[0]) < .9 ? [1, 0, 0] : [0, 0, 1];
  const right = unit(reference.map((v, i) => v - up[i] * dot(reference, up)));
  return { right, top: unit(cross(up, right)), up };
}

export function routeMap(captures, worldUp, width = 260, height = 170, padding = 20) {
  const basis = mapBasis(worldUp);
  const project = vector => [dot(vector, basis.right), -dot(vector, basis.top)];
  const points = [];
  captures.forEach((capture, index) => {
    const m = capture?.transform_matrix;
    const position = Array.isArray(m) && m.length >= 3 ? m.slice(0, 3).map(row => row?.[3]) : null;
    if (finite3(position)) points.push({ index, position, point: project(position) });
  });
  if (!points.length) return null;
  const xs = points.map(p => p.point[0]), ys = points.map(p => p.point[1]);
  const min = [Math.min(...xs), Math.min(...ys)], max = [Math.max(...xs), Math.max(...ys)];
  const span = Math.max(max[0]-min[0], max[1]-min[1], .01);
  const scale = Math.min((width-2*padding)/Math.max(max[0]-min[0], span*.03),
    (height-2*padding)/Math.max(max[1]-min[1], span*.03));
  const center = min.map((v, i) => (v + max[i])/2);
  const pixel = vector => project(vector).map((v, i) => (v-center[i])*scale + [width,height][i]/2);
  points.forEach(p => { p.pixel = pixel(p.position); });
  return { points, basis, scale, width, height, pixel,
    heading: direction => {
      const [x,y] = project(direction);
      return Math.hypot(x,y) > 1e-8 ? Math.atan2(y,x)*180/Math.PI + 90 : null;
    },
    nearest: (x, y, radius = 16, selected = 0) => {
      // At repeated loop positions prefer the current point, then its neighbors.
      let result = null, best = radius*radius;
      for (const p of points) {
        const d = (p.pixel[0]-x)**2 + (p.pixel[1]-y)**2;
        if (d < best - 1e-8 || (Math.abs(d-best) < 1e-8 && result && Math.abs(p.index-selected)<Math.abs(result.index-selected))) {
          best = d; result = p;
        }
      }
      return result?.index ?? null;
    } };
}

export class MiniMap {
  constructor(document, captures, up, metric, onChoose) {
    this.panel = document.querySelector('#mini-map');
    this.toggle = document.querySelector('#mini-map-toggle');
    this.svg = document.querySelector('#mini-map-svg');
    this.map = routeMap(captures, up);
    this.last = -Infinity;
    this.selected = 0;
    if (this.panel) this.panel.hidden = true;
    if (this.toggle) this.toggle.hidden = true;
    if (!this.panel || !this.toggle || !this.svg || !this.map) return;
    const get = id => document.querySelector('#mini-map-' + id);
    this.marker = get('viewer'); this.selectedMarker = get('selected');
    this.label = get('label'); this.hint = get('hint');
    this.toggle.hidden = false; this.panel.hidden = false;
    this.toggle.setAttribute('aria-pressed', 'true');
    const paths = this.map.points.map(p => p.pixel.map(v => v.toFixed(2)).join(','));
    get('route').setAttribute('d', 'M' + paths.join(' L'));
    get('points').setAttribute('d', paths.map(p => 'M'+p+' h.01').join(' '));
    const rawLength = 55 / this.map.scale;
    const power = 10 ** Math.floor(Math.log10(rawLength));
    const length = [1,2,5,10].map(n => n*power).filter(n => n <= rawLength).pop();
    get('scale').setAttribute('x2', String(20+length*this.map.scale));
    get('scale-label').textContent = metric ? `${Number(length.toPrecision(2))} m` : 'ระยะสัมพัทธ์';
    this.toggle.addEventListener('click', () => {
      if (document.pointerLockElement) document.exitPointerLock?.();
      this.panel.hidden = !this.panel.hidden;
      this.toggle.setAttribute('aria-pressed', String(!this.panel.hidden));
    });
    this.svg.addEventListener('click', event => {
      if (document.pointerLockElement) return;
      const rect = this.svg.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const x = (event.clientX-rect.left)*260/rect.width;
      const y = (event.clientY-rect.top)*170/rect.height;
      const chosen = this.map.nearest(x, y, 16, this.selected);
      if (chosen !== null) onChoose(chosen);
    });
    this.svg.addEventListener('keydown', event => {
      const step = { ArrowLeft: -1, ArrowDown: -1, ArrowRight: 1, ArrowUp: 1 }[event.key];
      if (!step) return;
      event.preventDefault();
      const at = this.map.points.findIndex(p => p.index === this.selected);
      onChoose(this.map.points[Math.max(0, Math.min(this.map.points.length-1, at+step))].index);
    });
  }
  update(position, direction, index, time = 0, locked = false) {
    if (!this.map || !this.marker || this.panel.hidden || (index === this.selected && time-this.last < 100)) return;
    this.last = time; this.selected = index;
    const [rawX, rawY] = this.map.pixel(position);
    const x = Math.max(8, Math.min(252, rawX)), y = Math.max(8, Math.min(162, rawY));
    const outside = x !== rawX || y !== rawY;
    const heading = this.map.heading(direction);
    this.marker.setAttribute('transform', `translate(${x} ${y}) rotate(${heading ?? 0})`);
    this.marker.setAttribute('opacity', outside ? '.55' : '1');
    const selected = this.map.points.find(p => p.index === index);
    if (selected) {
      this.selectedMarker.setAttribute('cx', selected.pixel[0]);
      this.selectedMarker.setAttribute('cy', selected.pixel[1]);
    }
    this.label.textContent = `จุด ${index+1}/${this.map.points.at(-1).index+1}${outside ? ' · อยู่นอกแผนที่' : ''}`;
    this.hint.textContent = locked ? 'กด Esc แล้วคลิกจุดเพื่อย้ายตำแหน่ง' : 'คลิกจุดเพื่อย้าย · ใช้ปุ่มลูกศรเมื่อเลือกแผนที่';
  }
}
