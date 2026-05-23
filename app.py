"""
EduAI Classroom — FastAPI Application
Optimised for 32GB RAM: only ONE heavy model loaded at a time via Lemonade.
- LLM  (Qwen3.5-4B-GGUF)      : slide generation + Q&A
- TTS  (kokoro-v1)             : narration  — loaded on demand, unloaded after
- STT  (Whisper-Large-v3-Turbo): transcription — loaded on demand, unloaded after
Browser Web Speech API used as zero-RAM fallback for STT + TTS.
"""

import asyncio, base64, json, os, re, uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from slide_generator import generate_presentation
from lecture_engine import LectureEngine

# ─── Config ──────────────────────────────────────────────────────────────────
LEMONADE_BASE = os.getenv("LEMONADE_BASE", "http://localhost:13305/v1")
LLM_MODEL     = os.getenv("LLM_MODEL",    "Qwen3.5-4B-GGUF")
TTS_MODEL     = os.getenv("TTS_MODEL",    "kokoro-v1")
TTS_VOICE     = os.getenv("TTS_VOICE",    "af_heart")
STT_MODEL     = os.getenv("STT_MODEL",    "Whisper-Large-v3-Turbo")

# Set to "browser" to skip Lemonade TTS/STT entirely (saves RAM)
TTS_BACKEND   = os.getenv("TTS_BACKEND",  "lemonade")   # lemonade | browser
STT_BACKEND   = os.getenv("STT_BACKEND",  "browser")    # lemonade | browser

SLIDES_DIR = Path("slides")
SLIDES_DIR.mkdir(exist_ok=True)
IMAGES_DIR = Path("static/images")
IMAGES_DIR.mkdir(exist_ok=True)

IMG_MODEL   = os.getenv("IMG_MODEL",    "SD-Turbo")   # SD-Turbo fits in RAM (~2GB)
IMG_BACKEND = os.getenv("IMG_BACKEND",  "lemonade")   # lemonade | disabled
IMG_SIZE    = os.getenv("IMG_SIZE",     "512x512")
IMG_STEPS   = int(os.getenv("IMG_STEPS", "4"))        # SD-Turbo works well at 4 steps

app = FastAPI(title="EduAI Classroom")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/slides", StaticFiles(directory="slides"), name="slides")

sessions: dict[str, LectureEngine] = {}

# ─── Pydantic models ─────────────────────────────────────────────────────────
class GenerateRequest(BaseModel):
    prompt: str
    slides: int = 8

class TTSRequest(BaseModel):
    text: str
    voice: str = TTS_VOICE

