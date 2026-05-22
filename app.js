/* ── EduAI Classroom — Frontend JS ────────────────────────────────── */

let state = {
  sessionId:    null,
  slides:       [],
  currentSlide: 0,
  ws:           null,
  isPlaying:    false,
  isPaused:     false,
  mediaRecorder: null,
  recording:    false,
  audioChunks:  [],
  voice:        'af_heart',
};

const audioPlayer = document.getElementById('audio-player');

/* ─── Init ─────────────────────────────────────────────────────── */
window.addEventListener('load', () => {
  checkStatus();
});

/* ─── Status Check ──────────────────────────────────────────────── */
async function checkStatus() {
  const dot    = document.getElementById('lemon-dot');
  const label  = document.getElementById('lemon-status');
  const chips  = document.getElementById('model-chips');
  dot.className = 'dot loading';
  label.textContent = 'Checking Lemonade Server…';
  chips.innerHTML   = '';
  try {
    const res  = await fetch('/api/status');
    const data = await res.json();
    if (data.lemonade.online) {
      dot.className = 'dot online';
      label.textContent = 'Lemonade Server — Online';
      const cfg = data.config;
      chips.innerHTML = [
        `<span class="chip">LLM: ${cfg.llm}</span>`,
        `<span class="chip">TTS: ${cfg.tts}</span>`,
        `<span class="chip">STT: ${cfg.stt}</span>`,
        `<span class="chip">IMG: ${cfg.image}</span>`,
      ].join('');
      if (data.lemonade.models.length > 0) {
        chips.innerHTML += data.lemonade.models.slice(0,4)
          .map(m => `<span class="chip" style="color:var(--blue)">${m}</span>`).join('');
      }
    } else {
      dot.className = 'dot error';
      label.textContent = 'Lemonade Server — Offline (start it first)';
    }
  } catch (e) {
    dot.className = 'dot error';
    label.textContent = 'Cannot reach backend — is app.py running?';
  }
}

/* ─── Generate Lecture ──────────────────────────────────────────── */
async function startGenerate() {
  const topic  = document.getElementById('topic-input').value.trim();
  const slides = document.getElementById('slide-count').value;
  state.voice  = document.getElementById('tts-voice').value;

  if (!topic) { alert('Please enter a lecture topic.'); return; }

  const btn  = document.getElementById('generate-btn');
  const txt  = document.getElementById('gen-btn-text');
  const spin = document.getElementById('gen-spinner');
  btn.disabled = true;
  txt.textContent = 'Generating…';
  spin.classList.remove('hidden');

  try {
    const res  = await fetch('/api/generate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ prompt: topic, slides: parseInt(slides) }),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Generation failed');
    }
    const data = await res.json();
    enterClassroom(data);
  } catch (e) {
    alert('Error: ' + e.message);
  } finally {
    btn.disabled = false;
    txt.textContent = '✨ Generate Lecture';
    spin.classList.add('hidden');
  }
}

/* ─── Enter Classroom ───────────────────────────────────────────── */
function enterClassroom(data) {
  state.sessionId    = data.session_id;
  state.slides       = data.slides;
  state.currentSlide = 0;
  state.isPlaying    = false;
  state.isPaused     = false;

  document.getElementById('lecture-title').textContent = data.title;
  document.getElementById('slide-counter').textContent = `Slide 0 / ${data.slide_count}`;
  const dl = document.getElementById('download-pptx');
  dl.href  = data.pptx_url;
  dl.download = `${data.title}.pptx`;

  // Switch screens
  document.getElementById('screen-setup').classList.remove('active');
  const cls = document.getElementById('screen-classroom');
  cls.style.display = 'flex';
  cls.classList.add('active');

  // Show first slide preview
  renderSlideHTML(state.slides[0], 0, state.slides.length);
  updateSlideInfo(state.slides[0]);
  updateProgress(0, state.slides.length);

  // Connect WebSocket
  connectWS(data.session_id);

  addChatMsg('system', `📚 Lecture ready: "${data.title}" — ${data.slide_count} slides. Press ▶ Start Lecture.`);
}

/* ─── WebSocket ─────────────────────────────────────────────────── */
function connectWS(sessionId) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws    = new WebSocket(`${proto}://${location.host}/ws/${sessionId}`);
  state.ws    = ws;

  ws.onopen = () => console.log('WS connected');

  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    handleWSMessage(msg);
  };

  ws.onclose = () => console.log('WS closed');
  ws.onerror = (e) => console.error('WS error', e);
}

function wsSend(obj) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN)
    state.ws.send(JSON.stringify(obj));
}

