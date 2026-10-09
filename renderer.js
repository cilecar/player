'use strict';

/* ── State ── */
const S = { tracks: [], cur: -1, playing: false, shuffle: false, repeat: 0 };

/* ── Audio & Web Audio ── */
const aud = new Audio();
aud.volume = 0.8;
let actx, analyser, mediaSrc;

function ensureCtx() {
  if (actx) { if (actx.state === 'suspended') actx.resume(); return; }
  actx = new AudioContext();
  analyser = actx.createAnalyser();
  analyser.fftSize = 2048;
  analyser.smoothingTimeConstant = 0.83;
  mediaSrc = actx.createMediaElementSource(aud);
  mediaSrc.connect(analyser);
  analyser.connect(actx.destination);
}

/* ── DOM refs ── */
const $ = id => document.getElementById(id);
const trackList  = $('track-list');
const metaTitle  = $('meta-title');
const metaArtist = $('meta-artist');
const artWrap    = $('art-wrap');
const artImg     = $('art-img');
const artPh      = $('art-ph');
const artPhCtx   = artPh.getContext('2d');
const waveEl     = $('wave');
const waveCtx    = waveEl.getContext('2d');
const progBar    = $('prog-bar');
const progFill   = $('prog-fill');
const tCur       = $('t-cur');
const tTot       = $('t-tot');
const playBtn    = $('play-btn');
const icoPlay    = $('ico-play');
const icoPause   = $('ico-pause');
const volSlider  = $('vol');
const shuffleBtn = $('shuffle-btn');
const repeatBtn  = $('repeat-btn');
const dropOl     = $('drop-ol');

/* ── Waveform canvas ── */
let wDPR = 1;
function resizeWave() {
  wDPR = window.devicePixelRatio || 1;
  const rect = waveEl.getBoundingClientRect();
  waveEl.width  = rect.width  * wDPR;
  waveEl.height = 64 * wDPR;
}
new ResizeObserver(resizeWave).observe(waveEl);
resizeWave();

function drawWave() {
  requestAnimationFrame(drawWave);
  const PW = waveEl.width, PH = waveEl.height;
  const CW = PW / wDPR, CH = PH / wDPR;
  waveCtx.clearRect(0, 0, PW, PH);
  waveCtx.save();
  waveCtx.scale(wDPR, wDPR);

  const acc  = '#C8943A';
  const cy   = CH / 2;
  const maxA = cy - 5;

  if (!analyser || !S.playing) {
    const t = Date.now() / 1000;
    const amp = maxA * 0.2;
    waveCtx.beginPath();
    for (let x = 0; x <= CW; x++) {
      const env = Math.sin((x / CW) * Math.PI);
      const v   = Math.sin((x / CW) * Math.PI * 3.4 + t * 0.45) * amp * env;
      x === 0 ? waveCtx.moveTo(x, cy - Math.abs(v)) : waveCtx.lineTo(x, cy - Math.abs(v));
    }
    for (let x = CW; x >= 0; x--) {
      const env = Math.sin((x / CW) * Math.PI);
      const v   = Math.sin((x / CW) * Math.PI * 3.4 + t * 0.45) * amp * env;
      waveCtx.lineTo(x, cy + Math.abs(v));
    }
    waveCtx.closePath();
    waveCtx.fillStyle = 'rgba(200,148,58,.07)';
    waveCtx.fill();
    waveCtx.restore();
    return;
  }

  const n    = analyser.frequencyBinCount;
  const raw  = new Uint8Array(n);
  analyser.getByteTimeDomainData(raw);
  const pts  = 256;
  const step = n / pts;
  const top  = new Float32Array(pts * 2);

  for (let i = 0; i < pts; i++) {
    const v = (raw[Math.floor(i * step)] / 128.0) - 1.0;
    top[i * 2]     = (i / (pts - 1)) * CW;
    top[i * 2 + 1] = cy - Math.abs(v) * maxA;
  }

  // Filled lens
  waveCtx.beginPath();
  waveCtx.moveTo(top[0], top[1]);
  for (let i = 1; i < pts; i++) {
    const px = top[(i-1)*2], py = top[(i-1)*2+1];
    const cx = top[i*2],     cy2 = top[i*2+1];
    waveCtx.quadraticCurveTo(px, py, (px+cx)/2, (py+cy2)/2);
  }
  waveCtx.lineTo(top[(pts-1)*2], top[(pts-1)*2+1]);
  for (let i = pts - 1; i >= 0; i--) {
    const x = top[i*2], my = cy + (cy - top[i*2+1]);
    if (i === pts - 1) { waveCtx.lineTo(x, my); }
    else {
      const nx = top[(i+1)*2], nmy = cy + (cy - top[(i+1)*2+1]);
      waveCtx.quadraticCurveTo(nx, nmy, (x+nx)/2, (my+nmy)/2);
    }
  }
  waveCtx.closePath();

  const grad = waveCtx.createLinearGradient(0, 0, 0, CH);
  grad.addColorStop(0,   'rgba(200,148,58,.42)');
  grad.addColorStop(.5,  'rgba(200,148,58,.09)');
  grad.addColorStop(1,   'rgba(200,148,58,.42)');
  waveCtx.fillStyle = grad;
  waveCtx.fill();

  function strokeCurve(getY) {
    waveCtx.beginPath();
    waveCtx.moveTo(top[0], getY(0));
    for (let i = 1; i < pts; i++) {
      const px = top[(i-1)*2], py = getY(i-1);
      const cx = top[i*2],     cy2 = getY(i);
      waveCtx.quadraticCurveTo(px, py, (px+cx)/2, (py+cy2)/2);
    }
    waveCtx.strokeStyle = acc;
    waveCtx.lineWidth   = 1.3;
    waveCtx.stroke();
  }
  strokeCurve(i => top[i*2+1]);
  strokeCurve(i => cy + (cy - top[i*2+1]));

  waveCtx.restore();
}
drawWave();

