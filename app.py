"""
EduAI Classroom — Main FastAPI Application
Integrates with Lemonade Server for local AI models.
"""

import asyncio
import base64
import json
import os
import re
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
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/slides", StaticFiles(directory="slides"), name="slides")

# Active lecture engines  {session_id: LectureEngine}
sessions: dict[str, LectureEngine] = {}


# ─── Models ─────────────────────────────────────────────────────────────────
class GenerateRequest(BaseModel):
    prompt: str
    slides: int = 8

class TTSRequest(BaseModel):
    text: str
    voice: str = TTS_VOICE


# ─── JSON extraction (handles Qwen3 <think> blocks, markdown fences) ────────
def extract_json(raw: str) -> dict | None:
    """
    Robustly extract a JSON object from LLM output that may contain:
      - <think>...</think> reasoning blocks (Qwen3)
      - ```json ... ``` or ``` ... ``` markdown fences
      - Leading/trailing prose
    Returns parsed dict or None if extraction fails.
    """
    # 1. Strip <think>...</think> blocks (Qwen3 chain-of-thought)
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)

    # 2. Try to pull JSON from a ```json ... ``` fence
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        candidate = fence.group(1)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # 3. Strip any remaining ``` markers and try whole text
    text = re.sub(r"```(?:json)?|```", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 4. Find the first { ... } block in the text (greedy from first { to last })
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    # 5. Last resort: find innermost balanced braces
    start = text.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break

    return None


def build_fallback_content(prompt: str, num_slides: int) -> dict:
    """
    If the LLM cannot produce valid JSON even after retry,
    generate a minimal valid structure so the user still gets a lecture.
    """
    topic = prompt.strip().title()
    slides = [
        {
            "title": topic,
            "bullets": [f"An introduction to {topic}", "Key concepts and ideas"],
            "speaker_note": f"Welcome to today's lecture on {topic}. Let's explore the key ideas together.",
        }
    ]
    for i in range(1, num_slides - 1):
        slides.append({
            "title": f"Section {i}: Key Concept {i}",
            "bullets": [
                f"Important aspect {i}.1 of {topic}",
                f"Important aspect {i}.2 of {topic}",
                f"Important aspect {i}.3 of {topic}",
            ],
            "speaker_note": f"In this section we cover key concept number {i} related to {topic}.",
        })
    slides.append({
        "title": "Summary & Takeaways",
        "bullets": [
            f"{topic} is a broad and important subject",
            "We covered foundational concepts today",
            "Further study is recommended",
        ],
        "speaker_note": f"That concludes our lecture on {topic}. I hope you found it informative!",
    })
    return {"title": topic, "slides": slides}


# ─── Lemonade API Helpers ────────────────────────────────────────────────────
async def lemonade_chat(messages: list[dict], extra_params: dict | None = None) -> str:
    """Call Lemonade /v1/chat/completions (OpenAI-compatible)."""
    payload = {
        "model":       LLM_MODEL,
        "messages":    messages,
        "stream":      False,
        "temperature": 0.3,
        "max_tokens":  2048,
    }
    if extra_params:
        payload.update(extra_params)
    async with httpx.AsyncClient(timeout=180) as client:
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
    async with httpx.AsyncClient(timeout=120) as client:
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

    # ── Attempt 1: structured system + user prompt ──────────────────────────
    system = (
        "You are an expert educator. Respond ONLY with a single valid JSON object. "
        "Do NOT include any thinking, explanation, or markdown — pure JSON only.\n"
        "Schema:\n"
        '{"title":"string","slides":[{"title":"string","bullets":["string"],"speaker_note":"string"}]}\n'
        "Rules: speaker_note = 2-3 sentences. bullets = 3-5 items per slide."
    )
    user_msg = (
        f"Create a {req.slides}-slide educational presentation about: {req.prompt}\n"
        "First slide = title slide. Last slide = summary. Middle slides = content.\n"
        "Return JSON only."
    )

    print(f"[INFO] Generating lecture for: {req.prompt!r}")
    raw = await lemonade_chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user_msg}]
    )
    print(f"[DEBUG] Raw LLM response (first 400 chars): {raw[:400]!r}")

    content = extract_json(raw)

    # ── Attempt 2: no system prompt, just a tight user instruction ──────────
    if content is None:
        print("[WARN] Attempt 1 failed, trying simpler prompt…")
        simple = (
            f'Create a {req.slides}-slide presentation on "{req.prompt}". '
            "Reply with ONLY this JSON (no other text):\n"
            '{"title":"TOPIC","slides":[{"title":"SLIDE_TITLE","bullets":["POINT1","POINT2","POINT3"],"speaker_note":"NARRATION"}]}'
        )
        raw2 = await lemonade_chat([{"role": "user", "content": simple}])
        print(f"[DEBUG] Attempt 2 raw (first 400 chars): {raw2[:400]!r}")
        content = extract_json(raw2)

    # ── Attempt 3: ask the model to fix its own output ──────────────────────
    if content is None:
        print("[WARN] Attempt 2 failed, asking model to output just JSON…")
        fix_msg = (
            "The previous response was not valid JSON. "
            "Output ONLY the JSON object below, filling in real content. "
            "No thinking tags. No markdown. No explanation:\n"
            f'{{"title":"{req.prompt}","slides":[{{"title":"Introduction","bullets":["Point 1","Point 2","Point 3"],"speaker_note":"Welcome to this lecture."}}]}}'
        )
        raw3 = await lemonade_chat([{"role": "user", "content": fix_msg}])
        print(f"[DEBUG] Attempt 3 raw (first 400 chars): {raw3[:400]!r}")
        content = extract_json(raw3)

    # ── Fallback: generate minimal structure locally so the app never breaks ─
    if content is None:
        print("[WARN] All LLM attempts failed — using local fallback content")
        content = build_fallback_content(req.prompt, req.slides)

    # Validate structure
    if "slides" not in content or not isinstance(content.get("slides"), list):
        content = build_fallback_content(req.prompt, req.slides)

    # Ensure each slide has required keys
    for slide in content["slides"]:
        slide.setdefault("title", "Slide")
        slide.setdefault("bullets", ["Key point"])
        slide.setdefault("speaker_note", slide["title"])

    print(f"[INFO] Generated {len(content['slides'])} slides for: {content.get('title')}")

    # Generate PPTX
    pptx_path = SLIDES_DIR / f"{session_id}.pptx"
    await asyncio.to_thread(generate_presentation, content, str(pptx_path))

    # Store session
    engine = LectureEngine(session_id, content, str(pptx_path))
    sessions[session_id] = engine

    return JSONResponse({
        "session_id":  session_id,
        "title":       content.get("title", req.prompt),
        "slide_count": len(content["slides"]),
        "pptx_url":    f"/slides/{session_id}.pptx",
        "slides":      content["slides"],
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
    body = await request.body()
    text = await lemonade_stt(body)
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
                    try:
                        audio = await lemonade_tts(answer)
                        audio_b64 = base64.b64encode(audio).decode()
                    except Exception:
                        audio_b64 = ""
                    engine.qa_history.append({"q": question, "a": answer})
                    await ws.send_json({
                        "type":      "answer",
                        "question":  question,
                        "answer":    answer,
                        "audio_b64": audio_b64,
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
    engine.current_slide = 0
    engine.stopped = False
    engine.paused  = False

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
        note = slide.get("speaker_note", "") or " ".join(slide.get("bullets", []))
        if note:
            try:
                audio = await lemonade_tts(note)
                await ws.send_json({
                    "type":        "narration",
                    "slide_index": engine.current_slide,
                    "text":        note,
                    "audio_b64":   base64.b64encode(audio).decode(),
                })
                # Wait for audio to roughly finish, then pause for reading
                # Estimate: ~120 words/min TTS → (words/2) seconds
                words = len(note.split())
                wait  = max(2, words / 2)
                await asyncio.sleep(wait)
            except Exception as e:
                await ws.send_json({"type": "tts_error", "message": str(e)})
                await asyncio.sleep(3)

        # Check pause before advancing
        engine.skip_slide = False
        engine.go_prev    = False
        engine.resume_event.clear()

        if engine.paused and not engine.skip_slide and not engine.stopped:
            await ws.send_json({"type": "waiting_for_resume"})
            await engine.resume_event.wait()

        if engine.stopped:
            break

        if engine.go_prev:
            engine.current_slide = max(0, engine.current_slide - 1)
        else:
            engine.current_slide += 1

    if not engine.stopped:
        await ws.send_json({"type": "lecture_complete"})


async def answer_question(engine: "LectureEngine", question: str) -> str:
    """Generate a contextual answer using LLM."""
    slides = engine.content.get("slides", [])
    if engine.current_slide < len(slides):
        slide = slides[engine.current_slide]
        ctx = f"Current slide: {slide['title']}\nPoints: " + "; ".join(slide.get("bullets", []))
    else:
        ctx = f"Topic: {engine.content.get('title', '')}"

    messages = [
        {"role": "system",
         "content": (
             "You are a helpful teacher answering a student question during a lecture. "
             "Be concise, clear, and friendly. 2-4 sentences only. "
             "Do not use <think> blocks. Just answer directly."
         )},
        {"role": "user",
         "content": f"Context:\n{ctx}\n\nStudent question: {question}"},
    ]
    answer = await lemonade_chat(messages)
    # Strip any stray <think> tags the model might still add
    answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL).strip()
    return answer or "That's a great question! Let me move to the next slide for more context."


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
