/* ── EduAI Classroom ─────────────────────────────────────────────── */
'use strict';

const state = {
  sessionId:     null,
  slides:        [],
  currentSlide:  0,
  ws:            null,
  isPlaying:     false,
  isPaused:      false,
  recording:     false,
  mediaRecorder: null,
  audioChunks:   [],
  ttsBackend:    'browser',   // 'browser' | 'lemonade'
  sttBackend:    'browser',   // 'browser' | 'lemonade'
  browserTTS:    window.speechSynthesis || null,
  browserSTT:    null,
  currentUtter:  null,
};

const audioPlayer = document.getElementById('audio-player');

// ─── Web Speech API setup ──────────────────────────────────────────
function initBrowserSTT() {
  const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRec) { console.warn('Browser STT not supported'); return null; }
  const rec = new SpeechRec();
  rec.continuous    = false;
  rec.interimResults = false;
  rec.lang          = 'en-US';
  return rec;
}

function browserSpeak(text, onEnd) {
  if (!state.browserTTS) { onEnd && onEnd(); return; }
  state.browserTTS.cancel();
  const utt = new SpeechSynthesisUtterance(text);
  utt.rate   = 0.92;
  utt.pitch  = 1.0;
  // Pick a good voice if available
  const voices = state.browserTTS.getVoices();
  const preferred = voices.find(v =>
    v.name.includes('Google') || v.name.includes('Natural') || v.name.includes('Neural')
  ) || voices.find(v => v.lang.startsWith('en')) || voices[0];
  if (preferred) utt.voice = preferred;
  utt.onend = () => { state.currentUtter = null; onEnd && onEnd(); };
  utt.onerror = () => { state.currentUtter = null; onEnd && onEnd(); };
  state.currentUtter = utt;
  state.browserTTS.speak(utt);
}

function stopSpeaking() {
  if (state.browserTTS) state.browserTTS.cancel();
  state.currentUtter = null;
}

// ─── Init ──────────────────────────────────────────────────────────
window.addEventListener('load', () => {
  checkStatus();
  window.addEventListener('resize', scaleSlide);
  // Pre-load voices (Chrome requires a user gesture first)
  if (window.speechSynthesis) {
    window.speechSynthesis.getVoices();
    window.speechSynthesis.onvoiceschanged = () => window.speechSynthesis.getVoices();
  }
});

// ─── Slide scaler ──────────────────────────────────────────────────
function scaleSlide() {
  const scaler   = document.querySelector('.slide-scaler');
  const viewport = document.querySelector('.slide-viewport');
  if (!scaler || !viewport) return;
  const vw    = viewport.clientWidth  - 32;
  const vh    = viewport.clientHeight - 32;
  const scale = Math.min(vw / 960, vh / 540, 1);
  scaler.style.transform = `scale(${scale})`;
}

// ─── Status check ──────────────────────────────────────────────────
async function checkStatus() {
  const dot   = document.getElementById('lemon-dot');
  const label = document.getElementById('lemon-status');
  const chips = document.getElementById('model-chips');
  dot.className = 'dot loading';
  label.textContent = 'Checking Lemonade Server…';
  chips.innerHTML   = '';
  try {
    const data = await fetch('/api/status').then(r => r.json());
    if (data.lemonade.online) {
      dot.className = 'dot online';
      label.textContent = `Lemonade Server — Online`;
      const cfg = data.config;
      chips.innerHTML =
        `<span class="chip">LLM: ${cfg.llm}</span>` +
        `<span class="chip ${cfg.tts_backend==='browser'?'chip-browser':''}">TTS: ${cfg.tts_backend==='browser'?'Browser (free)':cfg.tts}</span>` +
        `<span class="chip ${cfg.stt_backend==='browser'?'chip-browser':''}">STT: ${cfg.stt_backend==='browser'?'Browser (free)':cfg.stt}</span>`;
      data.lemonade.models.slice(0,4).forEach(m =>
        chips.innerHTML += `<span class="chip" style="color:var(--blue)">${m}</span>`);
    } else {
      dot.className = 'dot error';
      label.textContent = 'Lemonade Server — Offline';
    }
  } catch {
    dot.className = 'dot error';
    label.textContent = 'Cannot reach backend — is app.py running?';
  }
}