# ─── JSON extraction ─────────────────────────────────────────────────────────
def extract_json(raw: str) -> dict | None:
    # Strip Qwen3 <think> blocks
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    # Try fenced JSON
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try: return json.loads(m.group(1))
        except: pass
    # Try whole text
    text = re.sub(r"```(?:json)?|```", "", text).strip()
    try: return json.loads(text)
    except: pass
    # Find first balanced { }
    start = text.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{": depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try: return json.loads(text[start:i+1])
                    except: break
    return None

def build_fallback_content(prompt: str, num_slides: int) -> dict:
    topic = prompt.strip().title()
    slides = [{
        "title": f"Introduction to {topic}",
        "bullets": [
            f"Definition and overview of {topic}",
            f"Historical background and origin of {topic}",
            f"Why {topic} is important in today's world",
            f"Core principles that govern {topic}",
            f"Key terminology used in {topic}",
            f"Real-world applications of {topic}",
            f"Who benefits from understanding {topic}",
        ],
        "speaker_note": f"Welcome everyone. Today we will explore {topic} in depth, covering its fundamentals, applications, and significance.",
    }]
    sections = [
        ("Fundamental Concepts", ["Basic building blocks", "Core theory", "Essential principles", "Mathematical foundations", "Classification and types", "Standard models", "Key assumptions"]),
        ("How It Works", ["Step-by-step mechanism", "Input and output process", "Internal components", "Data flow and processing", "Feedback and control", "Error handling", "Optimisation strategies"]),
        ("Types and Categories", ["Major categories", "Sub-types and variants", "Comparison of approaches", "Strengths of each type", "Weaknesses and limitations", "When to use each type", "Industry standards"]),
        ("Tools and Technologies", ["Popular tools and frameworks", "Open-source options", "Commercial solutions", "Hardware requirements", "Software ecosystem", "Integration methods", "Future tools emerging"]),
        ("Real-World Applications", ["Industry use cases", "Research applications", "Case study examples", "Success stories", "Lessons learned", "Scalability in practice", "ROI and impact"]),
        ("Challenges and Limitations", ["Common obstacles", "Technical limitations", "Ethical concerns", "Resource constraints", "Scalability issues", "Regulatory challenges", "Unsolved problems"]),
        ("Future Trends", ["Emerging research directions", "Next-generation approaches", "Predicted advancements", "Industry roadmap", "Open research questions", "Career opportunities", "How to stay updated"]),
    ]
    for i in range(1, num_slides - 1):
        sec = sections[(i - 1) % len(sections)]
        slides.append({
            "title": f"{sec[0]}: {topic}",
            "bullets": sec[1],
            "speaker_note": f"In this section we examine {sec[0].lower()} related to {topic}. Each of these points plays an important role in building a comprehensive understanding.",
        })
    slides.append({
        "title": "Summary and Key Takeaways",
        "bullets": [
            f"{topic} is a foundational concept with wide applications",
            "We explored the core principles and how they work",
            "Multiple types and categories exist for different needs",
            "Real-world applications demonstrate practical value",
            "Challenges remain but solutions are emerging",
            "Future trends point toward exciting developments",
            "Continuous learning is key to staying current",
        ],
        "speaker_note": f"That concludes our lecture on {topic}. Remember these seven takeaways as a roadmap for your further study.",
    })
    return {"title": topic, "slides": slides[:num_slides]}

# ─── Lemonade helpers ────────────────────────────────────────────────────────
async def lemonade_chat(messages: list[dict], max_tokens: int = 4096) -> str:
    payload = {
        "model": LLM_MODEL, "messages": messages,
        "stream": False, "temperature": 0.4, "max_tokens": max_tokens,
    }
    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.post(f"{LEMONADE_BASE}/chat/completions", json=payload)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

async def lemonade_tts(text: str, voice: str = TTS_VOICE) -> bytes:
    payload = {"model": TTS_MODEL, "input": text, "voice": voice, "response_format": "mp3"}
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(f"{LEMONADE_BASE}/audio/speech", json=payload)
        r.raise_for_status()
        return r.content

async def lemonade_stt(audio_bytes: bytes) -> str:
    files = {"file": ("audio.wav", audio_bytes, "audio/wav")}
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{LEMONADE_BASE}/audio/transcriptions",
            files=files, data={"model": STT_MODEL}
        )
        r.raise_for_status()
        return r.json().get("text", "")

async def lemonade_image_gen(prompt: str, session_id: str, slide_idx: int) -> str | None:
    """
    Generate an image via Lemonade SD-Turbo.
    Returns relative URL path or None on failure.
    Lemonade auto-evicts the LLM when SD-Turbo loads (LRU), saving RAM.
    """
    filename = f"{session_id}_slide{slide_idx}.png"
    out_path = IMAGES_DIR / filename

    payload = {
        "model":   IMG_MODEL,
        "prompt":  prompt,
        "n":       1,
        "size":    IMG_SIZE,
        "steps":   IMG_STEPS,
        "cfg_scale": 1.0,           # SD-Turbo uses low CFG
        "response_format": "b64_json",
    }
    try:
        async with httpx.AsyncClient(timeout=600) as client:   # CPU can take 4-5 min
            r = await client.post(f"{LEMONADE_BASE}/images/generations", json=payload)
            r.raise_for_status()
            data = r.json()
            b64  = data["data"][0]["b64_json"]
            import base64 as _b64
            out_path.write_bytes(_b64.b64decode(b64))
            return f"/static/images/{filename}"
    except Exception as e:
        print(f"[IMG ERROR] {e}")
        return None


async def check_lemonade() -> dict:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{LEMONADE_BASE}/models")
            if r.status_code == 200:
                models = [m["id"] for m in r.json().get("data", [])]
                return {"online": True, "models": models}
    except: pass
    return {"online": False, "models": []}

# ─── REST endpoints ───────────────────────────────────────────────────────────
@app.get("/")
async def index():
    return FileResponse("static/index.html")

@app.get("/api/status")
async def status():
    result = await check_lemonade()
    return JSONResponse({
        "lemonade": result,
        "config": {
            "llm": LLM_MODEL, "tts": TTS_MODEL,
            "stt": STT_MODEL,
            "tts_backend": TTS_BACKEND,
            "stt_backend": STT_BACKEND,
            "img": IMG_MODEL,
            "img_backend": IMG_BACKEND,
        }
    })

