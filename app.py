"""
EduAI Classroom — Main FastAPI Application
Integrates with Lemonade Server for local AI models.
"""

import asyncio
import base64
import json
import os
import re
import tempfile
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from slide_generator import generate_presentation
from lecture_engine import LectureEngine

# ─── Config ─────────────────────────────────────────────────────────────────
LEMONADE_BASE = os.getenv("LEMONADE_BASE", "http://localhost:13305/v1")
LLM_MODEL     = os.getenv("LLM_MODEL",    "Qwen3.5-4B-GGUF")
TTS_MODEL     = os.getenv("TTS_MODEL",    "kokoro-v1")
TTS_VOICE     = os.getenv("TTS_VOICE",    "af_heart")
STT_MODEL     = os.getenv("STT_MODEL",    "Whisper-Large-v3-Turbo")
IMG_MODEL     = os.getenv("IMG_MODEL",    "SDXL-Turbo")
SLIDES_DIR    = Path("slides")
SLIDES_DIR.mkdir(exist_ok=True)

app = FastAPI(title="EduAI Classroom")
app.mount("/static",  StaticFiles(directory="static"),  name="static")
app.mount("/slides",  StaticFiles(directory="slides"),  name="slides")

# Active lecture engines  {session_id: LectureEngine}
sessions: dict[str, LectureEngine] = {}


# ─── Models ─────────────────────────────────────────────────────────────────
class GenerateRequest(BaseModel):
    prompt: str
    slides: int = 8

class TTSRequest(BaseModel):
    text: str
    voice: str = TTS_VOICE


# ─── Helpers ────────────────────────────────────────────────────────────────
async def lemonade_chat(messages: list[dict], stream: bool = False) -> str:
    """Call Lemonade /v1/chat/completions (OpenAI-compatible)."""
    payload = {
        "model":       LLM_MODEL,
        "messages":    messages,
        "stream":      stream,
        "temperature": 0.7,
        "max_tokens":  1024,
    }
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{LEMONADE_BASE}/chat/completions",
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


async def lemonade_tts(text: str, voice: str = TTS_VOICE) -> bytes:
    """Call Lemonade /v1/audio/speech (Kokoro TTS)."""
    payload = {
        "model":           TTS_MODEL,
        "input":           text,
        "voice":           voice,
        "response_format": "mp3",
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{LEMONADE_BASE}/audio/speech",
            json=payload,
        )
        resp.raise_for_status()
        return resp.content


async def lemonade_stt(audio_bytes: bytes) -> str:
    """Call Lemonade /v1/audio/transcriptions (Whisper STT)."""
    files = {"file": ("audio.webm", audio_bytes, "audio/webm")}
    data  = {"model": STT_MODEL}
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{LEMONADE_BASE}/audio/transcriptions",
            files=files,
            data=data,
        )
        resp.raise_for_status()
        return resp.json().get("text", "")