// ─── Generate ──────────────────────────────────────────────────────
async function startGenerate() {
  const topic  = document.getElementById('topic-input').value.trim();
  const slides = parseInt(document.getElementById('slide-count').value);
  if (!topic) { alert('Please enter a lecture topic.'); return; }

  const btn  = document.getElementById('generate-btn');
  const txt  = document.getElementById('gen-btn-text');
  const spin = document.getElementById('gen-spinner');
  btn.disabled = true;
  txt.textContent = 'Generating…';
  spin.classList.remove('hidden');

  try {
    const res = await fetch('/api/generate', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ prompt: topic, slides }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || 'Failed');
    const data = await res.json();
    state.ttsBackend = data.tts_backend || 'browser';
    state.sttBackend = data.stt_backend || 'browser';
    enterClassroom(data);
  } catch (e) {
    alert('Error: ' + e.message);
  } finally {
    btn.disabled = false;
    txt.textContent = '✨ Generate Lecture';
    spin.classList.add('hidden');
  }
}

// ─── Enter classroom ───────────────────────────────────────────────
function enterClassroom(data) {
  state.sessionId    = data.session_id;
  state.slides       = data.slides;
  state.currentSlide = 0;
  state.isPlaying    = state.isPaused = false;

  document.getElementById('lecture-title').textContent = data.title;
  document.getElementById('slide-counter').textContent = `Slide 0 / ${data.slide_count}`;
  const dl = document.getElementById('download-pptx');
  dl.href = data.pptx_url; dl.download = `${data.title}.pptx`;

  document.getElementById('screen-setup').classList.remove('active');
  const cls = document.getElementById('screen-classroom');
  cls.style.display = 'flex'; cls.classList.add('active');

  document.getElementById('slide-viewport').innerHTML = `
    <div class="slide-scaler" id="slide-scaler">
      <div id="slide-render"></div>
    </div>
    <div class="slide-nav-overlay">
      <button class="nav-arrow" onclick="prevSlide()">‹</button>
      <button class="nav-arrow" onclick="nextSlide()">›</button>
    </div>`;

  // Show STT/TTS mode
  const sttLabel = state.sttBackend === 'browser' ? '🟢 Voice: Browser Speech API (no RAM used)' : '🟡 Voice: Whisper (Lemonade)';
  const ttsLabel = state.ttsBackend === 'browser' ? '🟢 Audio: Browser TTS (no RAM used)' : '🟡 Audio: Kokoro (Lemonade)';
  addChatMsg('system', `📚 "${data.title}" — ${data.slide_count} slides ready.\n${ttsLabel}\n${sttLabel}`);
  addChatMsg('system', '▶ Press Start Lecture to begin.');

  renderSlide(state.slides[0], 0, state.slides.length);
  updateSlideInfo(state.slides[0]);
  updateProgress(0, state.slides.length);
  setTimeout(scaleSlide, 50);
  connectWS(data.session_id);
}

// ─── WebSocket ─────────────────────────────────────────────────────
function connectWS(sessionId) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  state.ws = new WebSocket(`${proto}://${location.host}/ws/${sessionId}`);
  state.ws.onopen    = () => console.log('WS open');
  state.ws.onmessage = e  => handleWS(JSON.parse(e.data));
  state.ws.onclose   = ()  => console.log('WS closed');
}
function wsSend(obj) {
  if (state.ws?.readyState === WebSocket.OPEN) state.ws.send(JSON.stringify(obj));
}

