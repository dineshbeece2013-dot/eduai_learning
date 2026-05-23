'use strict';

const state = {
  sessionId:      null,
  slides:         [],
  currentSlide:   0,
  ws:             null,
  isPlaying:      false,
  isPaused:       false,
  recording:      false,
  browserSTT:     null,
  ttsBackend:     'browser',
  sttBackend:     'browser',
  subtitlesOn:    false,
  quizPending:    null,   // current quiz data while modal open
};

const audioPlayer = document.getElementById('audio-player');

// ── Web Speech init ───────────────────────────────────────────────
window.addEventListener('load', () => {
  checkStatus();
  window.addEventListener('resize', scaleSlide);
  if (window.speechSynthesis) {
    window.speechSynthesis.getVoices();
    window.speechSynthesis.onvoiceschanged = () => window.speechSynthesis.getVoices();
  }
});

function scaleSlide() {
  const scaler   = document.querySelector('.slide-scaler');
  const viewport = document.querySelector('.slide-viewport');
  if (!scaler || !viewport) return;
  const vw    = viewport.clientWidth  - 28;
  const vh    = viewport.clientHeight - 28;
  const scale = Math.min(vw / 960, vh / 540, 1);
  scaler.style.transform = `scale(${scale})`;
}

// ── Status (no model names shown) ────────────────────────────────
async function checkStatus() {
  const dot   = document.getElementById('lemon-dot');
  const label = document.getElementById('lemon-status');
  dot.className = 'dot loading';
  label.textContent = 'Checking AI Server…';
  try {
    const data = await fetch('/api/status').then(r => r.json());
    if (data.lemonade.online) {
      dot.className    = 'dot online';
      label.textContent = 'AI Server — Ready';
    } else {
      dot.className    = 'dot error';
      label.textContent = 'AI Server — Offline (start Lemonade first)';
    }
  } catch {
    dot.className    = 'dot error';
    label.textContent = 'Cannot reach backend — is app.py running?';
  }
}

// ── Generate ──────────────────────────────────────────────────────
async function startGenerate() {
  const topic  = document.getElementById('topic-input').value.trim();
  const slides = parseInt(document.getElementById('slide-count').value);
  if (!topic) { alert('Please enter a lecture topic.'); return; }

  const btn  = document.getElementById('generate-btn');
  const txt  = document.getElementById('gen-btn-text');
  const spin = document.getElementById('gen-spinner');
  btn.disabled = true; txt.textContent = 'Generating…'; spin.classList.remove('hidden');

  try {
    const res = await fetch('/api/generate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
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
    btn.disabled = false; txt.textContent = '✨ Generate Lecture'; spin.classList.add('hidden');
  }
}

// ── Enter classroom ───────────────────────────────────────────────
function enterClassroom(data) {
  state.sessionId    = data.session_id;
  state.slides       = data.slides;
  state.currentSlide = 0;
  state.isPlaying    = state.isPaused = false;

  document.getElementById('lecture-title').textContent    = data.title;
  document.getElementById('slide-counter').textContent    = `Slide 0 / ${data.slide_count}`;
  const dl = document.getElementById('download-pptx');
  dl.href = data.pptx_url; dl.download = `${data.title}.pptx`;

  document.getElementById('screen-setup').classList.remove('active');
  const cls = document.getElementById('screen-classroom');
  cls.style.display = 'flex'; cls.classList.add('active');

  // Build slide viewport
  const vp = document.getElementById('slide-viewport');
  vp.innerHTML = `
    <div class="slide-scaler" id="slide-scaler">
      <div id="slide-render"></div>
    </div>
    <div class="slide-nav-overlay">
      <button class="nav-arrow" onclick="prevSlide()">‹</button>
      <button class="nav-arrow" onclick="nextSlide()">›</button>
    </div>`;

  renderSlide(state.slides[0], 0, state.slides.length);
  updateProgress(0, state.slides.length);
  setTimeout(scaleSlide, 50);
  connectWS(data.session_id);
  addChat('system', `📚 "${data.title}" — ${data.slide_count} slides ready. Press ▶ Start to begin.`);
}

// ── WebSocket ─────────────────────────────────────────────────────
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

// ── WS handler ────────────────────────────────────────────────────
function handleWS(msg) {
  switch (msg.type) {

    case 'slide_change':
      state.currentSlide = msg.slide_index;
      renderSlide(msg.slide, msg.slide_index, state.slides.length);
      updateProgress(msg.slide_index, state.slides.length);
      setStatus('playing');
      stopSpeaking();
      // Auto-show cached image
      { const key = `${state.sessionId}_${msg.slide_index}`;
        if (imgCache[key]) showSlideImage(imgCache[key]);
        else closeSlideImage(); }
      break;

    case 'narration':
      // Show subtitle if enabled
      showSubtitle(msg.text);
      if (msg.audio_b64) {
        playAudioB64(msg.audio_b64);
      } else if (msg.use_browser_tts && msg.text) {
        browserSpeak(msg.text);
      }
      break;

    case 'paused':
      state.isPaused = true; setStatus('paused'); stopSpeaking();
      document.getElementById('btn-pause').textContent = '▶ Resume';
      break;

    case 'resumed':
      state.isPaused = false; setStatus('playing');
      document.getElementById('btn-pause').textContent = '⏸ Pause';
      break;

    case 'quiz_loading':
      state.isPaused = true; setStatus('quiz');
      showQuizLoading();
      break;

    case 'quiz':
      showQuiz(msg);
      break;

    case 'quiz_result':
      handleQuizResult(msg);
      break;

    case 'question_received':
      addChat('user', '🎤 ' + msg.text);
      stopSpeaking();
      break;

    case 'answer':
      addChat('ai', msg.answer, '🤖 AI Lecturer');
      if (msg.audio_b64) playAudioB64(msg.audio_b64);
      else if (msg.use_browser_tts && msg.answer) {
        browserSpeak(msg.answer, () => { if (state.isPlaying) wsSend({ action: 'resume' }); });
      }
      break;

    case 'stt_error':
      addChat('system', '⚠️ Voice error: ' + msg.message);
      break;

    case 'lecture_complete':
      state.isPlaying = false; setStatus('done'); stopSpeaking();
      document.getElementById('btn-start').disabled   = false;
      document.getElementById('btn-pause').disabled   = true;
      document.getElementById('btn-start').textContent = '↩ Restart';
      showSubtitle('');
      addChat('system', '✅ Lecture complete! Keep asking questions.');
      break;

    case 'stopped':
      state.isPlaying = false; setStatus('idle'); stopSpeaking();
      document.getElementById('btn-start').disabled = false;
      document.getElementById('btn-pause').disabled = true;
      showSubtitle('');
      break;

    case 'error':
      addChat('system', '❌ ' + msg.message); break;
  }
}

// ── Controls ──────────────────────────────────────────────────────
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
  // Instant — no confirm, just stop
  stopSpeaking();
  wsSend({ action: 'stop' });
  state.isPlaying = false;
  hideQuiz();
  showSubtitle('');
}

