"""
slide_generator.py — Generates PPTX presentations from structured JSON content.
Uses python-pptx with a professional dark academic theme.
"""

from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt
import copy


# ─── Theme Colors ────────────────────────────────────────────────────────────
C_BG_DARK   = RGBColor(0x0F, 0x17, 0x23)   # Dark navy
C_BG_LIGHT  = RGBColor(0xF4, 0xF7, 0xFB)   # Off-white
C_ACCENT    = RGBColor(0x3B, 0xC4, 0x7A)   # Emerald green
C_ACCENT2   = RGBColor(0x5B, 0xA4, 0xF5)   # Sky blue
C_WHITE     = RGBColor(0xFF, 0xFF, 0xFF)
C_DARK_TEXT = RGBColor(0x1A, 0x25, 0x35)
C_MUTED     = RGBColor(0x6A, 0x7C, 0x95)
C_BULLET_BG = RGBColor(0x1E, 0x2D, 0x42)


def _set_bg(slide, color: RGBColor):
    background = slide.background
    fill = background.fill
    fill.solid()
    fill.fore_color.rgb = color


def _add_text_box(slide, text: str, left, top, width, height,
                  font_size=18, bold=False, color=C_WHITE,
                  align=PP_ALIGN.LEFT, font_name="Calibri"):
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.size = Pt(font_size)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.name = font_name
    return txBox