// ─── WS message handler ────────────────────────────────────────────
function handleWS(msg) {
  switch (msg.type) {

    case 'slide_change':
      state.currentSlide = msg.slide_index;
      renderSlide(msg.slide, msg.slide_index, state.slides.length);
      updateSlideInfo(msg.slide);
      updateProgress(msg.slide_index, state.slides.length);
      setStatus('playing');
      stopSpeaking();  // stop previous narration
      break;

    case 'narration':
      document.getElementById('narration-text').textContent = msg.text;
      if (msg.audio_b64) {
        // Lemonade TTS audio
        playAudioB64(msg.audio_b64);
      } else if (msg.use_browser_tts && msg.text) {
        // Browser TTS fallback
        browserSpeak(msg.text);
      }
      break;

    case 'tts_error':
      // Fallback to browser TTS
      const txt = document.getElementById('narration-text').textContent;
      if (txt) browserSpeak(txt);
      break;

    case 'paused':
      state.isPaused = true; setStatus('paused');
      stopSpeaking();
      document.getElementById('btn-pause').textContent = '▶ Resume';
      break;

    case 'resumed':
      state.isPaused = false; setStatus('playing');
      document.getElementById('btn-pause').textContent = '⏸ Pause';
      break;

    case 'waiting_for_resume':
      setStatus('paused');
      break;

    case 'question_received':
      addChatMsg('user', '🎤 ' + msg.text);
      stopSpeaking();
      break;

    case 'answer':
      addChatMsg('ai', msg.answer, '🤖 AI Lecturer');
      if (msg.audio_b64) {
        playAudioB64(msg.audio_b64);
      } else if (msg.use_browser_tts && msg.answer) {
        browserSpeak(msg.answer, () => {
          // Auto-resume after answer if was playing
          if (state.isPlaying) wsSend({ action: 'resume' });
        });
      }
      break;

    case 'stt_error':
      addChatMsg('system', '⚠️ Could not transcribe: ' + msg.message);
      document.getElementById('mic-status').textContent = '';
      break;

    case 'lecture_complete':
      state.isPlaying = false; setStatus('done');
      stopSpeaking();
      document.getElementById('btn-start').disabled   = false;
      document.getElementById('btn-pause').disabled   = true;
      document.getElementById('btn-start').textContent = '↩ Restart';
      addChatMsg('system', '✅ Lecture complete! Ask questions any time.');
      break;

    case 'stopped':
      state.isPlaying = false; setStatus('idle');
      stopSpeaking();
      document.getElementById('btn-start').disabled = false;
      document.getElementById('btn-pause').disabled = true;
      break;

    case 'error':
      addChatMsg('system', '❌ ' + msg.message);
      break;
  }
}

// ─── Controls ──────────────────────────────────────────────────────
function startLecture() {
  if (!state.sessionId) return;
  state.isPlaying = true; state.isPaused = false;
  document.getElementById('btn-start').disabled   = true;
  document.getElementById('btn-pause').disabled   = false;
  document.getElementById('btn-pause').textContent = '⏸ Pause';
  setStatus('playing');
  wsSend({ action: 'start_lecture' });
}

function togglePause() {
  if (state.isPaused) { wsSend({ action: 'resume' }); }
  else { stopSpeaking(); wsSend({ action: 'pause' }); }
}

function nextSlide() { stopSpeaking(); wsSend({ action: 'next_slide' }); }
function prevSlide() { stopSpeaking(); wsSend({ action: 'prev_slide' }); }

function stopLecture() {
  if (!confirm('Stop the current lecture?')) return;
  stopSpeaking();
  wsSend({ action: 'stop' });
  state.isPlaying = false;
}

function backToSetup() {
  stopSpeaking();
  state.ws?.close(); state.ws = null;
  document.getElementById('screen-classroom').classList.remove('active');
  document.getElementById('screen-classroom').style.display = 'none';
  document.getElementById('screen-setup').classList.add('active');
  document.getElementById('chat-messages').innerHTML =
    '<div class="chat-msg system">👋 Enter a topic and generate a new lecture.</div>';
  document.getElementById('btn-start').textContent  = '▶ Start Lecture';
  document.getElementById('btn-start').disabled     = false;
  document.getElementById('btn-pause').disabled     = true;
}

// ─── Microphone — uses Browser Web Speech API (zero RAM) ───────────
async function toggleMic() {
  if (state.recording) { stopMic(); return; }

  // Always try Browser Speech API first (no RAM, works offline)
  if (window.SpeechRecognition || window.webkitSpeechRecognition) {
    startBrowserSTT();
  } else if (state.sttBackend === 'lemonade') {
    startMediaRecorder();
  } else {
    addChatMsg('system', '⚠️ Your browser does not support voice input. Please type your question.');
  }
}

function startBrowserSTT() {
  const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
  const rec = new SpeechRec();
  rec.continuous     = false;
  rec.interimResults = false;
  rec.lang           = 'en-US';
  rec.maxAlternatives = 1;

  state.recording = true;
  document.getElementById('mic-btn').classList.add('recording');
  document.getElementById('mic-icon').textContent  = '⏹';
  document.getElementById('mic-status').textContent = '● Listening…';
  document.getElementById('mic-status').className   = 'mic-status recording';

  rec.onresult = (e) => {
    const text = e.results[0][0].transcript.trim();
    if (text) {
      addChatMsg('user', '🎤 ' + text);
      wsSend({ action: 'ask_question', text });
      if (state.isPlaying && !state.isPaused) {
        stopSpeaking(); wsSend({ action: 'pause' });
      }
    }
  };

  rec.onerror = (e) => {
    addChatMsg('system', '⚠️ Mic error: ' + e.error + '. Try typing your question instead.');
    resetMicUI();
  };

  rec.onend = () => { resetMicUI(); };

  state.browserSTT = rec;
  rec.start();
}