function backToSetup() {
  stopSpeaking(); hideQuiz();
  state.ws?.close(); state.ws = null;
  document.getElementById('screen-classroom').classList.remove('active');
  document.getElementById('screen-classroom').style.display = 'none';
  document.getElementById('screen-setup').classList.add('active');
  document.getElementById('chat-messages').innerHTML =
    '<div class="chat-msg system">👋 Enter a topic to start a new lecture.</div>';
  document.getElementById('btn-start').textContent  = '▶ Start';
  document.getElementById('btn-start').disabled     = false;
  document.getElementById('btn-pause').disabled     = true;
}

// ── Subtitles ─────────────────────────────────────────────────────
function toggleSubtitles() {
  state.subtitlesOn = !state.subtitlesOn;
  const bar = document.getElementById('subtitle-bar');
  const btn = document.getElementById('btn-sub');
  bar.classList.toggle('active', state.subtitlesOn);
  btn.classList.toggle('on', state.subtitlesOn);
  if (!state.subtitlesOn) document.getElementById('subtitle-text').textContent = '';
}

function showSubtitle(text) {
  if (!state.subtitlesOn) return;
  document.getElementById('subtitle-text').textContent = text || '';
}

// ── Quiz ──────────────────────────────────────────────────────────
function showQuizLoading() {
  const modal = document.getElementById('quiz-modal');
  modal.classList.remove('hidden');
  document.getElementById('quiz-question').textContent = '';
  document.getElementById('quiz-options').innerHTML =
    '<p class="quiz-loading-txt">⏳ Generating quiz question…</p>';
  document.getElementById('quiz-result').classList.add('hidden');
  document.getElementById('quiz-explain').classList.add('hidden');
  document.getElementById('quiz-continue').classList.add('hidden');
}

