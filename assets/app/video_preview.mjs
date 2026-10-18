// Keep the video element and timeline stable when native decoding fails.
const videoPreviews = new WeakMap();

function attachVideoPreview(video, source = video.dataset.videoSource) {
  if (!source || videoPreviews.has(video)) return;
  const panel = document.createElement('div');
  panel.className = 'video-preview-status';
  panel.style.cssText = 'display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:10px;font-size:12px';
  const message = document.createElement('span'), button = document.createElement('button');
  message.setAttribute('role', 'status'); message.setAttribute('aria-live', 'polite');
  button.type = 'button'; button.textContent = 'เล่น Preview';
  message.textContent = 'เล่นไม่ได้? ใช้ Preview สำหรับเว็บ';
  panel.append(message, button); video.insertAdjacentElement('afterend', panel);
  const state = {phase:'original', wanted:false, time:0, timer:null, pending:false};
  videoPreviews.set(video, state);
  const endpoint = '/api/video-preview?' + new URLSearchParams({source});
  function failed(error) {
    clearTimeout(state.timer); state.phase = 'error';
    message.textContent = error.message; button.textContent = 'ลองอีกครั้ง'; button.hidden = false;
  }
  function resume() {
    // Closing a trim dialog while preparing must never start invisible playback.
    if (!state.wanted || (video.closest('dialog') && !video.closest('dialog').open)) return;
    video.play().catch(() => {
      state.wanted = false;
      message.textContent = 'Preview พร้อมแล้ว กด Play เพื่อเล่น';
      button.hidden = false; button.textContent = 'เล่น Preview';
    });
  }
  async function check(start = false) {
    if (state.pending) return;
    state.pending = true;
    try {
      const response = await fetch(endpoint, {method:start ? 'POST' : 'GET', cache:'no-store', signal:AbortSignal.timeout(10000)});
      const result = await response.json();
      if (!response.ok || result.state === 'error') throw new Error(result.error || 'เตรียม Preview ไม่สำเร็จ');
      if (result.state === 'ready') {
        state.phase = 'preview';
        message.textContent = 'Preview สำหรับเว็บ · การสร้าง Views / Model ยังใช้ไฟล์ต้นฉบับ';
        button.hidden = true;
        video.addEventListener('loadedmetadata', () => {
          video.currentTime = Math.max(0, Math.min(state.time, Math.max(0, video.duration - .05)));
          resume();
        }, {once:true});
        video.src = result.url; video.load();
      } else {
        message.textContent = result.state === 'running'
          ? `กำลังเตรียม Preview ${Math.floor(result.progress || 0)}% · ครั้งแรกอาจใช้เวลาสักครู่`
          : 'รอคิวเตรียม Preview · เตรียมครั้งเดียวแล้วเปิดเล่นได้จากทุกหน้า';
        state.timer = setTimeout(() => check(), 1000);
      }
    } catch (error) { failed(error); }
    finally { state.pending = false; }
  }
  function prepare() {
    if (state.phase === 'preparing') return;
    state.time = Number.isFinite(video.currentTime) ? video.currentTime : 0;
    state.phase = 'preparing'; button.hidden = true;
    message.textContent = 'กำลังเตรียมวิดีโอ Preview สำหรับเว็บ…';
    // Do not keep downloading/decoding an incompatible 8K original in parallel.
    video.pause(); video.removeAttribute('src');
    for (const child of video.querySelectorAll('source')) child.removeAttribute('src');
    video.load();
    check(true);
  }
  button.addEventListener('click', () => {
    state.wanted = true;
    if (state.phase === 'preview') resume();
    else prepare();
  });
  video.addEventListener('play', () => {state.wanted = true;});
  video.addEventListener('pause', () => {
    if (!video.error && state.phase !== 'preparing') state.wanted = false;
  });
  video.addEventListener('error', () => {
    if (state.phase === 'original') prepare();
    else if (state.phase === 'preview') failed(new Error('เล่น Preview ไม่สำเร็จ กรุณาลองอีกครั้ง'));
  });
  video.closest('dialog')?.addEventListener('close', () => {state.wanted = false; video.pause();});
  if (video.error) prepare();
}

document.querySelectorAll('video[data-video-source]').forEach(video => attachVideoPreview(video));
document.addEventListener('video-preview-source', event => attachVideoPreview(event.detail.video, event.detail.source));