/* ── Album art placeholder ── */
function drawPlaceholder(title, artist) {
  const W = 210, H = 210;
  artPhCtx.clearRect(0, 0, W, H);
  let h = 0;
  for (let i = 0; i < title.length + artist.length; i++)
    h = (h * 31 + (title + artist).charCodeAt(i)) & 0xFFFF;
  const hue = h % 360;

  const bg = artPhCtx.createRadialGradient(W/2, H/2, 18, W/2, H/2, W * .76);
  bg.addColorStop(0, `hsl(${hue},22%,17%)`);
  bg.addColorStop(1, `hsl(${hue},14%,7%)`);
  artPhCtx.fillStyle = bg;
  artPhCtx.fillRect(0, 0, W, H);

  artPhCtx.strokeStyle = 'rgba(255,255,255,.045)';
  artPhCtx.lineWidth = 1;
  for (let r = 14; r < 100; r += 8) {
    artPhCtx.beginPath(); artPhCtx.arc(W/2, H/2, r, 0, Math.PI*2); artPhCtx.stroke();
  }
  const lg = artPhCtx.createRadialGradient(W/2, H/2-3, 2, W/2, H/2, 30);
  lg.addColorStop(0, `hsl(${hue},38%,28%)`);
  lg.addColorStop(1, `hsl(${hue},28%,18%)`);
  artPhCtx.beginPath(); artPhCtx.arc(W/2, H/2, 30, 0, Math.PI*2);
  artPhCtx.fillStyle = lg; artPhCtx.fill();
  if (title) {
    artPhCtx.fillStyle = `hsla(${hue},55%,88%,.88)`;
    artPhCtx.font = '600 20px system-ui, sans-serif';
    artPhCtx.textAlign = 'center'; artPhCtx.textBaseline = 'middle';
    artPhCtx.fillText(title[0].toUpperCase(), W/2, H/2+1);
  }
}

function showArt(track) {
  if (track.artwork) {
    artImg.src = track.artwork;
    artImg.style.display = 'block';
    artPh.style.display  = 'none';
  } else {
    artImg.style.display = 'none';
    artPh.style.display  = 'block';
    drawPlaceholder(track.title, track.artist);
  }
  artWrap.classList.add('lit');
}

/* ── Utils ── */
function fmt(s) {
  if (!isFinite(s) || isNaN(s) || s <= 0) return '0:00';
  return `${Math.floor(s/60)}:${String(Math.floor(s%60)).padStart(2,'0')}`;
}
function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

/* ── DOM list helpers — NO full re-renders on track switch ── */

const THUMB_PH = `<div class="item-thumb-placeholder">
  <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
    <path d="M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6z"/>
  </svg></div>`;