function showQuiz(data) {
  state.quizPending = data;
  document.getElementById('quiz-question').textContent = data.question;
  const opts = document.getElementById('quiz-options');
  opts.innerHTML = '';
  (data.options || []).forEach(opt => {
    const btn = document.createElement('button');
    btn.className   = 'quiz-opt';
    btn.textContent = opt;
    btn.onclick     = () => answerQuiz(opt, data.answer, data.explanation, opts);
    opts.appendChild(btn);
  });
  setStatus('quiz');
}

function answerQuiz(chosen, correctAnswer, explanation, optsContainer) {
  const isRight = chosen.trim().toUpperCase().startsWith(correctAnswer.trim().toUpperCase());

  // Style the options
  Array.from(optsContainer.children).forEach(btn => {
    btn.classList.add('disabled');
    if (btn.textContent.trim().toUpperCase().startsWith(correctAnswer.trim().toUpperCase())) {
      btn.classList.add('correct');
    } else if (btn.textContent === chosen && !isRight) {
      btn.classList.add('wrong');
    }
  });

  // Show result
  const result = document.getElementById('quiz-result');
  result.classList.remove('hidden', 'correct-result', 'wrong-result');
  result.classList.add(isRight ? 'correct-result' : 'wrong-result');
  document.getElementById('quiz-result-icon').textContent = isRight ? '✅' : '❌';
  document.getElementById('quiz-result-text').textContent = isRight
    ? 'Correct! Well done.' : `Not quite. The answer is ${correctAnswer}.`;

  // Explanation
  if (explanation) {
    const ex = document.getElementById('quiz-explain');
    ex.textContent = explanation;
    ex.classList.remove('hidden');
  }

  document.getElementById('quiz-continue').classList.remove('hidden');

  // Tell server
  wsSend({ action: 'quiz_answer', correct_answer: correctAnswer, chosen, explanation });
}

function handleQuizResult(msg) {
  // Already handled client-side above
}

function continueAfterQuiz() {
  hideQuiz();
  state.quizPending = null;
  state.isPaused    = false;
  setStatus('playing');
  // resume_event already set server-side via quiz_answer action
}

function hideQuiz() {
  document.getElementById('quiz-modal').classList.add('hidden');
}

// ── Mic ───────────────────────────────────────────────────────────
async function toggleMic() {
  if (state.recording) { stopMic(); return; }

  if (window.SpeechRecognition || window.webkitSpeechRecognition) {
    const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
    const rec       = new SpeechRec();
    rec.continuous      = false;
    rec.interimResults  = false;
    rec.lang            = 'en-US';

    state.recording = true; state.browserSTT = rec;
    document.getElementById('mic-btn').classList.add('recording');
    document.getElementById('mic-icon').textContent   = '⏹';
    document.getElementById('mic-status').textContent = '● Listening…';
    document.getElementById('mic-status').className   = 'mic-status recording';

    rec.onresult = e => {
      const text = e.results[0][0].transcript.trim();
      if (text) {
        wsSend({ action: 'ask_question', text });
        if (state.isPlaying && !state.isPaused) { stopSpeaking(); wsSend({ action: 'pause' }); }
      }
    };
    rec.onerror = e => { addChat('system', '⚠️ Mic: ' + e.error); resetMicUI(); };
    rec.onend   = () => resetMicUI();
    rec.start();
  } else {
    addChat('system', '⚠️ Browser does not support voice. Please type your question.');
  }
}

function stopMic() {
  state.browserSTT?.stop(); state.browserSTT = null;
  resetMicUI();
}

function resetMicUI() {
  state.recording = false;
  document.getElementById('mic-btn').classList.remove('recording');
  document.getElementById('mic-icon').textContent   = '🎤';
  document.getElementById('mic-status').textContent = '';
  document.getElementById('mic-status').className   = 'mic-status';
}

function sendTextQuestion() {
  const inp  = document.getElementById('text-question');
  const text = inp.value.trim();
  if (!text) return;
  inp.value = '';
  wsSend({ action: 'ask_question', text });
  if (state.isPlaying && !state.isPaused) { stopSpeaking(); wsSend({ action: 'pause' }); }
}

// ── Browser TTS ───────────────────────────────────────────────────
function browserSpeak(text, onEnd) {
  if (!window.speechSynthesis) { onEnd?.(); return; }
  window.speechSynthesis.cancel();
  const utt = new SpeechSynthesisUtterance(text);
  utt.rate  = 0.93; utt.pitch = 1.0;
  const voices   = window.speechSynthesis.getVoices();
  const preferred = voices.find(v => v.name.includes('Google') || v.name.includes('Natural') || v.name.includes('Neural'))
                 || voices.find(v => v.lang.startsWith('en')) || voices[0];
  if (preferred) utt.voice = preferred;
  utt.onend = utt.onerror = () => onEnd?.();

  // Subtitle sync — chunk-by-chunk using sentence boundaries
  if (state.subtitlesOn) {
    const sentences = text.match(/[^.!?]+[.!?]+/g) || [text];
    let idx = 0;
    utt.onboundary = (e) => {
      if (e.name === 'sentence' && idx < sentences.length) {
        showSubtitle(sentences[idx++].trim());
      }
    };
  }
  window.speechSynthesis.speak(utt);
}