function stopMic() {
  if (state.browserSTT) { state.browserSTT.stop(); state.browserSTT = null; }
  if (state.mediaRecorder?.state !== 'inactive') state.mediaRecorder?.stop();
  resetMicUI();
}

function resetMicUI() {
  state.recording = false;
  document.getElementById('mic-btn').classList.remove('recording');
  document.getElementById('mic-icon').textContent  = '🎤';
  document.getElementById('mic-status').textContent = '';
  document.getElementById('mic-status').className   = 'mic-status';
}

// Fallback: MediaRecorder → send to Lemonade Whisper
function startMediaRecorder() {
  navigator.mediaDevices.getUserMedia({ audio: true }).then(stream => {
    state.audioChunks   = [];
    state.mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
    state.mediaRecorder.ondataavailable = e => { if (e.data.size > 0) state.audioChunks.push(e.data); };
    state.mediaRecorder.onstop = () => {
      const blob   = new Blob(state.audioChunks, { type: 'audio/webm' });
      const reader = new FileReader();
      reader.onload = () => {
        wsSend({ action: 'ask_question', audio_b64: reader.result.split(',')[1] });
        if (state.isPlaying && !state.isPaused) { stopSpeaking(); wsSend({ action: 'pause' }); }
      };
      reader.readAsDataURL(blob);
      stream.getTracks().forEach(t => t.stop());
      resetMicUI();
    };
    state.mediaRecorder.start();
    state.recording = true;
    document.getElementById('mic-btn').classList.add('recording');
    document.getElementById('mic-icon').textContent  = '⏹';
    document.getElementById('mic-status').textContent = '● Recording…';
    document.getElementById('mic-status').className   = 'mic-status recording';
  }).catch(e => alert('Mic error: ' + e.message));
}

// ─── Text question ─────────────────────────────────────────────────
function sendTextQuestion() {
  const inp  = document.getElementById('text-question');
  const text = inp.value.trim();
  if (!text) return;
  inp.value = '';
  wsSend({ action: 'ask_question', text });
  if (state.isPlaying && !state.isPaused) { stopSpeaking(); wsSend({ action: 'pause' }); }
}

// ─── Slide rendering — fixed 960×540 px canvas ─────────────────────
function renderSlide(slide, idx, total) {
  const container = document.getElementById('slide-render');
  if (!container) return;
  const isFirst = idx === 0;
  const isLast  = idx === total - 1;
  const bullets  = (slide.bullets || []).slice(0, 7);

  // Pad to 7
  while (bullets.length < 7) bullets.push('Additional supporting information for this topic.');

  let html = '';

  if (isFirst) {
    html = `<div class="sl-title">
      <div class="t-bar"></div>
      <div class="t-circ"></div><div class="t-circ2"></div>
      <div class="t-badge">🎓 EduAI Classroom</div>
      <h1>${esc(slide.title)}</h1>
      <div class="t-sub">${esc(bullets[0])}</div>
      <div class="t-dots">
        <span style="background:#3bc47a"></span>
        <span style="background:#5ba4f5"></span>
        <span style="background:#f0c060"></span>
      </div>
    </div>`;

  } else if (isLast) {
    const items = bullets.map(b => `
      <div class="sl-sitem"><div class="s-dot"></div>
        <div class="s-text">${esc(b)}</div>
      </div>`).join('');
    html = `<div class="sl-summary">
      <div class="s-topbar"></div><div class="s-deco"></div>
      <div class="s-tag">Key Takeaways</div>
      <h2>${esc(slide.title)}</h2>
      <div class="s-items">${items}</div>
      <div class="s-cta">Questions? Press the mic button anytime!</div>
    </div>`;

  } else {
    const rows = bullets.map((b, i) => `
      <div class="sl-bullet">
        <div class="sl-num">${i + 1}</div>
        <div class="sl-btext">${esc(b)}</div>
      </div>`).join('');
    html = `<div class="sl-content">
      <div class="c-header">
        <div class="c-hbar"></div>
        <h2>${esc(slide.title)}</h2>
        <div class="c-badge">Slide ${idx} / ${total - 2}</div>
      </div>
      <div class="c-body">${rows}</div>
    </div>`;
  }

  container.innerHTML = html;
  document.getElementById('slide-counter').textContent = `Slide ${idx + 1} / ${total}`;
}