function makeItemEl(t, i) {
  const div = document.createElement('div');
  div.className = 'track-item';
  div.dataset.i  = i;

  const thumb = document.createElement('div');
  thumb.className = 'item-thumb';
  if (t.artwork) {
    const img = new Image();
    img.src = t.artwork;
    img.alt = '';
    thumb.appendChild(img);
  } else {
    thumb.innerHTML = THUMB_PH;
  }
  const bars = document.createElement('div');
  bars.className = 'item-bars';
  bars.innerHTML = '<div class="bar"></div><div class="bar"></div><div class="bar"></div>';
  thumb.appendChild(bars);

  const info = document.createElement('div');
  info.className = 'item-info';
  info.innerHTML = `<div class="item-title">${esc(t.title)}</div>
    <div class="item-artist">${esc(t.artist || 'Unknown artist')}</div>`;

  const dur = document.createElement('div');
  dur.className   = 'item-dur';
  dur.textContent = t.duration ? fmt(t.duration) : '—';

  div.append(thumb, info, dur);
  div.addEventListener('click', () => {
    const idx = +div.dataset.i;
    idx === S.cur ? togglePlay() : (loadTrack(idx), play());
  });
  return div;
}

function appendItems(tracks, startIdx) {
  const frag = document.createDocumentFragment();
  tracks.forEach((t, j) => frag.appendChild(makeItemEl(t, startIdx + j)));
  trackList.appendChild(frag);
}