function handleWSMessage(msg) {
  switch (msg.type) {

    case 'slide_change':
      state.currentSlide = msg.slide_index;
      renderSlideHTML(msg.slide, msg.slide_index, state.slides.length);
      updateSlideInfo(msg.slide);
      updateProgress(msg.slide_index, state.slides.length);
      setStatusBadge('playing');
      break;

    case 'narration':
      document.getElementById('narration-text').textContent = msg.text;
      if (msg.audio_b64) playAudioB64(msg.audio_b64);
      break;

    case 'tts_error':
      addChatMsg('system', '⚠️ TTS error: ' + msg.message);
      break;

    case 'paused':
      state.isPaused = true;
      setStatusBadge('paused');
      document.getElementById('btn-pause').textContent = '▶ Resume';
      break;

    case 'resumed':
      state.isPaused = false;
      setStatusBadge('playing');
      document.getElementById('btn-pause').textContent = '⏸ Pause';
      break;

    case 'waiting_for_resume':
      setStatusBadge('paused');
      break;

    case 'question_received':
      addChatMsg('user', '🎤 ' + msg.text);
      break;

    case 'answer':
      addChatMsg('ai', msg.answer, '🤖 AI Lecturer');
      if (msg.audio_b64) playAudioB64(msg.audio_b64);
      break;

    case 'lecture_complete':
      state.isPlaying = false;
      setStatusBadge('done');
      document.getElementById('btn-start').disabled  = false;
      document.getElementById('btn-pause').disabled  = true;
      document.getElementById('btn-start').textContent = '↩ Restart';
      addChatMsg('system', '✅ Lecture complete! You can still ask questions.');
      break;

    case 'stopped':
      state.isPlaying = false;
      setStatusBadge('idle');
      document.getElementById('btn-start').disabled = false;
      document.getElementById('btn-pause').disabled = true;
      break;

    case 'error':
      addChatMsg('system', '❌ ' + msg.message);
      break;
  }
}

/* ─── Controls ──────────────────────────────────────────────────── */
function startLecture() {
  if (!state.sessionId) return;
  state.isPlaying = true;
  state.isPaused  = false;
  document.getElementById('btn-start').disabled = true;
  document.getElementById('btn-pause').disabled = false;
  document.getElementById('btn-pause').textContent = '⏸ Pause';
  setStatusBadge('playing');
  wsSend({ action: 'start_lecture' });
  addChatMsg('system', '▶ Lecture started…');
}

function togglePause() {
  if (state.isPaused) {
    state.isPaused = false;
    wsSend({ action: 'resume' });
  } else {
    state.isPaused = true;
    wsSend({ action: 'pause' });
  }
}

function nextSlide() { wsSend({ action: 'next_slide' }); }
function prevSlide() { wsSend({ action: 'prev_slide' }); }

function stopLecture() {
  if (confirm('Stop the current lecture?')) {
    wsSend({ action: 'stop' });
    state.isPlaying = false;
  }
}

function backToSetup() {
  if (state.ws) { state.ws.close(); state.ws = null; }
  document.getElementById('screen-classroom').classList.remove('active');
  document.getElementById('screen-classroom').style.display = 'none';
  document.getElementById('screen-setup').classList.add('active');
  document.getElementById('chat-messages').innerHTML =
    '<div class="chat-msg system">👋 Start the lecture, then press the mic button or type to ask questions at any time.</div>';
  document.getElementById('btn-start').disabled  = false;
  document.getElementById('btn-pause').disabled  = true;
  document.getElementById('btn-start').textContent = '▶ Start Lecture';
}

/* ─── Slide Rendering (HTML) ────────────────────────────────────── */
function renderSlideHTML(slide, idx, total) {
  const container = document.getElementById('slide-render');
  const isFirst   = idx === 0;
  const isLast    = idx === total - 1;

  let html = '';

  if (isFirst) {
    html = `
      <div class="sl-title-slide">
        <div class="accent-bar"></div>
        <div class="deco-circle"></div>
        <div class="lbl">AI CLASSROOM · EDUAI</div>
        <h1>${esc(slide.title)}</h1>
        <div class="sub">${esc((slide.bullets || [])[0] || '')}</div>
      </div>`;
  } else if (isLast) {
    const items = (slide.bullets || []).map(b =>
      `<div class="sl-summary-item">
        <div class="sl-summary-bar" style="min-height:3em"></div>
        <div class="sl-summary-text">${esc(b)}</div>
      </div>`).join('');
    html = `
      <div class="sl-summary-slide">
        <div class="top-bar"></div>
        <div class="tag">Summary</div>
        <h2>${esc(slide.title)}</h2>
        ${items}
        <div class="sl-cta">Questions? Ask the AI lecturer anytime!</div>
      </div>`;
  } else {
    const contentIdx = idx;     // relative position
    const bullets = (slide.bullets || []).slice(0, 7).map((b, i) =>
      `<div class="sl-bullet-row">
        <div class="sl-num">${i + 1}</div>
        <div class="sl-bullet-text">${esc(b)}</div>
      </div>`).join('');
    html = `
      <div class="sl-content-slide">
        <div class="hdr">
          <div class="hdr-bar"></div>
          <h2>${esc(slide.title)}</h2>
          <div class="badge">${contentIdx} / ${total - 2}</div>
        </div>
        <div class="bullets">${bullets}</div>
      </div>`;
  }

  container.innerHTML = html;
  document.getElementById('slide-counter').textContent =
    `Slide ${idx + 1} / ${total}`;
}

