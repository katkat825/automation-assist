#!/usr/bin/env python3
"""
course_print_to_ppt.py — turn a course print (.docx) into a PowerPoint deck.

Standalone: one file, no other project code needed.

Requirements (one-time):
    pip install python-docx python-pptx

How to run (any of these):
    python course_print_to_ppt.py "HIPAA course print.docx"
    python course_print_to_ppt.py "HIPAA course print.docx" -o "HIPAA deck.pptx"
    python course_print_to_ppt.py            <- opens a file picker
    or drag a .docx onto this script in File Explorer

The deck is saved next to the .docx with the same name (.pptx) unless -o is given.

What you get (16:9, blue accent):
    * Title slide from the print's Title
    * A divider slide for each "Module N: ..." Heading 1
    * One slide per screenshot (each captured state / layer gets its own slide),
      screenshot on the left, transcript / feedback / answer choices in a panel
      on the right, with the correct answer ticked. Long text shrinks to fit;
      anything that still doesn't fit carries on to another slide with the
      same screenshot ("Part 2 of 2").
    * Nothing in the speaker notes.

The course print is expected to use Word's built-in styles:
    Title, Heading 1 (module), Heading 2 ("1 of 8 | Screen title"),
    Heading 3 (layer / state), screenshots as pictures, answer tables whose
    first column is "Correct" with an X on the right choice.
"""
from __future__ import annotations

import argparse
import io
import math
import os
import re
import sys
from dataclasses import dataclass, field, replace
from typing import Callable, Optional

try:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
    from pptx.util import Emu, Inches, Pt
except ImportError as _e:
    print(f"Missing library: {_e.name}\n"
          "Install the two requirements once, then run again:\n"
          "    pip install python-docx python-pptx")
    if len(sys.argv) < 2:
        input("\nPress Enter to close...")
    sys.exit(1)

DEFAULT_ACCENT = "1F3A5F"

# ---------------------------------------------------------------- model


@dataclass
class Block:
    kind: str                     # label | info | para | bullet | table
    text: str = ""
    rows: list = field(default_factory=list)   # table: list of row lists
    answer_key: bool = False      # table whose first column marks the correct choice


@dataclass
class Node:
    level: int                    # 1, 2, 3 (heading level)
    heading: str
    images: list = field(default_factory=list)  # (blob, content_type)
    blocks: list = field(default_factory=list)

    @property
    def has_content(self) -> bool:
        return bool(self.images or any(b.kind != "info" for b in self.blocks))


@dataclass
class CoursePrint:
    title: str
    subtitle: str
    nodes: list


# ---------------------------------------------------------------- parsing

_SCREEN_RE = re.compile(r"^\s*(?:screen\s+)?(\d+)\s*o(?:f)?\s*(\d+)\s*\|\s*(.*)$", re.I)
_LAYER_SUFFIX_RE = re.compile(r"\s*\((?:slide\s+)?layer\)\s*$", re.I)
_PLACEHOLDER_RE = re.compile(r"^\[\s*SCREENSHOT\b|^assets on this slide:", re.I)
_CHECK_MARKS = {"x", "✓", "✔", "√", "yes", "correct", "*"}


def _heading_level(p: Paragraph) -> int:
    name = (p.style.name if p.style is not None else "") or ""
    if name == "Title":
        return 0
    m = re.match(r"Heading\s+(\d)", name)
    if m:
        return int(m.group(1))
    # fall back to outline level set directly on the paragraph
    ol = p._p.find(qn("w:pPr") + "/" + qn("w:outlineLvl"))
    if ol is not None:
        try:
            return int(ol.get(qn("w:val"))) + 1
        except (TypeError, ValueError):
            pass
    return -1


def _images_in(el, part) -> list:
    out = []
    for blip in el.iter(qn("a:blip")):
        rid = blip.get(qn("r:embed"))
        if not rid or rid not in part.related_parts:
            continue
        ip = part.related_parts[rid]
        out.append((ip.blob, getattr(ip, "content_type", "")))
    return out


def _is_label(p: Paragraph, text: str) -> bool:
    if not text.endswith(":") or len(text) > 60:
        return False
    runs = [r for r in p.runs if r.text.strip()]
    return bool(runs) and all(r.bold for r in runs)


