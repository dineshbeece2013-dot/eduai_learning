# 🎓 EduAI Classroom

An interactive AI-powered classroom web application that integrates with
**Lemonade Server** to run fully local AI models — no cloud API required.

---

## Features

| Feature | Model Used |
|---|---|
| Lecture script & slide generation | Qwen3-0.6B-GGUF (LLM) |
| Voice narration (lecturer speaks) | kokoro-v1 (Kokoro TTS) |
| Voice questions (student speaks) | Whisper-Large-v3-Turbo (STT) |
| Image generation (optional) | SDXL-Turbo (Stable Diffusion) |
| PPTX download | python-pptx |

---

## Architecture

```
Browser (HTML/JS)
      │  WebSocket + REST
      ▼
FastAPI (app.py)  ──── Lemonade Server (localhost:13305)
      │                     ├── Qwen3-0.6B-GGUF   (LLM)
      │                     ├── kokoro-v1          (TTS)
      │                     ├── Whisper-Large-v3-Turbo (STT)
      │                     └── SDXL-Turbo         (Image gen)
      │
      └── python-pptx → generates .pptx file
```

---

## Prerequisites

### 1. Install Lemonade Server
Download from https://lemonade-server.ai and install as a system service.

### 2. Pull required models
```bash
lemonade pull Qwen3-0.6B-GGUF
lemonade pull kokoro-v1
lemonade pull Whisper-Large-v3-Turbo
lemonade pull SDXL-Turbo        # optional, for image gen
```

### 3. Verify Lemonade is running
```bash
curl http://localhost:13305/v1/models
```

---

## Installation

```bash
cd eduai

# Create virtual environment
python -m venv .venv

# Activate
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

## Running

```bash
# Make sure Lemonade Server is already running!

python app.py
```

Then open **http://localhost:8000** in your browser.

---

## Configuration

Override default models via environment variables:

```bash
# Windows (PowerShell)
$env:LLM_MODEL = "Qwen3-1.7B-GGUF"
$env:TTS_MODEL = "kokoro-v1"
$env:STT_MODEL = "Whisper-Large-v3-Turbo"
$env:TTS_VOICE = "bm_george"
python app.py

# Linux/macOS
LLM_MODEL=Qwen3-1.7B-GGUF TTS_VOICE=bm_george python app.py
```

| Variable | Default | Description |
|---|---|---|
| `LEMONADE_BASE` | `http://localhost:13305/v1` | Lemonade API base URL |
| `LLM_MODEL` | `Qwen3-0.6B-GGUF` | LLM for generating slides + answers |
| `TTS_MODEL` | `kokoro-v1` | Text-to-speech model |
| `TTS_VOICE` | `af_heart` | TTS voice ID |
| `STT_MODEL` | `Whisper-Large-v3-Turbo` | Speech-to-text model |
| `IMG_MODEL` | `SDXL-Turbo` | Image generation model |

---

## How to Use

1. **Open** http://localhost:8000
2. **Check** the green Lemonade status dot — must be Online
3. **Enter** a lecture topic (e.g. "Introduction to Photosynthesis")
4. **Choose** number of slides and TTS voice
5. Click **Generate Lecture** — AI creates slides + narration in ~30 seconds
6. Click **▶ Start Lecture** — AI narrator speaks each slide automatically
7. **Interrupt anytime** — press 🎤 mic button and ask a question in voice
8. The AI pauses, transcribes your question, answers in text + voice, then resumes
9. Use **⏸ Pause / ▶ Resume / ⏮ / ⏭** for full playback control
10. Download the generated **.pptx** file from the top bar

---

## Project Structure

```
eduai/
├── app.py              ← FastAPI server + WebSocket lecture controller
├── lecture_engine.py   ← Per-session state management
├── slide_generator.py  ← python-pptx slide builder
├── requirements.txt
├── slides/             ← Generated .pptx files (auto-created)
└── static/
    ├── index.html      ← Single-page classroom UI
    ├── css/style.css   ← Full UI styling
    └── js/app.js       ← WebSocket client + mic/audio handling
```

---

## Lemonade API Endpoints Used

| Endpoint | Purpose |
|---|---|
| `GET  /v1/models` | Server health + loaded models |
| `POST /v1/chat/completions` | Generate slide content + Q&A answers |
| `POST /v1/audio/speech` | Kokoro TTS narration |
| `POST /v1/audio/transcriptions` | Whisper STT for voice questions |
| `POST /v1/images/generations` | SDXL image generation (optional) |

All endpoints are OpenAI-compatible — Lemonade serves them at `localhost:13305`.