async def check_lemonade() -> dict:
    """Check if Lemonade server is alive and list loaded models."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{LEMONADE_BASE}/models")
            if resp.status_code == 200:
                models = [m["id"] for m in resp.json().get("data", [])]
                return {"online": True, "models": models}
    except Exception:
        pass
    return {"online": False, "models": []}


# ─── REST Endpoints ──────────────────────────────────────────────────────────
@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.get("/api/status")
async def status():
    result = await check_lemonade()
    return JSONResponse({
        "lemonade": result,
        "config": {
            "llm":   LLM_MODEL,
            "tts":   TTS_MODEL,
            "stt":   STT_MODEL,
            "image": IMG_MODEL,
        }
    })


@app.post("/api/generate")
async def generate_lecture(req: GenerateRequest):
    """Generate slides + lecture script from a topic prompt."""
    session_id = str(uuid.uuid4())

    # 1. Ask LLM for structured slide content
    system = (
        "You are an expert educator. When given a topic, respond ONLY with valid JSON "
        "for a presentation. Format:\n"
        '{"title":"...", "slides":[{"title":"...", "bullets":["..."], '
        '"speaker_note":"..."}]}\n'
        "Each speaker_note should be 2-3 conversational sentences the lecturer will say. "
        "No markdown, no extra text — pure JSON only."
    )
    user_msg = (
        f"Create a {req.slides}-slide educational presentation about: {req.prompt}\n"
        "Include title slide, content slides, and a summary slide."
    )

    raw = await lemonade_chat([
        {"role": "system",  "content": system},
        {"role": "user",    "content": user_msg},
    ])

    # Parse JSON (strip markdown fences if model adds them)
    json_str = re.sub(r"```(?:json)?|```", "", raw).strip()
    try:
        content = json.loads(json_str)
    except json.JSONDecodeError:
        raise HTTPException(500, f"LLM returned invalid JSON: {raw[:300]}")

    # 2. Generate PPTX
    pptx_path = SLIDES_DIR / f"{session_id}.pptx"
    await asyncio.to_thread(generate_presentation, content, str(pptx_path))

    # 3. Store session
    engine = LectureEngine(session_id, content, str(pptx_path))
    sessions[session_id] = engine

    return JSONResponse({
        "session_id":  session_id,
        "title":       content.get("title", req.prompt),
        "slide_count": len(content.get("slides", [])),
        "pptx_url":    f"/slides/{session_id}.pptx",
        "slides":      content.get("slides", []),
    })


@app.post("/api/tts")
async def text_to_speech(req: TTSRequest):
    """Convert text to speech audio via Kokoro TTS."""
    audio = await lemonade_tts(req.text, req.voice)
    b64   = base64.b64encode(audio).decode()
    return JSONResponse({"audio_b64": b64, "format": "mp3"})


@app.post("/api/stt")
async def speech_to_text_endpoint(request: Request):
    """Transcribe audio bytes to text via Whisper STT."""
    body  = await request.body()
    text  = await lemonade_stt(body)
    return JSONResponse({"text": text})


@app.get("/api/session/{session_id}")
async def get_session(session_id: str):
    engine = sessions.get(session_id)
    if not engine:
        raise HTTPException(404, "Session not found")
    return JSONResponse(engine.to_dict())


# ─── WebSocket — real-time lecture control ───────────────────────────────────
@app.websocket("/ws/{session_id}")
async def websocket_lecture(ws: WebSocket, session_id: str):
    await ws.accept()
    engine = sessions.get(session_id)
    if not engine:
        await ws.send_json({"type": "error", "message": "Session not found"})
        await ws.close()
        return

    engine.ws = ws

    try:
        while True:
            msg = await ws.receive_json()
            action = msg.get("action")

            if action == "start_lecture":
                asyncio.create_task(run_lecture(engine, ws))

            elif action == "pause":
                engine.paused = True
                await ws.send_json({"type": "paused"})

            elif action == "resume":
                engine.paused = False
                engine.resume_event.set()
                await ws.send_json({"type": "resumed"})

            elif action == "next_slide":
                engine.skip_slide = True
                engine.resume_event.set()

            elif action == "prev_slide":
                engine.go_prev = True
                engine.resume_event.set()

            elif action == "ask_question":
                # Voice question — audio base64 or text
                if "audio_b64" in msg:
                    audio_bytes = base64.b64decode(msg["audio_b64"])
                    question    = await lemonade_stt(audio_bytes)
                else:
                    question = msg.get("text", "")

                if question.strip():
                    await ws.send_json({"type": "question_received", "text": question})
                    engine.paused = True
                    answer = await answer_question(engine, question)
                    audio  = await lemonade_tts(answer)
                    await ws.send_json({
                        "type":      "answer",
                        "question":  question,
                        "answer":    answer,
                        "audio_b64": base64.b64encode(audio).decode(),
                    })

            elif action == "stop":
                engine.stopped = True
                engine.resume_event.set()
                await ws.send_json({"type": "stopped"})
                break

    except WebSocketDisconnect:
        engine.stopped = True


async def run_lecture(engine: "LectureEngine", ws: WebSocket):
    """Drive the slide-by-slide lecture narration."""
    slides = engine.content.get("slides", [])

    while engine.current_slide < len(slides):
        if engine.stopped:
            break

        slide = slides[engine.current_slide]
        await ws.send_json({
            "type":        "slide_change",
            "slide_index": engine.current_slide,
            "slide":       slide,
        })

        # Generate audio for speaker note
        note = slide.get("speaker_note", " ".join(slide.get("bullets", [])))
        if note:
            try:
                audio = await lemonade_tts(note)
                await ws.send_json({
                    "type":        "narration",
                    "slide_index": engine.current_slide,
                    "text":        note,
                    "audio_b64":   base64.b64encode(audio).decode(),
                })
            except Exception as e:
                await ws.send_json({"type": "tts_error", "message": str(e)})

        # Wait between slides — check for pause/skip
        engine.skip_slide    = False
        engine.go_prev       = False
        engine.resume_event.clear()

        wait_seconds = 2  # Small gap before auto-advance
        await asyncio.sleep(wait_seconds)

        if engine.go_prev:
            engine.current_slide = max(0, engine.current_slide - 1)
            continue

        if engine.paused and not engine.skip_slide:
            await ws.send_json({"type": "waiting_for_resume"})
            await engine.resume_event.wait()

        if engine.go_prev:
            engine.current_slide = max(0, engine.current_slide - 1)
        else:
            engine.current_slide += 1

    if not engine.stopped:
        await ws.send_json({"type": "lecture_complete"})


async def answer_question(engine: "LectureEngine", question: str) -> str:
    """Generate a contextual answer using LLM."""
    slide = engine.content["slides"][engine.current_slide]
    ctx   = f"Slide {engine.current_slide + 1}: {slide['title']}\n" + \
             "\n".join(slide.get("bullets", []))

    messages = [
        {"role": "system",
         "content": (
             "You are a helpful teacher answering a student's question during a lecture. "
             "Be concise, clear, and encouraging. 2-4 sentences max."
         )},
        {"role": "user",
         "content": f"Context (current slide):\n{ctx}\n\nStudent question: {question}"},
    ]
    return await lemonade_chat(messages)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