def _is_info(p: Paragraph, text: str) -> bool:
    if not (text.startswith("(") and text.endswith(")")):
        return False
    runs = [r for r in p.runs if r.text.strip()]
    return len(text) < 120 and (not runs or all(r.italic for r in runs) or "point" in text.lower())


def _table_block(t: Table) -> Optional[Block]:
    rows = []
    for r in t.rows:
        cells = []
        for c in r.cells:
            txt = " ".join(c.text.split())
            if not cells or cells[-1] is not c:   # merged cells repeat
                cells.append(txt)
        rows.append(cells)
    rows = [r for r in rows if any(r)]
    if not rows:
        return None
    head = [h.lower() for h in rows[0]]
    answer_key = len(head) >= 2 and head[0] in ("correct", "answer", "✓", "x")
    return Block("table", rows=rows, answer_key=answer_key)


def parse_course_print(path: str) -> CoursePrint:
    doc = Document(path)
    part = doc.part
    title, subtitle = "", ""
    nodes: list[Node] = []
    current: Optional[Node] = None
    preamble = Node(0, "")        # anything before the first heading

    for el in doc.element.body.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        target = current or preamble
        if tag == "tbl":
            tb = _table_block(Table(el, doc))
            if tb:
                target.blocks.append(tb)
            target.images.extend(_images_in(el, part))
            continue
        if tag != "p":
            continue
        p = Paragraph(el, doc)
        text = " ".join(p.text.split())
        level = _heading_level(p)
        style = (p.style.name if p.style is not None else "") or ""

        if level == 0:
            title = title or text
            continue
        if style == "Subtitle" and not subtitle:
            subtitle = text.strip("()")
            continue
        if level in (1, 2, 3) and text:
            current = Node(level, text)
            nodes.append(current)
            continue

        imgs = _images_in(el, part)
        target.images.extend(imgs)
        if not text or _PLACEHOLDER_RE.search(text):
            continue
        if _is_label(p, text):
            target.blocks.append(Block("label", text))
        elif _is_info(p, text):
            target.blocks.append(Block("info", text.strip("() ")))
        elif "List" in style or p._p.find(qn("w:pPr") + "/" + qn("w:numPr")) is not None:
            target.blocks.append(Block("bullet", text))
        else:
            target.blocks.append(Block("para", text))

    if not title:
        title = (doc.core_properties.title or "").strip() or \
            os.path.splitext(os.path.basename(path))[0]
    if preamble.has_content:
        nodes.insert(0, Node(1, "Introduction", preamble.images, preamble.blocks))
    return CoursePrint(title, subtitle, nodes)


# ---------------------------------------------------------------- slide plan


@dataclass
class SlideSpec:
    kind: str                     # title | section | screen
    eyebrow: str = ""
    title: str = ""
    pill: str = ""                # "3 of 8"
    tag: str = ""                 # "Layer", "Image 2 of 3"
    info: str = ""                # quiz type line
    image: Optional[tuple] = None
    blocks: list = field(default_factory=list)
    lines: Optional[list] = None  # panel text for this slide (set by _paginate)
    more: bool = False            # text carries on to the next slide


def _split_module(heading: str) -> tuple[str, str]:
    m = re.match(r"^((?:module|lesson|section|unit|part|chapter)\s+[\w.]+)\s*[:\-–—]\s*(.+)$",
                 heading, re.I)
    return (m.group(1), m.group(2)) if m else ("", heading)


def _block_lines(blocks: list) -> list[tuple[str, str]]:
    """Flatten blocks into (kind, text) lines for the on-slide text panel."""
    out = []
    for b in blocks:
        if b.kind == "table":
            rows = b.rows[1:] if len(b.rows) > 1 else b.rows
            if b.answer_key:
                out.append(("label", "Answer choices:"))
                for r in rows:
                    mark = (r[0] or "").strip().lower() in _CHECK_MARKS
                    choice = " ".join(c for c in r[1:] if c)
                    out.append(("correct" if mark else "bullet",
                                choice + ("  [Correct]" if mark else "")))
            else:
                hdr = b.rows[0] if len(b.rows) > 1 else []
                if hdr:
                    out.append(("label", " → ".join(h for h in hdr if h) + ":"))
                for r in rows:
                    out.append(("bullet", " → ".join(c for c in r if c)))
        else:
            out.append((b.kind, b.text))
    return out