@app.post("/api/generate")
async def generate_lecture(req: GenerateRequest):
    session_id = str(uuid.uuid4())

    # Build a detailed JSON example so the model understands the exact structure
    example_bullet = (
        '"Detailed explanation of point one with full context",'
        '"Second key concept with supporting evidence",'
        '"Third important fact including examples",'
        '"Fourth principle with practical implications",'
        '"Fifth topic area covering real-world usage",'
        '"Sixth consideration including challenges",'
        '"Seventh takeaway summarising this aspect"'
    )
    system = (
        "You are an expert educator creating a detailed lecture presentation. "
        "You MUST respond with ONLY a valid JSON object — no thinking tags, no markdown, no explanation.\n\n"
        "CRITICAL RULES:\n"
        "1. Every slide MUST have EXACTLY 7 bullets (never fewer)\n"
        "2. Each bullet MUST be a complete sentence of 10-20 words\n"
        "3. speaker_note MUST be 3-4 unique sentences specific to that slide's content\n"
        "4. speaker_note must NEVER start with 'This slide' or 'In this slide'\n"
        "5. Content must be educational, factual, and detailed\n\n"
        "JSON format:\n"
        '{"title":"Presentation Title","slides":['
        '{"title":"Slide Title","bullets":['
        + example_bullet +
        '],"speaker_note":"Begin with context. Expand on the main idea. Connect to real world. Conclude the point."}]}'
    )

    user_msg = (
        f"Create a {req.slides}-slide educational lecture on: {req.prompt}\n\n"
        f"Slide structure:\n"
        f"- Slide 1: Title/Introduction slide\n"
        f"- Slides 2 to {req.slides - 1}: Deep content slides, each covering a different aspect\n"
        f"- Slide {req.slides}: Summary with 7 key takeaways\n\n"
        f"IMPORTANT: Every slide must have exactly 7 detailed bullet points. "
        f"Speaker notes must be natural speech, never starting with 'This slide'."
    )

    print(f"[INFO] Generating lecture: {req.prompt!r}")
    try:
        raw = await lemonade_chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
            max_tokens=4096
        )
    except Exception as e:
        print(f"[ERROR] LLM call failed: {e}")
        raw = ""

    print(f"[DEBUG] Raw response[:500]: {raw[:500]!r}")
    content = extract_json(raw)

    # Retry with simpler prompt if needed
    if content is None:
        print("[WARN] Retrying with minimal prompt…")
        simple = (
            f'Topic: "{req.prompt}". Write {req.slides} slides. '
            'Return ONLY JSON: {"title":"...","slides":[{"title":"...","bullets":["sentence 1","sentence 2","sentence 3","sentence 4","sentence 5","sentence 6","sentence 7"],"speaker_note":"..."}]}'
        )
        try:
            raw2 = await lemonade_chat([{"role": "user", "content": simple}], max_tokens=4096)
            content = extract_json(raw2)
        except: pass

    if content is None:
        print("[WARN] Using local fallback content")
        content = build_fallback_content(req.prompt, req.slides)

    if not isinstance(content.get("slides"), list) or len(content["slides"]) == 0:
        content = build_fallback_content(req.prompt, req.slides)

    # ── Post-process: pad every slide to 7 bullets, fix speaker notes ─────────
    slide_count = len(content["slides"])
    topic = content.get("title", req.prompt)

    for i, slide in enumerate(content["slides"]):
        slide.setdefault("title", f"Slide {i+1}")
        bullets = slide.get("bullets", [])

        # Pad bullets to 7 if short
        while len(bullets) < 7:
            bullets.append(f"Additional key insight about {slide['title'].lower()} and its significance")
        slide["bullets"] = bullets[:7]  # cap at 7

        # Fix speaker note — never repeat "This slide"
        note = slide.get("speaker_note", "").strip()
        note = re.sub(r"<think>.*?</think>", "", note, flags=re.DOTALL).strip()
        bad_starts = ["this slide", "in this slide", "on this slide", "the slide"]
        if not note or any(note.lower().startswith(p) for p in bad_starts):
            if i == 0:
                note = (
                    f"Welcome to our lecture on {topic}. "
                    f"Today we will build a solid understanding of {slide['title']}. "
                    f"By the end, you will be equipped with practical knowledge you can apply."
                )
            elif i == slide_count - 1:
                note = (
                    f"We have now covered all the major aspects of {topic}. "
                    f"These seven takeaways represent the most important concepts from today. "
                    f"Review them regularly and explore each area in greater depth."
                )
            else:
                note = (
                    f"Let us now explore {slide['title']}. "
                    f"Understanding {bullets[0].lower()} is foundational to this topic. "
                    f"Pay close attention to how each point connects to real-world scenarios. "
                    f"These concepts are widely used across multiple domains."
                )
        slide["speaker_note"] = note

    print(f"[INFO] Final: {slide_count} slides, topic='{topic}'")

    pptx_path = SLIDES_DIR / f"{session_id}.pptx"
    await asyncio.to_thread(generate_presentation, content, str(pptx_path))

    engine = LectureEngine(session_id, content, str(pptx_path))
    sessions[session_id] = engine

    return JSONResponse({
        "session_id":  session_id,
        "title":       topic,
        "slide_count": slide_count,
        "pptx_url":    f"/slides/{session_id}.pptx",
        "slides":      content["slides"],
        "tts_backend": TTS_BACKEND,
        "stt_backend": STT_BACKEND,
    })

