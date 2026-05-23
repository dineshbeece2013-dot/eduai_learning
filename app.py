"""
EduAI Classroom — FastAPI Application
Fixes: detailed speaker notes, instant stop, quiz generation.
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
TTS_BACKEND   = os.getenv("TTS_BACKEND",  "browser")
STT_BACKEND   = os.getenv("STT_BACKEND",  "browser")
IMG_MODEL     = os.getenv("IMG_MODEL",    "SD-Turbo")
IMG_BACKEND   = os.getenv("IMG_BACKEND",  "lemonade")
IMG_SIZE      = os.getenv("IMG_SIZE",     "512x512")
IMG_STEPS     = int(os.getenv("IMG_STEPS", "4"))
# Quiz every N content slides (0 = disabled)
QUIZ_EVERY    = int(os.getenv("QUIZ_EVERY", "3"))

SLIDES_DIR = Path("slides");  SLIDES_DIR.mkdir(exist_ok=True)
IMAGES_DIR = Path("static/images"); IMAGES_DIR.mkdir(exist_ok=True)

app = FastAPI(title="EduAI Classroom")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/slides",  StaticFiles(directory="slides"),  name="slides")

sessions: dict[str, LectureEngine] = {}

# ─── Pydantic ────────────────────────────────────────────────────────────────
class GenerateRequest(BaseModel):
    prompt: str
    slides: int = 8

class TTSRequest(BaseModel):
    text: str
    voice: str = TTS_VOICE

class ImgRequest(BaseModel):
    session_id: str
    slide_index: int

# ─── JSON extraction ─────────────────────────────────────────────────────────
def extract_json(raw: str) -> dict | None:
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try: return json.loads(m.group(1))
        except: pass
    text = re.sub(r"```(?:json)?|```", "", text).strip()
    try: return json.loads(text)
    except: pass
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
    sections = [
        ("Introduction",             ["Definition and scope", "Historical development", "Why it matters today", "Core principles overview", "Key terminology", "Real-world relevance", "What we will cover"]),
        ("Fundamental Concepts",     ["Building blocks", "Core theory", "Essential principles", "Mathematical foundations", "Classification", "Standard models", "Key assumptions"]),
        ("How It Works",             ["Step-by-step mechanism", "Input and output", "Internal components", "Data flow", "Feedback loops", "Error handling", "Optimisation"]),
        ("Types and Categories",     ["Major categories", "Sub-types", "Comparison", "Strengths", "Weaknesses", "When to use each", "Industry standards"]),
        ("Tools and Technologies",   ["Popular frameworks", "Open-source options", "Commercial tools", "Hardware needs", "Software ecosystem", "Integration", "Emerging tools"]),
        ("Real-World Applications",  ["Industry use cases", "Research uses", "Case studies", "Success stories", "Lessons learned", "Scalability", "ROI and impact"]),
        ("Challenges",               ["Common obstacles", "Technical limits", "Ethical concerns", "Resource constraints", "Scalability issues", "Regulatory challenges", "Open problems"]),
        ("Future Trends",            ["Emerging directions", "Next-gen approaches", "Predicted advances", "Industry roadmap", "Open research", "Career opportunities", "Staying updated"]),
    ]
    slides = []
    for i in range(num_slides):
        if i == 0:
            slides.append({
                "title": f"Introduction to {topic}",
                "bullets": [f"Definition and overview of {topic}", f"Historical background of {topic}", f"Why {topic} matters today", f"Core principles of {topic}", f"Key terminology in {topic}", f"Real-world applications of {topic}", f"What we will cover in this lecture"],
                "speaker_note": f"Welcome everyone. Today we explore {topic} comprehensively. We'll cover foundational concepts, real-world applications, and future trends. By the end you'll have a solid grounding you can build upon.",
            })
        elif i == num_slides - 1:
            slides.append({
                "title": "Summary and Key Takeaways",
                "bullets": [f"{topic} has wide-ranging applications across industries", "Core principles form the foundation for deeper study", "Multiple types and approaches suit different needs", "Real-world adoption continues to grow rapidly", "Technical and ethical challenges require careful attention", "Future trends point toward exciting new capabilities", "Continued learning and practice are essential for mastery"],
                "speaker_note": f"We have now covered all major aspects of {topic}. Review these seven takeaways regularly. Each point represents a door to further exploration. Keep asking questions and connecting ideas across domains.",
            })
        else:
            sec = sections[min(i, len(sections)-1)]
            slides.append({
                "title": f"{sec[0]}: {topic}",
                "bullets": sec[1],
                "speaker_note": f"Now let us examine {sec[0].lower()} as it relates to {topic}. {sec[1][0]} is our starting point. From there we build toward a complete picture of how these elements interact in practice.",
            })
    return {"title": topic, "slides": slides}

# ─── Lemonade helpers ─────────────────────────────────────────────────────────
async def lemonade_chat(messages: list[dict], max_tokens: int = 4096) -> str:
    payload = {"model": LLM_MODEL, "messages": messages, "stream": False, "temperature": 0.4, "max_tokens": max_tokens}
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
        r = await client.post(f"{LEMONADE_BASE}/audio/transcriptions", files=files, data={"model": STT_MODEL})
        r.raise_for_status()
        return r.json().get("text", "")

async def lemonade_image_gen(prompt: str, session_id: str, slide_idx: int) -> str | None:
    filename = f"{session_id}_slide{slide_idx}.png"
    out_path = IMAGES_DIR / filename
    payload = {"model": IMG_MODEL, "prompt": prompt, "n": 1, "size": IMG_SIZE,
               "steps": IMG_STEPS, "cfg_scale": 1.0, "response_format": "b64_json"}
    try:
        async with httpx.AsyncClient(timeout=600) as client:
            r = await client.post(f"{LEMONADE_BASE}/images/generations", json=payload)
            r.raise_for_status()
            b64 = r.json()["data"][0]["b64_json"]
            import base64 as _b64
            out_path.write_bytes(_b64.b64decode(b64))
            return f"/static/images/{filename}"
    except Exception as e:
        print(f"[IMG ERROR] {e}"); return None

async def check_lemonade() -> dict:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{LEMONADE_BASE}/models")
            if r.status_code == 200:
                models = [m["id"] for m in r.json().get("data", [])]
                return {"online": True, "models": models}
    except: pass
    return {"online": False, "models": []}

async def generate_quiz(engine: LectureEngine) -> dict | None:
    """Generate a 3-option multiple-choice quiz for the last N slides covered."""
    slides  = engine.content.get("slides", [])
    topic   = engine.content.get("title", "the topic")
    # Pick recent slides for context
    recent  = slides[max(0, engine.current_slide - QUIZ_EVERY): engine.current_slide + 1]
    context = "\n".join(f"- {s['title']}: {'; '.join(s.get('bullets', [])[:3])}" for s in recent)

    prompt = (
        f"Based on this lecture content about {topic}:\n{context}\n\n"
        "Create one multiple-choice quiz question. "
        "Return ONLY JSON: "
        '{"question":"...","options":["A) ...","B) ...","C) ..."],"answer":"A","explanation":"..."}'
        "\nThe answer field must be A, B, or C. No extra text."
    )
    try:
        raw  = await lemonade_chat([{"role": "user", "content": prompt}], max_tokens=400)
        data = extract_json(raw)
        if data and "question" in data and "options" in data and "answer" in data:
            return data
    except Exception as e:
        print(f"[QUIZ ERROR] {e}")
    return None

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
            "tts_backend": TTS_BACKEND,
            "stt_backend": STT_BACKEND,
            "img_backend": IMG_BACKEND,
        }
    })

@app.post("/api/generate")
async def generate_lecture(req: GenerateRequest):
    session_id = str(uuid.uuid4())

    # Detailed speaker note instructions — each bullet explained in full
    system = (
        "You are an expert professor creating a detailed lecture. "
        "Respond ONLY with valid JSON — no <think> tags, no markdown, no explanation.\n\n"
        "RULES:\n"
        "1. Every slide MUST have exactly 7 bullets — complete informative sentences of 12-20 words each\n"
        "2. speaker_note MUST be 5-7 sentences that EXPLAIN EACH BULLET IN DETAIL — not a summary\n"
        "3. speaker_note must read like a real professor speaking: explain WHY, give EXAMPLES, connect to real world\n"
        "4. speaker_note must NEVER start with 'This slide', 'In this slide', 'Today'\n"
        "5. Each content slide covers ONE distinct aspect with depth — not a shallow overview\n\n"
        "speaker_note example for a slide about Supervised Learning:\n"
        "\"Supervised learning works by training a model on labelled examples, much like a student learning from an answer key. "
        "For instance, to build an email spam classifier, we feed thousands of emails already marked as spam or not-spam. "
        "The model adjusts its internal weights every time it makes a wrong prediction. "
        "This process is called gradient descent, and it continues until the error rate is acceptably low. "
        "The key advantage is predictability — you always know what the model is trying to learn. "
        "However, collecting and labelling large datasets is expensive and time-consuming. "
        "Major applications include medical diagnosis, fraud detection, and image recognition.\"\n\n"
        "JSON format: {\"title\":\"...\",\"slides\":[{\"title\":\"...\",\"bullets\":[\"...\"],\"speaker_note\":\"...\"}]}"
    )

    user_msg = (
        f"Create a {req.slides}-slide educational lecture on: {req.prompt}\n\n"
        f"Slide 1 = engaging title/intro. Slides 2-{req.slides-1} = deep content (one distinct aspect each). "
        f"Slide {req.slides} = summary with 7 takeaways.\n"
        "Every speaker_note MUST explain all 7 bullets in detail with examples. Return JSON only."
    )

    print(f"[INFO] Generating: {req.prompt!r}")
    raw = ""
    try:
        raw = await lemonade_chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
            max_tokens=4096
        )
    except Exception as e:
        print(f"[LLM ERROR] {e}")

    content = extract_json(raw)

    if content is None:
        print("[WARN] Retry with minimal prompt…")
        try:
            raw2 = await lemonade_chat([{"role": "user", "content":
                f'Topic: "{req.prompt}". {req.slides} slides. Return ONLY JSON: '
                '{"title":"...","slides":[{"title":"...","bullets":["s1","s2","s3","s4","s5","s6","s7"],'
                '"speaker_note":"5-7 sentences explaining each bullet with examples."}]}'
            }], max_tokens=4096)
            content = extract_json(raw2)
        except: pass

    if content is None:
        content = build_fallback_content(req.prompt, req.slides)

    if not isinstance(content.get("slides"), list) or len(content["slides"]) == 0:
        content = build_fallback_content(req.prompt, req.slides)

    # Post-process
    topic      = content.get("title", req.prompt)
    slide_count = len(content["slides"])
    bad_starts  = ["this slide", "in this slide", "on this slide", "today we", "welcome"]

    for i, slide in enumerate(content["slides"]):
        slide.setdefault("title", f"Slide {i+1}")
        bullets = slide.get("bullets", [])
        while len(bullets) < 7:
            bullets.append(f"Key insight about {slide['title'].lower()} with practical implications")
        slide["bullets"] = bullets[:7]

        note = re.sub(r"<think>.*?</think>", "", slide.get("speaker_note", ""), flags=re.DOTALL).strip()
        if not note or any(note.lower().startswith(p) for p in bad_starts) or len(note.split()) < 30:
            # Generate a richer fallback note from bullets
            b = slide["bullets"]
            if i == 0:
                note = (f"Welcome to our lecture on {topic}. "
                        f"{b[0]}. {b[1]}. We will explore {b[2].lower()} in depth. "
                        f"By understanding {b[3].lower()}, you gain practical tools for real applications. "
                        f"{b[4]}. {b[5]}. {b[6]}.")
            elif i == slide_count - 1:
                note = (f"Let us consolidate everything we have covered about {topic}. "
                        f"{b[0]}. {b[1]}. {b[2]}. "
                        f"Remember that {b[3].lower()} will be most relevant in your practical work. "
                        f"{b[4]}. {b[5]}. "
                        f"Take these insights forward and keep building on each one.")
            else:
                note = (f"Let us now examine {slide['title']} in detail. "
                        f"{b[0]}. This matters because it forms the foundation for everything else. "
                        f"{b[1]}. In practice, you will encounter this frequently. "
                        f"{b[2]}. A good example is how industry leaders apply this daily. "
                        f"{b[3]}. {b[4]}. "
                        f"Finally, {b[5].lower()} and {b[6].lower()} complete the picture.")
        slide["speaker_note"] = note

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
        "quiz_every":  QUIZ_EVERY,
    })

@app.post("/api/tts")
async def tts_endpoint(req: TTSRequest):
    if TTS_BACKEND == "browser":
        return JSONResponse({"audio_b64": "", "use_browser": True})
    try:
        audio = await lemonade_tts(req.text, req.voice)
        return JSONResponse({"audio_b64": base64.b64encode(audio).decode(), "use_browser": False})
    except Exception as e:
        return JSONResponse({"audio_b64": "", "use_browser": True, "error": str(e)})

@app.post("/api/stt")
async def stt_endpoint(request: Request):
    if STT_BACKEND == "browser":
        return JSONResponse({"text": "", "use_browser": True})
    try:
        body = await request.body()
        text = await lemonade_stt(body)
        return JSONResponse({"text": text})
    except Exception as e:
        return JSONResponse({"text": "", "error": str(e)})

@app.get("/api/session/{session_id}")
async def get_session(session_id: str):
    engine = sessions.get(session_id)
    if not engine: raise HTTPException(404, "Session not found")
    return JSONResponse(engine.to_dict())

@app.post("/api/generate-image")
async def generate_slide_image(req: ImgRequest):
    if IMG_BACKEND == "disabled":
        raise HTTPException(503, "Image generation disabled")
    engine = sessions.get(req.session_id)
    if not engine: raise HTTPException(404, "Session not found")
    slides = engine.content.get("slides", [])
    if req.slide_index >= len(slides): raise HTTPException(400, "Invalid slide")
    slide  = slides[req.slide_index]
    topic  = engine.content.get("title", "education")
    title  = slide.get("title", "")
    bullets = slide.get("bullets", [])[:3]
    img_prompt = (
        f"Educational illustration about {title} in the context of {topic}. "
        f"Key concepts: {', '.join(bullets[:2])}. "
        "Clean infographic style, bright colors, white background, no text, high detail."
    )
    url = await lemonade_image_gen(img_prompt, req.session_id, req.slide_index)
    if url:
        engine.slide_images[req.slide_index] = url
        return JSONResponse({"url": url, "slide_index": req.slide_index})
    raise HTTPException(500, "Image generation failed")

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

            elif action == "quiz_answer":
                # User answered quiz — evaluate
                correct  = msg.get("correct_answer", "")
                chosen   = msg.get("chosen", "")
                explain  = msg.get("explanation", "")
                is_right = chosen.strip().upper().startswith(correct.strip().upper())
                await ws.send_json({
                    "type":        "quiz_result",
                    "correct":     is_right,
                    "chosen":      chosen,
                    "right_answer": correct,
                    "explanation": explain,
                })
                # Resume lecture after quiz
                engine.paused = False
                engine.resume_event.set()

            elif action == "ask_question":
                if "audio_b64" in msg:
                    try:
                        question = await lemonade_stt(base64.b64decode(msg["audio_b64"]))
                    except Exception as e:
                        await ws.send_json({"type": "stt_error", "message": str(e)}); continue
                else:
                    question = msg.get("text", "").strip()

                if not question:
                    await ws.send_json({"type": "stt_error", "message": "No transcription"}); continue

                engine.paused = True
                await ws.send_json({"type": "question_received", "text": question})
                answer    = await answer_question(engine, question)
                audio_b64 = ""
                if TTS_BACKEND == "lemonade":
                    try: audio_b64 = base64.b64encode(await lemonade_tts(answer)).decode()
                    except: pass
                engine.qa_history.append({"q": question, "a": answer})
                await ws.send_json({
                    "type": "answer", "question": question, "answer": answer,
                    "audio_b64": audio_b64,
                    "use_browser_tts": TTS_BACKEND == "browser" or audio_b64 == "",
                })

            elif action == "stop":
                # Instant stop — set flag and unblock event immediately
                engine.stopped = True
                engine.paused  = False
                engine.skip_slide = True
                engine.resume_event.set()
                await ws.send_json({"type": "stopped"}); break

    except WebSocketDisconnect:
        engine.stopped = True


async def run_lecture(engine: LectureEngine, ws: WebSocket):
    slides        = engine.content.get("slides", [])
    engine.current_slide = 0
    engine.stopped = engine.paused = False
    content_slide_count = 0   # counts only non-title, non-summary slides

    while engine.current_slide < len(slides):
        if engine.stopped: break

        slide      = slides[engine.current_slide]
        slide_idx  = engine.current_slide
        is_content = 0 < slide_idx < len(slides) - 1

        # ── Send slide change ────────────────────────────────────────────────
        await ws.send_json({
            "type":        "slide_change",
            "slide_index": slide_idx,
            "slide":       slide,
        })

        # ── Narration ────────────────────────────────────────────────────────
        note = slide.get("speaker_note", "").strip()
        if not note:
            note = f"Let us examine {slide.get('title', 'this topic')} now."

        audio_b64 = ""
        if TTS_BACKEND == "lemonade":
            try: audio_b64 = base64.b64encode(await lemonade_tts(note)).decode()
            except Exception as e: print(f"[TTS] {e}")

        await ws.send_json({
            "type":           "narration",
            "slide_index":    slide_idx,
            "text":           note,
            "audio_b64":      audio_b64,
            "use_browser_tts": TTS_BACKEND == "browser" or audio_b64 == "",
        })

        if is_content:
            content_slide_count += 1

        # ── Timed wait (0.25s ticks for instant stop response) ───────────────
        words   = len(note.split())
        wait    = max(4, (words / 130) * 60 + 2)
        elapsed = 0.0
        while elapsed < wait:
            if engine.stopped or engine.skip_slide or engine.go_prev or engine.paused:
                break
            await asyncio.sleep(0.25)
            elapsed += 0.25

        if engine.stopped: break

        # ── Quiz trigger (after every QUIZ_EVERY content slides, if not last) ─
        if (QUIZ_EVERY > 0 and is_content
                and content_slide_count % QUIZ_EVERY == 0
                and slide_idx < len(slides) - 1
                and not engine.stopped and not engine.skip_slide):

            engine.paused = True
            engine.resume_event.clear()
            await ws.send_json({"type": "quiz_loading"})

            quiz = await generate_quiz(engine)
            if quiz:
                await ws.send_json({"type": "quiz", **quiz})
                # Wait for user to answer (resume_event set by quiz_answer action)
                await engine.resume_event.wait()
            else:
                engine.paused = False

        # ── Pause gate ────────────────────────────────────────────────────────
        engine.skip_slide = False
        engine.go_prev    = False
        engine.resume_event.clear()

        if engine.paused and not engine.stopped:
            await ws.send_json({"type": "waiting_for_resume"})
            await engine.resume_event.wait()

        if engine.stopped: break

        if engine.go_prev:
            engine.current_slide = max(0, engine.current_slide - 1)
            content_slide_count  = max(0, content_slide_count - 1)
        else:
            engine.current_slide += 1

    if not engine.stopped:
        await ws.send_json({"type": "lecture_complete"})


async def answer_question(engine: LectureEngine, question: str) -> str:
    slides = engine.content.get("slides", [])
    topic  = engine.content.get("title", "the topic")
    idx    = engine.current_slide
    slide  = slides[idx] if idx < len(slides) else {}
    ctx    = (f"Lecture: {topic}\nCurrent slide: {slide.get('title','')}\n"
              f"Content: {'; '.join(slide.get('bullets',[]))}")
    system = (
        "You are a teacher answering a student's question mid-lecture. "
        "Give a clear, educational answer in 3-5 sentences with a real-world example. "
        "Do NOT start with 'This slide', 'Great question', or 'Certainly'. "
        "Do NOT use <think> tags. Speak naturally and directly."
    )
    try:
        raw    = await lemonade_chat(
            [{"role": "system", "content": system},
             {"role": "user",   "content": f"Context:\n{ctx}\n\nQuestion: {question}"}],
            max_tokens=512
        )
        answer = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        return answer or f"That is an important aspect of {topic}. Could you rephrase your question?"
    except Exception as e:
        return f"Based on our study of {topic}: {question.rstrip('?')} is a key concept worth exploring further."


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