def plan_slides(cp: CoursePrint) -> list[SlideSpec]:
    specs = [SlideSpec("title", title=cp.title, eyebrow="Course Print",
                       info=cp.subtitle)]
    module = ""
    screen_ctx = ""               # "Screen 4 of 8 · Who Must Comply?"
    screen_pill = ""

    for node in cp.nodes:
        info = " · ".join(b.text for b in node.blocks if b.kind == "info")
        body = [b for b in node.blocks if b.kind != "info"]

        if node.level == 1:
            module = node.heading
            screen_ctx, screen_pill = "", ""
            if not node.has_content:
                label, name = _split_module(node.heading)
                specs.append(SlideSpec("section", eyebrow=label, title=name))
                continue
            eyebrow, title, pill, tag = cp.title, node.heading, "", ""
        elif node.level == 2:
            m = _SCREEN_RE.match(node.heading)
            if m:
                screen_pill = f"{m.group(1)} of {m.group(2)}"
                title = m.group(3).strip() or node.heading
            else:
                screen_pill, title = "", node.heading
            screen_ctx = title
            eyebrow, pill, tag = module, screen_pill, ""
        else:  # level 3 — an interactive state / layer of the current screen
            title = _LAYER_SUFFIX_RE.sub("", node.heading).strip() or node.heading
            ctx = f"{module}  ·  {screen_ctx}" if screen_ctx else module
            eyebrow, pill, tag = ctx, screen_pill, "Layer"

        images = node.images or [None]
        for i, img in enumerate(images):
            t = tag
            if len(images) > 1:
                t = (f"{tag} · " if tag else "") + f"View {i + 1} of {len(images)}"
            specs.append(SlideSpec(
                "screen", eyebrow=eyebrow, title=title, pill=pill, tag=t, info=info,
                image=img, blocks=body))
    return specs


# ---------------------------------------------------------------- rendering

SLIDE_W, SLIDE_H = Inches(13.333), Inches(7.5)
MARGIN = Inches(0.6)
FONT = "Calibri"
INK = RGBColor(0x1F, 0x29, 0x37)
MUTED = RGBColor(0x6B, 0x72, 0x80)
RULE = RGBColor(0xD1, 0xD5, 0xDB)
PANEL = RGBColor(0xF3, 0xF4, 0xF6)
GOOD = RGBColor(0x15, 0x80, 0x3D)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)


def _rgb(hexstr: str) -> RGBColor:
    h = hexstr.strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        raise ValueError(f"accent must be a 6-digit hex colour, got {hexstr!r}")
    return RGBColor.from_string(h.upper())


def _tint(c: RGBColor, f: float) -> RGBColor:
    """Mix colour toward white by f (0..1)."""
    return RGBColor(*(int(v + (255 - v) * f) for v in (c[0], c[1], c[2])))


def _text(slide, x, y, w, h, text, size, color=INK, bold=False, align=PP_ALIGN.LEFT,
          anchor=MSO_ANCHOR.TOP, spacing=None):
    tb = slide.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    p = tf.paragraphs[0]
    p.alignment = align
    r = p.add_run()
    r.text = text
    f = r.font
    f.size, f.bold, f.name = Pt(size), bold, FONT
    f.color.rgb = color
    if spacing:
        rPr = r._r.get_or_add_rPr()
        rPr.set("spc", str(spacing))
    return tb


def _rect(slide, x, y, w, h, fill, shape=MSO_SHAPE.RECTANGLE, line=None):
    s = slide.shapes.add_shape(shape, x, y, w, h)
    s.fill.solid()
    s.fill.fore_color.rgb = fill
    if line is None:
        s.line.fill.background()
    else:
        s.line.color.rgb = line
        s.line.width = Pt(0.75)
    s.shadow.inherit = False
    return s