@app.post("/api/tts")
async def tts_endpoint(req: TTSRequest):
    """TTS via Lemonade (Kokoro). Returns base64 MP3."""
    if TTS_BACKEND == "browser":
        return JSONResponse({"audio_b64": "", "use_browser": True})
    try:
        audio = await lemonade_tts(req.text, req.voice)
        return JSONResponse({"audio_b64": base64.b64encode(audio).decode(), "use_browser": False})
    except Exception as e:
        print(f"[TTS ERROR] {e}")
        return JSONResponse({"audio_b64": "", "use_browser": True, "error": str(e)})

@app.post("/api/stt")
async def stt_endpoint(request: Request):
    """STT via Lemonade (Whisper). Accepts raw audio bytes."""
    if STT_BACKEND == "browser":
        return JSONResponse({"text": "", "use_browser": True})
    try:
        body = await request.body()
        text = await lemonade_stt(body)
        return JSONResponse({"text": text})
    except Exception as e:
        print(f"[STT ERROR] {e}")
        return JSONResponse({"text": "", "error": str(e)})

@app.get("/api/session/{session_id}")
async def get_session(session_id: str):
    engine = sessions.get(session_id)
    if not engine:
        raise HTTPException(404, "Session not found")
    return JSONResponse(engine.to_dict())

# ─── Image generation endpoint ───────────────────────────────────────────────
class ImgRequest(BaseModel):
    session_id: str
    slide_index: int

@app.post("/api/generate-image")
async def generate_slide_image(req: ImgRequest):
    """
    Generate an illustrative image for a specific slide.
    Runs SD-Turbo via Lemonade. LRU evicts the LLM from RAM automatically.
    After generation, next LLM call will reload Qwen (takes ~30s).
    """
    if IMG_BACKEND == "disabled":
        raise HTTPException(503, "Image generation is disabled")

    engine = sessions.get(req.session_id)
    if not engine:
        raise HTTPException(404, "Session not found")

    slides = engine.content.get("slides", [])
    if req.slide_index >= len(slides):
        raise HTTPException(400, "Invalid slide index")

    slide   = slides[req.slide_index]
    topic   = engine.content.get("title", "education")

    # Build a descriptive image prompt from slide content
    title   = slide.get("title", "")
    bullets = slide.get("bullets", [])[:3]
    # Educational illustration style prompt for SD-Turbo
    img_prompt = (
        f"Educational illustration about {title} in the context of {topic}. "
        f"Key concepts: {', '.join(bullets[:2])}. "
        "Clean infographic style, bright colors, white background, "
        "professional educational diagram, no text, high detail."
    )

    print(f"[IMG] Generating for slide {req.slide_index}: {title!r}")
    url = await lemonade_image_gen(img_prompt, req.session_id, req.slide_index)

    if url:
        # Store in session for retrieval
        engine.slide_images[req.slide_index] = url
        return JSONResponse({"url": url, "slide_index": req.slide_index})
    else:
        raise HTTPException(500, "Image generation failed — check Lemonade logs. CPU mode takes 4-5 minutes.")