function esc(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ─── Slide info sidebar ────────────────────────────────────────────
function updateSlideInfo(slide) {
  document.getElementById('info-title').textContent = slide.title || '';
  document.getElementById('info-bullets').innerHTML =
    (slide.bullets || []).slice(0, 7).map(b => `<li>${esc(b)}</li>`).join('');
}

function updateProgress(idx, total) {
  const pct = total > 1 ? (idx / (total - 1)) * 100 : 0;
  document.getElementById('progress-bar').style.width = pct + '%';
}

function setStatus(s) {
  const el = document.getElementById('lecture-status');
  el.className = 'status-badge ' + s;
  el.textContent = { playing:'▶ Playing', paused:'⏸ Paused', done:'✓ Done', idle:'Idle' }[s] || s;
}

// ─── Audio ─────────────────────────────────────────────────────────
function playAudioB64(b64) {
  const bin  = atob(b64);
  const buf  = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
  const url  = URL.createObjectURL(new Blob([buf], { type: 'audio/mpeg' }));
  audioPlayer.src = url;
  audioPlayer.play().catch(e => console.warn('audio:', e));
}

// ─── Chat ──────────────────────────────────────────────────────────
function addChatMsg(type, text, label) {
  const box = document.getElementById('chat-messages');
  const div = document.createElement('div');
  div.className = 'chat-msg ' + type;
  if (type === 'ai' && label) {
    div.innerHTML = `<div class="msg-label">${esc(label)}</div>${esc(text)}`;
  } else {
    div.textContent = text;
  }
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

// ─── Image Generation ──────────────────────────────────────────────
// Cache: sessionId+slideIdx -> URL (so we don't re-generate)
const imgCache = {};

async function generateSlideImage() {
  if (!state.sessionId) return;

  const btn       = document.getElementById('btn-img');
  const cacheKey  = `${state.sessionId}_${state.currentSlide}`;
  const slide     = state.slides[state.currentSlide];

  // Show cached image immediately
  if (imgCache[cacheKey]) {
    showSlideImage(imgCache[cacheKey]);
    return;
  }

  btn.disabled = true;
  btn.classList.add('loading');
  btn.textContent = '⏳ Generating…';

  // Warn user about timing
  addChatMsg('img-gen',
    `🖼 Generating illustration for "${slide?.title || 'slide'}"…\n` +
    `⚠️ SD-Turbo loads into RAM now (LLM evicts automatically).\n` +
    `CPU mode: ~4-5 min. GPU mode: ~30 sec. Next Q&A will reload LLM (~30s).`
  );

  try {
    const res = await fetch('/api/generate-image', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id:  state.sessionId,
        slide_index: state.currentSlide,
      }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Generation failed');
    }

    const data = await res.json();
    imgCache[cacheKey] = data.url;
    showSlideImage(data.url);
    addChatMsg('system', `✅ Image ready for slide ${state.currentSlide + 1}!`);
  } catch (e) {
    addChatMsg('system', `❌ Image error: ${e.message}`);
  } finally {
    btn.disabled   = false;
    btn.classList.remove('loading');
    btn.textContent = '🖼 Image';
  }
}

function showSlideImage(url) {
  const wrap = document.getElementById('slide-image-wrap');
  const img  = document.getElementById('slide-image');
  img.src    = url + '?t=' + Date.now();  // bust cache
  wrap.classList.remove('hidden');
}

function closeSlideImage() {
  document.getElementById('slide-image-wrap').classList.add('hidden');
}

// Auto-show cached image when slide changes
const _origRenderSlide = renderSlide;
window.renderSlide = function(slide, idx, total) {
  _origRenderSlide(slide, idx, total);
  const key = `${state.sessionId}_${idx}`;
  if (imgCache[key]) {
    showSlideImage(imgCache[key]);
  } else {
    closeSlideImage();  // hide stale image from previous slide
  }
};