def _add_rect(slide, left, top, width, height, color: RGBColor, radius=0):
    shape = slide.shapes.add_shape(
        1,  # MSO_SHAPE_TYPE.RECTANGLE
        left, top, width, height
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()  # no border
    return shape


# ─── Slide builders ──────────────────────────────────────────────────────────
def build_title_slide(prs: Presentation, slide_data: dict, topic_title: str):
    slide_layout = prs.slide_layouts[6]  # blank
    slide = prs.slides.add_slide(slide_layout)
    W, H = prs.slide_width, prs.slide_height

    _set_bg(slide, C_BG_DARK)

    # Accent bar left
    _add_rect(slide, 0, 0, Inches(0.22), H, C_ACCENT)

    # Decorative circle top-right
    circ = slide.shapes.add_shape(9, W - Inches(3.5), -Inches(1), Inches(4.5), Inches(4.5))
    circ.fill.solid()
    circ.fill.fore_color.rgb = RGBColor(0x1A, 0x35, 0x55)
    circ.line.fill.background()

    # Small label
    _add_text_box(slide, "AI CLASSROOM  ·  EDUAI",
                  Inches(0.5), Inches(1.0), Inches(8), Inches(0.5),
                  font_size=11, color=C_ACCENT, bold=True)

    # Main title
    title_text = slide_data.get("title", topic_title)
    _add_text_box(slide, title_text,
                  Inches(0.5), Inches(1.8), Inches(8.5), Inches(2.5),
                  font_size=48, bold=True, color=C_WHITE, font_name="Calibri")

    # Subtitle / bullets as tagline
    bullets = slide_data.get("bullets", [])
    if bullets:
        _add_text_box(slide, bullets[0],
                      Inches(0.5), Inches(4.0), Inches(7), Inches(0.8),
                      font_size=18, color=RGBColor(0xA0, 0xC8, 0xFF))

    # Bottom bar
    _add_rect(slide, 0, H - Inches(0.55), W, Inches(0.55), RGBColor(0x0A, 0x10, 0x1A))
    _add_text_box(slide, "Powered by Lemonade Server  ·  Local AI",
                  Inches(0.5), H - Inches(0.52), Inches(8), Inches(0.45),
                  font_size=10, color=C_MUTED)


def build_content_slide(prs: Presentation, slide_data: dict, slide_num: int, total: int):
    slide_layout = prs.slide_layouts[6]  # blank
    slide = prs.slides.add_slide(slide_layout)
    W, H = prs.slide_width, prs.slide_height

    _set_bg(slide, C_BG_LIGHT)

    # Top header bar
    _add_rect(slide, 0, 0, W, Inches(1.3), C_BG_DARK)

    # Accent strip
    _add_rect(slide, 0, 0, Inches(0.12), Inches(1.3), C_ACCENT)

    # Slide title in header
    title = slide_data.get("title", f"Slide {slide_num}")
    _add_text_box(slide, title,
                  Inches(0.3), Inches(0.18), W - Inches(2.5), Inches(0.9),
                  font_size=26, bold=True, color=C_WHITE, font_name="Calibri")

    # Slide counter badge
    counter_x = W - Inches(1.6)
    _add_rect(slide, counter_x, Inches(0.35), Inches(1.3), Inches(0.55),
              RGBColor(0x1E, 0x2D, 0x42))
    _add_text_box(slide, f"{slide_num} / {total}",
                  counter_x, Inches(0.33), Inches(1.3), Inches(0.6),
                  font_size=13, bold=True, color=C_ACCENT2, align=PP_ALIGN.CENTER)

    # Bullet points
    bullets = slide_data.get("bullets", [])
    y = Inches(1.55)
    row_h = Inches(0.72)

    for i, bullet in enumerate(bullets[:7]):  # max 7 bullets
        # Alternating card background
        card_color = RGBColor(0xFF, 0xFF, 0xFF) if i % 2 == 0 else RGBColor(0xF0, 0xF4, 0xFA)
        _add_rect(slide, Inches(0.35), y, W - Inches(0.7), row_h - Inches(0.06), card_color)

        # Bullet number circle
        num_box = slide.shapes.add_shape(9, Inches(0.45), y + Inches(0.1),
                                         Inches(0.45), Inches(0.45))
        num_box.fill.solid()
        num_box.fill.fore_color.rgb = C_ACCENT
        num_box.line.fill.background()

        _add_text_box(slide, str(i + 1),
                      Inches(0.45), y + Inches(0.08), Inches(0.45), Inches(0.45),
                      font_size=12, bold=True, color=C_WHITE, align=PP_ALIGN.CENTER)

        # Bullet text
        _add_text_box(slide, bullet,
                      Inches(1.05), y + Inches(0.06), W - Inches(1.5), row_h - Inches(0.15),
                      font_size=15, color=C_DARK_TEXT)
        y += row_h

    # Bottom bar
    _add_rect(slide, 0, H - Inches(0.4), W, Inches(0.4), C_BG_DARK)
    _add_text_box(slide, "EduAI Classroom",
                  Inches(0.3), H - Inches(0.38), Inches(4), Inches(0.35),
                  font_size=9, color=C_MUTED)


def build_summary_slide(prs: Presentation, slide_data: dict, topic_title: str):
    slide_layout = prs.slide_layouts[6]
    slide = prs.slides.add_slide(slide_layout)
    W, H = prs.slide_width, prs.slide_height

    _set_bg(slide, C_BG_DARK)

    # Big accent rectangle
    _add_rect(slide, 0, H - Inches(2.8), W, Inches(2.8), RGBColor(0x0A, 0x10, 0x1A))
    _add_rect(slide, 0, 0, W, Inches(0.15), C_ACCENT)

    # "Summary" label
    _add_text_box(slide, "SUMMARY",
                  Inches(0.5), Inches(0.4), Inches(4), Inches(0.6),
                  font_size=12, bold=True, color=C_ACCENT)

    # Title
    title = slide_data.get("title", "Summary")
    _add_text_box(slide, title,
                  Inches(0.5), Inches(1.0), W - Inches(1.0), Inches(1.4),
                  font_size=40, bold=True, color=C_WHITE, font_name="Calibri")

    # Bullets as key takeaways
    bullets = slide_data.get("bullets", [])
    y = Inches(2.6)
    for bullet in bullets[:5]:
        _add_rect(slide, Inches(0.5), y, Inches(0.05), Inches(0.35), C_ACCENT2)
        _add_text_box(slide, bullet,
                      Inches(0.75), y - Inches(0.05), W - Inches(1.5), Inches(0.5),
                      font_size=16, color=RGBColor(0xCC, 0xDD, 0xF5))
        y += Inches(0.5)

    # CTA
    _add_text_box(slide, "Questions? Ask the AI lecturer anytime!",
                  Inches(0.5), H - Inches(1.1), W - Inches(1.0), Inches(0.6),
                  font_size=14, color=C_ACCENT2, align=PP_ALIGN.CENTER)


# ─── Main entry ──────────────────────────────────────────────────────────────
def generate_presentation(content: dict, output_path: str):
    """
    content = {
        "title": "...",
        "slides": [
            {"title": "...", "bullets": ["..."], "speaker_note": "..."},
            ...
        ]
    }
    """
    prs = Presentation()
    prs.slide_width  = Inches(13.33)
    prs.slide_height = Inches(7.5)

    slides     = content.get("slides", [])
    topic_title = content.get("title", "Presentation")
    total       = len(slides)

    for idx, slide_data in enumerate(slides):
        if idx == 0:
            build_title_slide(prs, slide_data, topic_title)
        elif idx == total - 1:
            build_summary_slide(prs, slide_data, topic_title)
        else:
            build_content_slide(prs, slide_data, idx, total - 2)

    prs.save(output_path)
    return output_path


if __name__ == "__main__":
    # Quick test
    sample = {
        "title": "Introduction to Machine Learning",
        "slides": [
            {"title": "Introduction to Machine Learning",
             "bullets": ["What is ML?", "Why it matters"],
             "speaker_note": "Welcome everyone. Today we explore machine learning."},
            {"title": "What is Machine Learning?",
             "bullets": ["Learning from data", "Pattern recognition", "Automated decisions",
                         "Subset of AI", "Uses statistics"],
             "speaker_note": "Machine learning is a method of data analysis that automates model building."},
            {"title": "Key Summary",
             "bullets": ["ML learns from data", "Three main types", "Wide applications"],
             "speaker_note": "That concludes our introduction to machine learning."},
        ]
    }
    generate_presentation(sample, "/tmp/test_edu.pptx")
    print("Generated: /tmp/test_edu.pptx")