def _pill(slide, right_x, y, text, fill, color, outline=None, size=11):
    w = Inches(0.25 + 0.085 * len(text))
    s = _rect(slide, right_x - w, y, w, Inches(0.34), fill, MSO_SHAPE.ROUNDED_RECTANGLE, outline)
    s.adjustments[0] = 0.5
    tf = s.text_frame
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = text
    r.font.size, r.font.bold, r.font.name = Pt(size), True, FONT
    r.font.color.rgb = color
    return w


def _fit_lines(lines, width_in, height_in):
    """Pick the largest font size whose estimated wrapped height fits the box.
    Returns (size_pt, lines_that_fit, truncated)."""
    for size in (16, 14, 13, 12, 11, 10):
        cpl = max(10, int(width_in * 72 / (size * 0.47)))       # chars per line
        line_h = size * 1.2 / 72
        used, kept = 0.0, []
        ok = True
        for kind, text in lines:
            n = max(1, math.ceil(len(text) / cpl))
            need = n * line_h + (size * 0.5 / 72)                 # para spacing
            if kind == "label" and kept:
                need += size * 0.4 / 72
            if used + need > height_in:
                ok = False
                break
            used += need
            kept.append((kind, text))
        if ok:
            return size, kept, False
    return 10, kept, True


def _panel_text(slide, x, y, w, h, lines, accent, more=False):
    pad = Inches(0.2)
    _rect(slide, x, y, w, h, PANEL)
    inner_w, inner_h = (w - 2 * pad) / 914400, (h - 2 * pad) / 914400
    size, kept, _ = _fit_lines(lines, inner_w, inner_h)
    kept = lines                  # _paginate already split long text across slides
    tb = slide.shapes.add_textbox(x + pad, y + pad, w - 2 * pad, h - 2 * pad)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    first = True
    if more:
        kept = kept + [("more", "… continued on the next slide")]
    for kind, text in kept:
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        p.space_after = Pt(size * 0.5)
        if kind == "label" and not first:
            p.space_before = Pt(size * 0.4)
        first = False
        r = p.add_run()
        f = r.font
        f.name, f.size, f.color.rgb = FONT, Pt(size), INK
        if kind == "label":
            r.text = text.rstrip(":").upper()
            f.bold, f.size, f.color.rgb = True, Pt(max(9, size - 2)), accent
            r._r.get_or_add_rPr().set("spc", "60")
        elif kind in ("bullet", "correct"):
            r.text = ("✓  " if kind == "correct" else "•  ") + text.replace("  [Correct]", "")
            if kind == "correct":
                f.bold, f.color.rgb = True, GOOD
        elif kind == "more":
            r.text = text
            f.italic, f.color.rgb, f.size = True, MUTED, Pt(max(9, size - 1))
        else:
            r.text = text
    return tb


def _add_picture(slide, blob, x, y, max_w, max_h):
    pic = slide.shapes.add_picture(io.BytesIO(blob), x, y)
    iw, ih = pic.width, pic.height
    scale = min(max_w / iw, max_h / ih)
    pic.width, pic.height = int(iw * scale), int(ih * scale)
    pic.left = int(x + (max_w - pic.width) / 2)
    pic.top = int(y + (max_h - pic.height) / 2)
    pic.line.color.rgb = RULE
    pic.line.width = Pt(0.75)
    return pic


def _placeholder(slide, x, y, w, h, msg):
    s = _rect(slide, x, y, w, h, PANEL, line=RULE)
    tf = s.text_frame
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = msg
    r.font.size, r.font.italic, r.font.name = Pt(14), True, FONT
    r.font.color.rgb = MUTED


def _render_title(prs, spec, accent):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    _rect(s, 0, 0, SLIDE_W, SLIDE_H, accent)
    _rect(s, MARGIN, Inches(3.05), Inches(0.9), Inches(0.08), _tint(accent, 0.55))
    _text(s, MARGIN, Inches(2.45), Inches(11), Inches(0.45), spec.eyebrow.upper(), 14,
          _tint(accent, 0.65), bold=True, spacing=200)
    _text(s, MARGIN, Inches(3.3), Inches(12), Inches(2.2), spec.title, 44, WHITE, bold=True)
    if spec.info:
        _text(s, MARGIN, Inches(5.6), Inches(11), Inches(0.5), spec.info, 18, _tint(accent, 0.7))


