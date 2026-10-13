export const routes = {
  room: {title:'ห้องเดียว', path:'M130 230L130 90L550 90L550 240L140 240L225 165L460 165', start:[130,230], walls:'M70 45H610V285H70Z', caption:'ภาพจำลอง: เดินรอบห้องโดยเว้นระยะจากผนัง แล้วเพิ่มแนวผ่านกลางเพื่อเห็นฉากจากหลายตำแหน่ง'},
  corridor: {title:'ทางเดิน', path:'M95 145L580 145L580 205L110 205', start:[95,145], walls:'M55 95H630V255H55Z', caption:'ภาพจำลอง: เดินต่อเนื่องไปตามทางเดิน หากพื้นที่พอ ให้เดินกลับอีกแนวเพื่อเพิ่มมุมมอง ลดการเคลื่อนกล้องเร็วที่ปลายทาง'},
  rooms: {title:'หลายห้อง', path:'M115 240L115 85L275 85L275 170L420 170L420 85L560 85L560 240L420 240L420 185L275 185L160 220', start:[115,240], walls:'M65 45H325V135M325 210V285H65ZM355 45H615V285H355V210M355 135V45', caption:'ภาพจำลอง: ถ่ายแต่ละห้องและเดินเชื่อมผ่านประตูอย่างต่อเนื่อง เพื่อให้ภาพในสองห้องมีบริเวณที่เชื่อมกัน'},
  repeat: {title:'วนซ้ำแนวเดิม', path:'M130 235L130 90L550 90L550 235L130 235L130 90L550 90L550 235L130 235', start:[130,235], walls:'M70 45H610V285H70Z', caption:'ภาพจำลอง: เดินทับแนวเดิมหลายรอบเพิ่มจำนวนภาพ แต่จุดบอดยังอาจอยู่เดิม รอบเพิ่มควรเยื้องแนวหรือผ่านมุมที่ขาด'}
};

export function selectRoute(root, key) {
  const route=routes[key]; if (!route) return false;
  root.querySelector('#tutorial-route-path').setAttribute('d', route.path);
  root.querySelector('#tutorial-route-path').setAttribute('stroke', key==='repeat'?'#ffbc65':'#92e6c6');
  root.querySelector('#tutorial-room-walls').querySelector('path').setAttribute('d',route.walls);
  root.querySelector('#tutorial-room-walls').querySelector('rect').setAttribute('visibility',key==='room'||key==='repeat'?'visible':'hidden');
  root.querySelector('#tutorial-route-title').textContent='เส้นทางจำลอง · '+route.title;
  root.querySelector('#tutorial-route-description').textContent=route.caption;
  root.querySelector('#tutorial-route-caption').textContent=route.caption;
  const start=root.querySelector('#tutorial-start');start.setAttribute('cx',route.start[0]);start.setAttribute('cy',route.start[1]);
  const label=root.querySelector('#tutorial-start-label');label.setAttribute('x',route.start[0]+15);label.setAttribute('y',route.start[1]-5);
  root.querySelectorAll('[data-route]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.route===key)));
  return true;
}
if (typeof document!=='undefined') {
  document.querySelectorAll('[data-route]').forEach(button=>button.addEventListener('click',()=>selectRoute(document,button.dataset.route)));
}