function esc(str) {
  return String(str || '')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

/* ─── Slide Info Panel ──────────────────────────────────────────── */
function updateSlideInfo(slide) {
  document.getElementById('info-title').textContent = slide.title || '';
  const ul = document.getElementById('info-bullets');
  ul.innerHTML = (slide.bullets || []).slice(0,5)
    .map(b => `<li>${esc(b)}</li>`).join('');
}

function updateProgress(idx, total) {
  const pct = total > 1 ? ((idx) / (total - 1)) * 100 : 0;
  document.getElementById('progress-bar').style.width = pct + '%';
}

function setStatusBadge(status) {
  const el = document.getElementById('lecture-status');
  el.className = 'status-badge ' + status;
  const labels = { playing:'▶ Playing', paused:'⏸ Paused', done:'✓ Complete', idle:'Idle' };
  el.textContent = labels[status] || status;
}

/* ─── Audio ─────────────────────────────────────────────────────── */
function playAudioB64(b64) {
  const blob = b64ToBlob(b64, 'audio/mpeg');
  const url  = URL.createObjectURL(blob);
  audioPlayer.src = url;
  audioPlayer.play().catch(e => console.warn('Audio play error:', e));
}

function b64ToBlob(b64, mime) {
  const bin  = atob(b64);
  const buf  = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
  return new Blob([buf], { type: mime });
}

/* ─── Microphone (Voice Question) ───────────────────────────────── */
async function toggleMic() {
  if (state.recording) {
    stopMic();
  } else {
    startMic();
  }
}

async function startMic() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    state.audioChunks  = [];
    state.mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
    state.mediaRecorder.ondataavailable = e => {
      if (e.data.size > 0) state.audioChunks.push(e.data);
    };
    state.mediaRecorder.onstop = sendVoiceQuestion;
    state.mediaRecorder.start();
    state.recording = true;
    document.getElementById('mic-btn').classList.add('recording');
    document.getElementById('mic-icon').textContent = '⏹';
    document.getElementById('mic-status').textContent = '● Recording…';
    document.getElementById('mic-status').className = 'mic-status recording';
  } catch (e) {
    alert('Microphone access denied: ' + e.message);
  }
}

function stopMic() {
  if (state.mediaRecorder && state.recording) {
    state.mediaRecorder.stop();
    state.mediaRecorder.stream.getTracks().forEach(t => t.stop());
    state.recording = false;
    document.getElementById('mic-btn').classList.remove('recording');
    document.getElementById('mic-icon').textContent = '🎤';
    document.getElementById('mic-status').textContent = 'Processing…';
    document.getElementById('mic-status').className = 'mic-status';
  }
}

async function sendVoiceQuestion() {
  const blob   = new Blob(state.audioChunks, { type: 'audio/webm' });
  const reader = new FileReader();
  reader.onload = () => {
    const b64 = reader.result.split(',')[1];
    wsSend({ action: 'ask_question', audio_b64: b64 });
    // Pause lecture while answering
    if (state.isPlaying && !state.isPaused) {
      wsSend({ action: 'pause' });
    }
  };
  reader.readAsDataURL(blob);
  document.getElementById('mic-status').textContent = '';
}

/* ─── Text Question ─────────────────────────────────────────────── */
function sendTextQuestion() {
  const input = document.getElementById('text-question');
  const text  = input.value.trim();
  if (!text) return;
  input.value = '';
  wsSend({ action: 'ask_question', text });
  if (state.isPlaying && !state.isPaused) wsSend({ action: 'pause' });
}

/* ─── Chat Messages ─────────────────────────────────────────────── */
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