def _render_section(prs, spec, accent, course_title):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    _rect(s, 0, 0, SLIDE_W, SLIDE_H, accent)
    _rect(s, 0, 0, Inches(0.35), SLIDE_H, _tint(accent, 0.35))
    if spec.eyebrow:
        _text(s, Inches(1.1), Inches(2.6), Inches(11), Inches(0.5), spec.eyebrow.upper(), 18,
              _tint(accent, 0.65), bold=True, spacing=300)
    _text(s, Inches(1.1), Inches(3.15), Inches(11.4), Inches(2.0), spec.title, 40, WHITE,
          bold=True)
    _text(s, Inches(1.1), Inches(6.7), Inches(11), Inches(0.35), course_title, 11,
          _tint(accent, 0.6))


def _render_screen(prs, spec, accent, course_title, idx, total):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    right = SLIDE_W - MARGIN
    # header
    _rect(s, 0, 0, SLIDE_W, Inches(0.09), accent)
    pill_w = _pill(s, right, Inches(0.42), spec.pill, accent, WHITE) if spec.pill else 0
    tag_w = 0
    if spec.tag:
        tag_w = _pill(s, right - pill_w - (Inches(0.12) if pill_w else 0), Inches(0.42),
                      spec.tag, _tint(accent, 0.88), accent, size=10)
    head_w = SLIDE_W - 2 * MARGIN - pill_w - tag_w - Inches(0.4)
    if spec.eyebrow:
        _text(s, MARGIN, Inches(0.36), head_w, Inches(0.3), spec.eyebrow.upper(), 11,
              accent, bold=True, spacing=100)
    tsize = 26 if len(spec.title) <= 55 else 22 if len(spec.title) <= 75 else 18
    _text(s, MARGIN, Inches(0.66), head_w, Inches(0.62), spec.title, tsize, INK, bold=True,
          anchor=MSO_ANCHOR.MIDDLE)
    if spec.info:
        _text(s, MARGIN, Inches(1.28), head_w, Inches(0.3), spec.info, 12, MUTED)
    # footer
    _rect(s, MARGIN, Inches(7.02), SLIDE_W - 2 * MARGIN, Emu(9525), RULE)
    _text(s, MARGIN, Inches(7.1), Inches(9), Inches(0.28), course_title, 10, MUTED)
    _text(s, right - Inches(2), Inches(7.1), Inches(2), Inches(0.28), f"{idx} / {total}", 10,
          MUTED, align=PP_ALIGN.RIGHT)

    lines = spec.lines if spec.lines is not None else _block_lines(spec.blocks)
    px, py, pw, ph = _panel_box(spec)

    if spec.image is None:
        if lines:
            _panel_text(s, px, py, pw, ph, lines, accent, spec.more)
        else:
            _placeholder(s, px, py, pw, ph,
                         "No screenshot or text for this screen in the course print")
    else:
        # always the screenshot on the left with the text panel beside it
        _panel_text(s, px, py, pw, ph,
                    lines or [("more", "No narration or on-screen text for this screen.")],
                    accent, spec.more)
        img_box = (MARGIN, py, IMG_W, ph)
        try:
            pic = _add_picture(s, spec.image[0], *img_box)
            pic.top = int(py)          # top-align the screenshot beside the panel
        except Exception as e:  # unsupported format (EMF, etc.)
            _placeholder(s, *img_box, f"Image could not be placed ({type(e).__name__})")


IMG_W, GAP = Inches(7.7), Inches(0.3)
PANEL_PAD = Inches(0.2)


def _panel_box(spec) -> tuple:
    """(x, y, w, h) of a screen slide's text panel."""
    top = Inches(1.68) if spec.info else Inches(1.42)
    h = Inches(6.88) - top
    if spec.image is None:
        return MARGIN, top, SLIDE_W - 2 * MARGIN, h
    x = MARGIN + IMG_W + GAP
    return x, top, SLIDE_W - MARGIN - x, h