# ─── WebSocket ────────────────────────────────────────────────────────────────
@app.websocket("/ws/{session_id}")
async def websocket_lecture(ws: WebSocket, session_id: str):
    await ws.accept()
    engine = sessions.get(session_id)
    if not engine:
        await ws.send_json({"type": "error", "message": "Session not found"})
        await ws.close(); return

    engine.ws = ws
    try:
        while True:
            msg    = await ws.receive_json()
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
                # Audio bytes (WAV from browser recording) or plain text
                if "audio_b64" in msg:
                    audio_bytes = base64.b64decode(msg["audio_b64"])
                    # Try Lemonade STT first, fallback already handled client-side
                    try:
                        question = await lemonade_stt(audio_bytes)
                    except Exception as e:
                        await ws.send_json({"type": "stt_error", "message": str(e)})
                        continue
                else:
                    question = msg.get("text", "").strip()

                if not question:
                    await ws.send_json({"type": "stt_error", "message": "Could not transcribe audio"})
                    continue

                was_playing = engine.paused == False and not engine.stopped
                engine.paused = True
                await ws.send_json({"type": "question_received", "text": question})

                answer = await answer_question(engine, question)

                # TTS for answer
                audio_b64 = ""
                if TTS_BACKEND == "lemonade":
                    try:
                        audio_b64 = base64.b64encode(await lemonade_tts(answer)).decode()
                    except Exception as e:
                        print(f"[TTS ERROR] {e}")

                engine.qa_history.append({"q": question, "a": answer})
                await ws.send_json({
                    "type": "answer", "question": question,
                    "answer": answer, "audio_b64": audio_b64,
                    "use_browser_tts": TTS_BACKEND == "browser" or audio_b64 == "",
                })

            elif action == "stop":
                engine.stopped = True
                engine.resume_event.set()
                await ws.send_json({"type": "stopped"}); break

    except WebSocketDisconnect:
        engine.stopped = True


async def run_lecture(engine: LectureEngine, ws: WebSocket):
    slides = engine.content.get("slides", [])
    engine.current_slide = 0
    engine.stopped = engine.paused = False

    while engine.current_slide < len(slides):
        if engine.stopped: break

        slide = slides[engine.current_slide]
        await ws.send_json({
            "type": "slide_change",
            "slide_index": engine.current_slide,
            "slide": slide,
        })

        note = slide.get("speaker_note", "").strip()
        if not note:
            note = f"Let us examine {slide.get('title', 'this topic')} in detail."

        # Send narration text immediately (browser TTS will use it)
        audio_b64 = ""
        if TTS_BACKEND == "lemonade":
            try:
                audio_b64 = base64.b64encode(await lemonade_tts(note)).decode()
            except Exception as e:
                print(f"[TTS ERROR] {e}")

        await ws.send_json({
            "type": "narration",
            "slide_index": engine.current_slide,
            "text": note,
            "audio_b64": audio_b64,
            "use_browser_tts": TTS_BACKEND == "browser" or audio_b64 == "",
        })

        # Wait proportional to text length
        words = len(note.split())
        # ~130 wpm speaking rate + 3s reading buffer
        wait = max(4, (words / 130) * 60 + 3)
        
        # Wait in small increments so skip/pause is responsive
        elapsed = 0
        while elapsed < wait:
            if engine.stopped or engine.skip_slide or engine.go_prev or engine.paused:
                break
            await asyncio.sleep(0.5)
            elapsed += 0.5

        engine.skip_slide = False
        engine.go_prev    = False
        engine.resume_event.clear()

        if engine.paused and not engine.stopped:
            await ws.send_json({"type": "waiting_for_resume"})
            await engine.resume_event.wait()

        if engine.stopped: break
        if engine.go_prev:
            engine.current_slide = max(0, engine.current_slide - 1)
        else:
            engine.current_slide += 1

    if not engine.stopped:
        await ws.send_json({"type": "lecture_complete"})


async def answer_question(engine: LectureEngine, question: str) -> str:
    slides  = engine.content.get("slides", [])
    topic   = engine.content.get("title", "the topic")
    idx     = engine.current_slide

    if idx < len(slides):
        slide = slides[idx]
        ctx = (
            f"Lecture topic: {topic}\n"
            f"Current slide: {slide['title']}\n"
            f"Slide content: {'; '.join(slide.get('bullets', []))}"
        )
    else:
        ctx = f"Lecture topic: {topic}"

    system = (
        "You are a friendly, knowledgeable teacher answering a student's question during a lecture. "
        "Rules: answer in 3-5 sentences, be specific and educational, relate to the slide context, "
        "do NOT start with 'This slide' or phrases like 'Great question!', "
        "do NOT use <think> tags, write naturally as if speaking."
    )
    try:
        raw = await lemonade_chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": f"Context:\n{ctx}\n\nStudent asked: {question}"}],
            max_tokens=512
        )
        answer = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        return answer if answer else f"That relates closely to {topic}. Could you rephrase the question?"
    except Exception as e:
        return f"I'm processing that question about {topic}. The key point is that {question.lower().rstrip('?')} is an important concept we should explore further."


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