/* Update only the two touched items when switching tracks */
function setActiveItem(oldIdx, newIdx) {
  if (oldIdx >= 0) {
    const el = trackList.querySelector(`.track-item[data-i="${oldIdx}"]`);
    el?.classList.remove('active', 'paused');
  }
  if (newIdx >= 0) {
    const el = trackList.querySelector(`.track-item[data-i="${newIdx}"]`);
    if (el) {
      el.classList.add('active');
      if (!S.playing) el.classList.add('paused');
      el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }
}

function updatePlayState() {
  const el = trackList.querySelector('.track-item.active');
  if (!el) return;
  el.classList.toggle('paused', !S.playing);
}

function showEmptyState() {
  trackList.innerHTML = `<div class="empty"><div class="empty-icon">◎</div>
    <p>Drop audio files here<br>or press + to add tracks</p></div>`;
}

/* ── Load track (no DOM list rebuild) ── */
function loadTrack(i) {
  if (i < 0 || i >= S.tracks.length) return;
  const prev = S.cur;
  S.cur = i;
  const t = S.tracks[i];
  aud.src = `file://${t.path}`;
  metaTitle.textContent  = t.title;
  metaArtist.textContent = t.artist || 'Unknown artist';
  tTot.textContent       = t.duration ? fmt(t.duration) : '—';
  tCur.textContent       = '0:00';
  progFill.style.width   = '0%';
  showArt(t);
  setActiveItem(prev, i);
  document.title = t.artist ? `${t.title} · ${t.artist}` : t.title;
}

/* ── Playback ── */
function play() {
  ensureCtx();
  aud.play().catch(e => console.error('play:', e));
  S.playing = true;
  icoPlay.style.display  = 'none';
  icoPause.style.display = '';
  updatePlayState();
}

function pause() {
  aud.pause();
  S.playing = false;
  icoPlay.style.display  = '';
  icoPause.style.display = 'none';
  updatePlayState();
}

function togglePlay() {
  if (!S.tracks.length) return;
  if (S.cur === -1) { loadTrack(0); play(); return; }
  S.playing ? pause() : play();
}

function next() {
  if (!S.tracks.length) return;
  if (S.repeat === 2) { aud.currentTime = 0; if (S.playing) play(); return; }
  let n;
  if (S.shuffle) { n = Math.floor(Math.random() * S.tracks.length); }
  else {
    n = (S.cur + 1) % S.tracks.length;
    if (n === 0 && S.repeat === 0) { pause(); return; }
  }
  loadTrack(n);
  if (S.playing) play();
}

function prev() {
  if (!S.tracks.length) return;
  if (aud.currentTime > 3) { aud.currentTime = 0; return; }
  const n = S.shuffle
    ? Math.floor(Math.random() * S.tracks.length)
    : (S.cur - 1 + S.tracks.length) % S.tracks.length;
  loadTrack(n);
  if (S.playing) play();
}

/* ── Progress ── */
aud.addEventListener('timeupdate', () => {
  if (!aud.duration) return;
  progFill.style.width = `${(aud.currentTime / aud.duration) * 100}%`;
  tCur.textContent = fmt(aud.currentTime);
});

aud.addEventListener('loadedmetadata', () => {
  if (S.cur >= 0 && !S.tracks[S.cur].duration) {
    S.tracks[S.cur].duration = aud.duration;
    tTot.textContent = fmt(aud.duration);
    // Update duration in list without re-rendering
    const el = trackList.querySelector(`.track-item[data-i="${S.cur}"] .item-dur`);
    if (el) el.textContent = fmt(aud.duration);
  }
});

aud.addEventListener('ended', next);

progBar.addEventListener('click', e => {
  if (!aud.duration) return;
  const r = progBar.getBoundingClientRect();
  aud.currentTime = ((e.clientX - r.left) / r.width) * aud.duration;
});

/* ── Volume ── */
volSlider.addEventListener('input', () => { aud.volume = +volSlider.value; });

/* ── Shuffle / Repeat ── */
shuffleBtn.addEventListener('click', () => {
  S.shuffle = !S.shuffle;
  shuffleBtn.classList.toggle('on', S.shuffle);
});

const SVG_REPEAT_OFF = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 1 21 5 17 9"/><polyline points="7 23 3 19 7 15"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>`;
const SVG_REPEAT_ONE = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 1 21 5 17 9"/><polyline points="7 23 3 19 7 15"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/><text x="12" y="14.5" text-anchor="middle" font-size="7" font-weight="700" fill="currentColor" stroke="none">1</text></svg>`;

repeatBtn.addEventListener('click', () => {
  S.repeat = (S.repeat + 1) % 3;
  repeatBtn.classList.toggle('on', S.repeat > 0);
  repeatBtn.title   = ['Repeat: off','Repeat: all','Repeat: one'][S.repeat];
  repeatBtn.innerHTML = S.repeat === 2 ? SVG_REPEAT_ONE : SVG_REPEAT_OFF;
});

/* ── Controls ── */
playBtn.addEventListener('click', togglePlay);
$('prev-btn').addEventListener('click', prev);
$('next-btn').addEventListener('click', next);

/* ── File loading (+ button) ── */
async function loadFiles(paths) {
  if (!paths.length) return;
  const meta = await window.api.getMetadata(paths);
  const startIdx = S.tracks.length;
  if (!startIdx) trackList.innerHTML = ''; // clear empty state
  S.tracks.push(...meta);
  appendItems(meta, startIdx);
  if (S.cur === -1 && meta.length) loadTrack(startIdx);
}

$('add-btn').addEventListener('click', async () => {
  const paths = await window.api.openFiles();
  await loadFiles(paths);
});

/* ── Library load on startup (streaming) ── */
async function loadLibrary() {
  trackList.innerHTML = `<div class="empty"><div class="empty-icon">◎</div><p>Scanning library…</p></div>`;

  const paths = await window.api.scanLibrary();
  if (!paths.length) { showEmptyState(); return; }

  trackList.innerHTML = ''; // clear loading state
  S.tracks = [];

  window.api.onTracksBatch(batch => {
    const startIdx = S.tracks.length;
    S.tracks.push(...batch);
    appendItems(batch, startIdx);
    if (S.cur === -1) loadTrack(0); // load first track as soon as it arrives
  });

  window.api.onTracksDone(() => {
    window.api.offTracksBatch();
  });

  window.api.startLibraryScan(paths);
}
loadLibrary();

/* ── Drag & drop ── */
let dragCnt = 0;
document.addEventListener('dragenter', e => { e.preventDefault(); dragCnt++; dropOl.classList.add('show'); });
document.addEventListener('dragleave', () => { if (--dragCnt <= 0) { dragCnt = 0; dropOl.classList.remove('show'); } });
document.addEventListener('dragover',  e => e.preventDefault());
document.addEventListener('drop', async e => {
  e.preventDefault(); dragCnt = 0; dropOl.classList.remove('show');
  const paths = Array.from(e.dataTransfer.files)
    .filter(f => /\.(mp3|flac|ogg|wav|aac|m4a|opus|weba|webm)$/i.test(f.name))
    .map(f => f.path);
  await loadFiles(paths);
});

/* ── Keyboard ── */
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT') return;
  switch (e.code) {
    case 'Space':      e.preventDefault(); togglePlay(); break;
    case 'ArrowRight': e.preventDefault(); aud.currentTime = Math.min(aud.duration||0, aud.currentTime+5); break;
    case 'ArrowLeft':  e.preventDefault(); aud.currentTime = Math.max(0, aud.currentTime-5); break;
    case 'ArrowUp':    e.preventDefault(); aud.volume = Math.min(1, aud.volume+.05); volSlider.value = aud.volume; break;
    case 'ArrowDown':  e.preventDefault(); aud.volume = Math.max(0, aud.volume-.05); volSlider.value = aud.volume; break;
  }
});