def _paginate(specs: list) -> list:
    """Text that won't fit the panel even at the smallest font carries on to
    extra slides showing the same screenshot, tagged "Part n of N"."""
    out = []
    for spec in specs:
        lines = _block_lines(spec.blocks) if spec.kind == "screen" else []
        if not lines:
            out.append(spec)
            continue
        _, _, w, h = _panel_box(spec)
        inner_w, inner_h = (w - 2 * PANEL_PAD) / 914400, (h - 2 * PANEL_PAD) / 914400
        pages = []
        while lines:
            _, kept, truncated = _fit_lines(lines, inner_w, inner_h)
            if not truncated:
                pages.append(lines)
                break
            # leave room for the "continued" line; always move forward
            _, kept, _ = _fit_lines(lines, inner_w, inner_h - 0.35)
            kept = kept or lines[:1]
            pages.append(kept)
            lines = lines[len(kept):]
        for i, page in enumerate(pages):
            tag = spec.tag
            if len(pages) > 1:
                tag = (f"{tag} · " if tag else "") + f"Part {i + 1} of {len(pages)}"
            out.append(replace(spec, tag=tag, lines=page, more=i < len(pages) - 1))
    return out


def build_deck(cp: CoursePrint, out_path: str, accent: str = DEFAULT_ACCENT,
               progress: Optional[Callable[[int, int], None]] = None) -> dict:
    acc = _rgb(accent)
    specs = _paginate(plan_slides(cp))
    prs = Presentation()
    prs.slide_width, prs.slide_height = SLIDE_W, SLIDE_H
    prs.core_properties.title = cp.title
    total = len(specs)
    for i, spec in enumerate(specs, 1):
        if spec.kind == "title":
            _render_title(prs, spec, acc)
        elif spec.kind == "section":
            _render_section(prs, spec, acc, cp.title)
        else:
            _render_screen(prs, spec, acc, cp.title, i, total)
        if progress:
            progress(i, total)
    prs.save(out_path)
    screens = [s for s in specs if s.kind == "screen"]
    return {
        "out": out_path,
        "slides": total,
        "sections": sum(1 for s in specs if s.kind == "section"),
        "screen_slides": len(screens),
        "images": sum(1 for s in screens if s.image is not None),
        "text_only": sum(1 for s in screens if s.image is None),
    }


def convert(docx_path: str, out_path: Optional[str] = None, accent: str = DEFAULT_ACCENT,
            progress: Optional[Callable[[int, int], None]] = None) -> dict:
    """Convert a course print .docx to .pptx. Returns a summary dict."""
    out_path = out_path or os.path.splitext(docx_path)[0] + ".pptx"
    cp = parse_course_print(docx_path)
    summary = build_deck(cp, out_path, accent=accent, progress=progress)
    summary["title"] = cp.title
    return summary


def _pick_file() -> Optional[str]:
    """File picker for double-click use (no arguments given)."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        path = filedialog.askopenfilename(title="Select a course print",
                                          filetypes=[("Word document", "*.docx")])
        root.destroy()
        return path or None
    except Exception:
        return input("Path to the course print .docx: ").strip().strip('"') or None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Convert a course print .docx to PowerPoint.")
    ap.add_argument("docx", nargs="?", help="course print .docx (omit to pick a file)")
    ap.add_argument("-o", "--out", default=None,
                    help="output .pptx (default: same name, next to the .docx)")
    a = ap.parse_args(argv)
    interactive = a.docx is None
    src = a.docx or _pick_file()
    try:
        if not src:
            print("No file chosen.")
            return 1
        if not os.path.isfile(src) or not src.lower().endswith(".docx"):
            print(f"Not a .docx file: {src}")
            return 1
        out = a.out or os.path.splitext(src)[0] + ".pptx"
        print(f"Converting {os.path.basename(src)} ...")

        def prog(i, n):
            if i == n or i % 20 == 0:
                print(f"  slide {i} of {n}")

        try:
            s = convert(src, out, DEFAULT_ACCENT, progress=prog)
        except PermissionError:
            print(f"Couldn't write {out} - is it open in PowerPoint? Close it and try again.")
            return 1
        print(f"Course     : {s['title']}")
        print(f"Slides     : {s['slides']}  ({s['sections']} module dividers, "
              f"{s['screen_slides']} screen slides, {s['text_only']} without a screenshot)")
        print(f"Saved      : {s['out']}")
        return 0
    finally:
        if interactive or getattr(sys, "frozen", False):
            input("\nPress Enter to close...")


if __name__ == "__main__":
    sys.exit(main())
