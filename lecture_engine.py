"""
Lecture Engine — holds per-session lecture state.
"""

import asyncio


class LectureEngine:
    def __init__(self, session_id: str, content: dict, pptx_path: str):
        self.session_id    = session_id
        self.content       = content          # {title, slides:[{title,bullets,speaker_note}]}
        self.pptx_path     = pptx_path
        self.current_slide = 0
        self.paused        = False
        self.stopped       = False
        self.skip_slide    = False
        self.go_prev       = False
        self.resume_event  = asyncio.Event()
        self.ws            = None             # WebSocket, set at connection time
        self.qa_history: list[dict] = []

    def to_dict(self) -> dict:
        return {
            "session_id":    self.session_id,
            "title":         self.content.get("title", ""),
            "current_slide": self.current_slide,
            "slide_count":   len(self.content.get("slides", [])),
            "paused":        self.paused,
            "stopped":       self.stopped,
            "pptx_path":     self.pptx_path,
            "qa_history":    self.qa_history,
        }