function stopSpeaking() {
  window.speechSynthesis?.cancel();
}

// ── Audio (Lemonade TTS) ──────────────────────────────────────────
function playAudioB64(b64) {
  const bin = atob(b64);
  const buf = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
  audioPlayer.src = URL.createObjectURL(new Blob([buf], { type: 'audio/mpeg' }));
  audioPlayer.play().catch(e => console.warn('audio:', e));
}

// ── Slide rendering ───────────────────────────────────────────────
function renderSlide(slide, idx, total) {
  const el = document.getElementById('slide-render');
  if (!el) return;
  const isFirst = idx === 0;
  const isLast  = idx === total - 1;
  const bullets = [...(slide.bullets || [])];
  while (bullets.length < 7) bullets.push('Further exploration of this topic is recommended.');

  if (isFirst) {
    el.innerHTML = `<div class="sl-title">
      <div class="t-bar"></div><div class="t-circ"></div><div class="t-circ2"></div>
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
    el.innerHTML = `<div class="sl-summary">
      <div class="s-topbar"></div><div class="s-deco"></div>
      <div class="s-tag">Key Takeaways</div>
      <h2>${esc(slide.title)}</h2>
      <div class="s-items">
        ${bullets.map(b=>`<div class="sl-sitem"><div class="s-dot"></div><div class="s-text">${esc(b)}</div></div>`).join('')}
      </div>
      <div class="s-cta">Questions? Press the mic or type below!</div>
    </div>`;
  } else {
    el.innerHTML = `<div class="sl-content">
      <div class="c-header">
        <div class="c-hbar"></div>
        <h2>${esc(slide.title)}</h2>
        <div class="c-badge">Slide ${idx} / ${total-2}</div>
      </div>
      <div class="c-body">
        ${bullets.map((b,i)=>`<div class="sl-bullet"><div class="sl-num">${i+1}</div><div class="sl-btext">${esc(b)}</div></div>`).join('')}
      </div>
    </div>`;
  }
  document.getElementById('slide-counter').textContent = `Slide ${idx+1} / ${total}`;
}

function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function updateProgress(idx, total) {
  const pct = total > 1 ? (idx / (total - 1)) * 100 : 0;
  document.getElementById('progress-bar').style.width = pct + '%';
}

function setStatus(s) {
  const el = document.getElementById('lecture-status');
  el.className = 'status-badge ' + s;
  el.textContent = { playing:'▶ Playing', paused:'⏸ Paused', quiz:'📝 Quiz', done:'✓ Done', idle:'Idle' }[s] || s;
}

// ── Chat ──────────────────────────────────────────────────────────
function addChat(type, text, label) {
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

// ── Image generation ──────────────────────────────────────────────
const imgCache = {};

async function generateSlideImage() {
  if (!state.sessionId) return;
  const btn      = document.getElementById('btn-img');
  const cacheKey = `${state.sessionId}_${state.currentSlide}`;
  if (imgCache[cacheKey]) { showSlideImage(imgCache[cacheKey]); return; }

  btn.disabled = true; btn.classList.add('loading'); btn.textContent = '⏳';
  addChat('system', `🖼 Generating illustration… (may take 30s–5min depending on GPU/CPU)`);
  try {
    const res = await fetch('/api/generate-image', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: state.sessionId, slide_index: state.currentSlide }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || 'Failed');
    const data = await res.json();
    imgCache[cacheKey] = data.url;
    showSlideImage(data.url);
    addChat('system', '✅ Image ready!');
  } catch (e) {
    addChat('system', '❌ Image error: ' + e.message);
  } finally {
    btn.disabled = false; btn.classList.remove('loading'); btn.textContent = '🖼';
  }
}

function showSlideImage(url) {
  const wrap = document.getElementById('slide-image-wrap');
  document.getElementById('slide-image').src = url + '?t=' + Date.now();
  wrap.classList.remove('hidden');
}
function closeSlideImage() {
  document.getElementById('slide-image-wrap').classList.add('hidden');
}
