"""
build_print.py — build a course print (.docx) from a SCORM package plus the
screenshots captured on the QA pass.

Inputs
    * the published Storyline SCORM package (.zip or an extracted folder):
      module headings, screen titles and order, narration, on-screen text,
      interactive layers and quiz answers all come from the package data
    * optionally, the ``course_print_shots`` folder (or its
      ``shots_manifest.json``) written by the QA Companion with
      "Capture screenshots (course print)" ticked

Output — the "normal" course print layout (modelled on the hand-made
Anti-Phishing Essentials print):

    Title  "<course name>"            Open Sans 30pt bold, centred
    Subtitle "(<course number>)"
    Heading 1  module ("Start", "Module 1: ...", "Conclusion")
    Heading 2  "Screen X of N | <slide title>"
               full-width (6.5") screenshot
               **Content:** narration, then on-screen text not already spoken
    Heading 3  interactive layers ("Example 1 of 6", "Learn More", ...)
               quiz feedback layers become a bold "Feedback:" line
    Heading 3  "Interactive states" — the extra states captured with the
               panel's Capture button, in capture order

Letter, 1" margins, Open Sans throughout, optional confidentiality footer
with page numbers and a faint grey diagonal CONFIDENTIAL watermark (the
same Word watermark the hand-made prints use). A screen with no captured screenshot gets a red
"[ SCREENSHOT ... ]" placeholder so it is easy to find and paste one in.

The result is a regular course print, so it also feeds straight into
docx_to_pptx.

Usage:
    python -m app.automation.course_print.build_print <package.zip>
           [--shots <course_print_shots dir>] [-o out.docx] [-n 11594]
           [--no-footer] [--no-watermark]
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import zipfile
from dataclasses import dataclass, field
from typing import Callable, Optional

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

FONT = "Open Sans"
IMAGE_WIDTH = Inches(6.5)          # full text width on Letter with 1" margins
FOOTER_TEXT = ("This document contains confidential and privileged material for "
               "the sole use of the intended recipient(s). Any review, use, "
               "distribution or disclosure by others is strictly prohibited.")
PLACEHOLDER_FMT = "[ SCREENSHOT — screen {n} — insert rendered capture ]"
MANIFEST_NAME = "shots_manifest.json"


# ====================================================================== package
class Package:
    """Reads files from a SCORM .zip or an already-extracted folder."""

    def __init__(self, path: str):
        self.path = path
        if os.path.isdir(path):
            self.zip = None
            self.names = set()
            for root, _, files in os.walk(path):
                for f in files:
                    rel = os.path.relpath(os.path.join(root, f), path)
                    self.names.add(rel.replace("\\", "/"))
        elif zipfile.is_zipfile(path):
            self.zip = zipfile.ZipFile(path)
            self.names = set(self.zip.namelist())
        else:
            raise ValueError(f"{path} is not a SCORM .zip or folder")

    def has(self, name: str) -> bool:
        return name in self.names

    def text(self, name: str) -> str:
        if self.zip is not None:
            raw = self.zip.read(name)
        else:
            with open(os.path.join(self.path, name), "rb") as fh:
                raw = fh.read()
        return raw.decode("utf-8-sig", errors="replace")

    def close(self):
        if self.zip is not None:
            self.zip.close()


def _js_unescape(s: str) -> str:
    out, k, n = [], 0, len(s)
    simple = {"'": "'", '"': '"', "\\": "\\", "n": "\n", "r": "\r",
              "t": "\t", "/": "/", "b": "\b", "f": "\f"}
    while k < n:
        c = s[k]
        if c == "\\" and k + 1 < n:
            nx = s[k + 1]
            if nx in simple:
                out.append(simple[nx]); k += 2; continue
            if nx == "u" and k + 6 <= n:
                out.append(chr(int(s[k + 2:k + 6], 16))); k += 6; continue
            out.append(nx); k += 2; continue
        out.append(c); k += 1
    return "".join(out)


def provide_data(raw: str):
    """Parse ``window.globalProvideData('kind', '<json>')`` into Python."""
    start = raw.index("', '") + 4
    end = raw.rindex("')")
    return json.loads(_js_unescape(raw[start:end]))


# ================================================================ text helpers
_VAR_RE = re.compile(r"%[_\w.]+%")
_ASSET_RE = re.compile(r"^[\w\-]+\.(jpe?g|png|gif|svg|mp3|mp4|json|woff2?|ttf|webm)$", re.I)
_EXAMPLE_RE = re.compile(r"^(example|case|scenario|step|tip)\s+(\d+)\s+of\s+(\d+)$", re.I)
_NOISE = {"continue", "start", "next", "next >", "back", "< back", "submit", "prev",
          "< prev", "previous", "menu", "resources", "replay", "close", "x",
          "return", "click here", "select", "done", "begin", "restart"}
_FEEDBACK = {"correct", "incorrect", "well done", "not quite", "try again", "lie",
             "that's right", "that’s right", "great job", "good job", "nice work",
             "nice job", "excellent", "sorry", "oops", "truth", "that's correct",
             "that’s correct", "that's incorrect", "that’s incorrect", "you got it",
             "not quite right", "almost", "right", "wrong", "feedback",
             "great choice", "good choice", "that's risky", "that’s risky", "risky",
             "bad choice", "not the best choice", "uh oh", "uh-oh"}


def _titlecase(t: str) -> str:
    """"THAT'S RISKY!" -> "That's Risky!" (str.title() gives "That'S")."""
    return re.sub(r"[A-Za-z]+(['’][A-Za-z]+)?",
                  lambda m: m.group(0)[0].upper() + m.group(0)[1:].lower(), t)


def clean_text(t) -> str:
    if not isinstance(t, str):
        return ""
    t = t.replace("^%^", "%").replace("^", "")
    t = html.unescape(t).replace(" ", " ")
    return re.sub(r"[ \t]+", " ", t).strip()


def html_to_paras(h: str) -> list:
    if not h:
        return []
    h = re.sub(r"</p\s*>", "\n", h, flags=re.I)
    h = re.sub(r"<br\s*/?>", "\n", h, flags=re.I)
    h = re.sub(r"<li[^>]*>", "\n", h, flags=re.I)
    h = re.sub(r"<[^>]+>", "", h)
    h = html.unescape(h).replace(" ", " ")
    return [re.sub(r"\s+", " ", ln).strip() for ln in h.split("\n") if ln.strip()]


def _norm(t: str) -> str:
    t = t.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", t).strip().lower()


def is_noise(t: str) -> bool:
    if not t:
        return True
    low = t.lower().strip()
    if low in _NOISE or _ASSET_RE.match(t) or _VAR_RE.search(t):
        return True
    if re.fullmatch(r"\d+[.)]?", t):                # lone list markers "1", "2."
        return True
    if re.fullmatch(r"of \d+", low):
        return True
    if re.fullmatch(r"v\d+[-._]\d+[\w.-]*", low):   # version stamps "v4-03"
        return True
    return False


def _block_lines(obj: dict) -> list:
    """One line per textLib block (= a paragraph in Storyline), spans joined."""
    tl = obj.get("textLib")
    if isinstance(tl, dict):
        tl = [tl]
    if not isinstance(tl, list):
        return []
    lines = []
    for td in tl:
        if not isinstance(td, dict):
            continue
        for block in (td.get("vartext") or {}).get("blocks", []) or []:
            t = clean_text("".join(sp.get("text", "") or "" for sp in block.get("spans", []) or []))
            if t:
                lines.append(t)
    return lines


def layer_lines(layer: dict) -> list:
    """On-screen text of a slide layer in authoring order, buttons left out."""
    out = []

    def walk(objs):
        objs = [o for o in objs or [] if isinstance(o, dict)]
        # Answer choices (radio/checkbox objects) are often stored bottom-up;
        # list them top-to-bottom as they appear on screen.
        radios = [o for o in objs if (o.get("accType") or "") in ("radio", "checkbox")]
        if len(radios) > 1:
            in_order = iter(sorted(radios, key=lambda o: (float(o.get("yPos") or 0),
                                                          float(o.get("xPos") or 0))))
            radio_ids = {id(o) for o in radios}
            objs = [next(in_order) if id(o) in radio_ids else o for o in objs]
        for o in objs:
            if (o.get("accType") or "") != "button":
                out.extend(t for t in _block_lines(o) if not is_noise(t))
            walk(o.get("objects"))

    walk(layer.get("objects"))
    dedup = []
    for t in out:                     # Storyline stores accessible duplicates
        if t not in dedup:
            dedup.append(t)
    return dedup


# ======================================================================= model
@dataclass
class Layer:
    heading: str
    lines: list
    feedback: bool = False
    order: Optional[int] = None       # "Example 3 of 6" -> 3, for sorting


@dataclass
class Screen:
    number: int
    title: str
    slide_id: str
    narration: list = field(default_factory=list)
    onscreen: list = field(default_factory=list)
    layers: list = field(default_factory=list)
    correct: set = field(default_factory=set)      # normalised correct-choice texts
    code: str = ""             # "<scene>.<slide in scene>", as in capture file names


@dataclass
class Module:
    heading: str
    screens: list = field(default_factory=list)


@dataclass
class CourseModel:
    title: str
    number: str
    total: int
    modules: list = field(default_factory=list)

    @property
    def screens(self):
        return [s for m in self.modules for s in m.screens]


def _layer_from_lines(lines: list, idx: int) -> Optional[Layer]:
    if not lines:
        return None
    order = None
    heading = None
    body = list(lines)
    for i, t in enumerate(body):        # "EXAMPLE 6 OF 6" wherever it sits
        m = _EXAMPLE_RE.match(t)
        if m:
            order = int(m.group(2))
            heading = f"{m.group(1).title()} {m.group(2)} of {m.group(3)}"
            body.pop(i)
            break
    first = body[0] if body else ""
    key = _norm(first).rstrip("!.: ")
    if key in _FEEDBACK:
        return Layer(heading=_titlecase(first) if first.isupper() else first,
                     lines=body[1:], feedback=True, order=order)
    if heading is None and first and len(first) <= 45 and (
            first.isupper() or first.endswith("!")
            or re.match(r"(?i)(learn more|statement|tip|did you know|example|case)\b", first)):
        heading = _titlecase(first) if first.isupper() else first
        body = body[1:]
    return Layer(heading=heading or f"Layer {idx}", lines=body, order=order)


def _scene_titles(outline: list) -> dict:
    """scene id -> menu heading, for menu groups (links that have children)."""
    titles = {}
    for link in outline or []:
        sid = (link.get("slideid") or "")
        if sid.startswith("_player."):
            sid = sid[len("_player."):]
        if link.get("links") and "." not in sid:
            titles[sid] = clean_text(link.get("displaytext", ""))
    return titles


def _correct_choices(slide_entry: dict) -> set:
    out = set()
    for ia in slide_entry.get("interactions") or []:
        if ia.get("issurvey"):
            continue
        choices = {c.get("id"): clean_text(c.get("lmstext", "")) for c in ia.get("choices", []) or []}
        for ans in ia.get("answers", []) or []:
            if ans.get("status") != "correct":
                continue
            for st in (ans.get("evaluate") or {}).get("statements", []) or []:
                cid = (st.get("choiceid") or "").replace("choices.", "")
                if choices.get(cid):
                    out.add(_norm(choices[cid]))
            break
    return out


def _identity(pkg: Package, source: str) -> tuple:
    """(course title, course number or None) from meta.xml / imsmanifest.xml."""
    title, number = "", None
    if pkg.has("meta.xml"):
        m = re.search(r'title="([^"]+)"', pkg.text("meta.xml"))
        if m:
            title = html.unescape(m.group(1)).strip()
            nm = re.search(r"\s(\d{4,6})$", title)
            if nm:
                number = nm.group(1)
                title = title[:nm.start()].strip()
    if not title and pkg.has("imsmanifest.xml"):
        m = re.search(r"<organization[^>]*>\s*<title>([^<]+)</title>", pkg.text("imsmanifest.xml"))
        if m:
            title = html.unescape(m.group(1)).strip()
    title = title or os.path.splitext(os.path.basename(source.rstrip("/\\")))[0]
    return title, number or guess_course_number(source)


def read_identity(source: str) -> tuple:
    """Cheap (title, number) lookup without parsing every slide."""
    pkg = Package(source)
    try:
        return _identity(pkg, source)
    finally:
        pkg.close()


def build_model(source: str, number: Optional[str] = None) -> CourseModel:
    pkg = Package(source)
    try:
        data = provide_data(pkg.text("html5/data/js/data.js"))
        frame = provide_data(pkg.text("html5/data/js/frame.js")) \
            if pkg.has("html5/data/js/frame.js") else {}

        title, meta_number = _identity(pkg, source)

        notes = {n.get("slideId"): n.get("content", "") for n in frame.get("notesData", []) or []}
        outline = ((frame.get("navData") or {}).get("outline") or {}).get("links", [])
        group_titles = _scene_titles(outline)

        scenes = [s for s in data.get("scenes", []) if not s.get("isMessageScene")]
        total = data.get("slideCount") or sum(len(s.get("slides", [])) for s in scenes)
        model = CourseModel(title=title,
                            number=number or meta_number or "",
                            total=total)
        n = 0
        for si, scene in enumerate(scenes):
            heading = group_titles.get(scene.get("id", ""))
            if not heading:
                if si == 0:
                    heading = "Introduction"
                elif si == len(scenes) - 1 and len(scenes) > 1:
                    heading = "Conclusion"
                else:
                    heading = clean_text(scene.get("title") or "") or f"Section {si + 1}"
            mod = Module(heading=heading)
            scene_no = scene.get("sceneNumber") or si + 1
            for k, entry in enumerate(scene.get("slides", []), start=1):
                n += 1
                sid = entry.get("id", "")
                sc = Screen(number=n, title=clean_text(entry.get("title", "")), slide_id=sid,
                            narration=html_to_paras(notes.get(f"{scene.get('id')}.{sid}", "")),
                            correct=_correct_choices(entry),
                            code=f"{scene_no}.{entry.get('slideNumberInScene') or k}")
                jf = entry.get("html5url") or f"html5/data/js/{sid}.js"
                if pkg.has(jf):
                    slide = provide_data(pkg.text(jf))
                    sc.title = sc.title or clean_text(slide.get("title", ""))
                    extra = 0
                    for li, ly in enumerate(slide.get("slideLayers", []) or []):
                        lines = layer_lines(ly)
                        if li == 0 or ly.get("isBaseLayer"):
                            sc.onscreen.extend(lines)
                        elif lines:
                            extra += 1
                            lay = _layer_from_lines(lines, extra)
                            if lay:
                                sc.layers.append(lay)
                    _sort_examples(sc.layers)
                    _mark_duplicate_feedback(sc.layers)
                mod.screens.append(sc)
            if mod.screens:
                model.modules.append(mod)
        return model
    finally:
        pkg.close()


def _sort_examples(layers: list):
    """Storyline stores "Example 6 of 6" ... "1 of 6" in reverse; put the
    numbered layers back in display order, leaving the rest where they are."""
    slots = [i for i, ly in enumerate(layers) if ly.order is not None]
    ordered = sorted((layers[i] for i in slots), key=lambda ly: ly.order)
    for i, ly in zip(slots, ordered):
        layers[i] = ly


def _mark_duplicate_feedback(layers: list):
    """Layers that say the same thing under different short banners
    ("Great Choice!" / "That's Risky!") are feedback for a choice."""
    seen: dict = {}
    for ly in layers:
        if ly.lines and len(ly.heading) <= 45 and not ly.heading.startswith("Layer "):
            seen.setdefault(_norm(" ".join(ly.lines)), []).append(ly)
    for group in seen.values():
        if len(group) > 1:
            for ly in group:
                ly.feedback = True


def guess_course_number(path: str) -> Optional[str]:
    m = re.search(r"(?:^|[_\-\s])(\d{4,6})(?=[_\-\s.A-Za-z]|$)", os.path.basename(path))
    return m.group(1) if m else None


# ======================================================================= shots
@dataclass
class Shot:
    path: str
    kind: str          # "base" | "manual" | "extra" (in the folder, not the manifest)
    slide_id: str
    seq: Optional[int] = None

    @property
    def sort_key(self):
        # capture order first; un-numbered extras after, by file name
        return (self.seq if self.seq is not None else 10 ** 9,
                _natural(os.path.basename(self.path)))


_IMAGE_EXT = (".png", ".jpg", ".jpeg")
_SEQ_RE = re.compile(r"^(\d+)")
_CODE_RE = re.compile(r"(?:^|[_\-\s])s(\d+)[-.](\d+)(?=[_\-\s.]|$)", re.I)
_SCREEN_RE = re.compile(r"(?:^|[_\-\s])(?:screen|scr)[\s_\-]*(\d+)", re.I)


def _natural(name: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def _manifest_path(shots: str) -> tuple:
    """(folder, manifest path or None) for a shots folder / manifest argument."""
    p = os.path.abspath(shots)
    if p.lower().endswith(".json"):
        return os.path.dirname(p), (p if os.path.isfile(p) else None)
    m = os.path.join(p, MANIFEST_NAME)
    return p, (m if os.path.isfile(m) else None)


def load_shots(shots: Optional[str]) -> tuple:
    """Read the capture manifest only. Returns ({slide_id: [Shot, ...]},
    manifest_path). Image paths resolve next to the manifest, so a moved or
    copied folder still works."""
    if not shots:
        return {}, None
    folder, manifest = _manifest_path(shots)
    if manifest is None:
        return {}, None
    with open(manifest, encoding="utf-8") as fh:
        rows = json.load(fh).get("shots", [])
    by_slide: dict = {}
    for row in sorted(rows, key=lambda r: r.get("seq", 0)):
        sid = row.get("slide_id")
        if not sid or not row.get("file"):
            continue
        by_slide.setdefault(sid, []).append(
            Shot(os.path.join(folder, row["file"]), row.get("kind", "base"), sid, row.get("seq")))
    return by_slide, manifest


def place_extra(name: str, model: CourseModel) -> Optional[str]:
    """Work out which screen an extra screenshot belongs to from its file
    name. Tried in order: the slide id ("..._6LYxbVMVRNn_..."), the capture
    screen code ("..._s2-1_..." = scene 2, slide 1), then "screen 12" /
    "Screen12" (the "Screen X of N" number). Returns a slide id or None."""
    stem = os.path.splitext(name)[0]
    screens = model.screens
    for sc in screens:
        if sc.slide_id and re.search(r"(?:^|[_\-\s])" + re.escape(sc.slide_id) + r"(?=[_\-\s]|$)", stem):
            return sc.slide_id
    m = _CODE_RE.search(stem)
    if m:
        code = f"{int(m.group(1))}.{int(m.group(2))}"
        for sc in screens:
            if sc.code == code:
                return sc.slide_id
    m = _SCREEN_RE.search(stem)
    if m:
        num = int(m.group(1))
        for sc in screens:
            if sc.number == num:
                return sc.slide_id
    return None


def collect_shots(shots: Optional[str], model: CourseModel) -> dict:
    """Everything in the screenshots folder, matched to screens:

    * manifest shots whose file still exists, in capture order
    * extra images dropped in the folder (not in the manifest), placed by
      the screen in their file name and ordered by the number they start
      with, among the captured ones
    * a deleted auto (base) shot is replaced at emit time by that screen's
      first remaining manual / extra shot

    Returns {"by_slide", "unplaced", "deleted", "extras", "unmatched", "manifest"}.
    """
    res = {"by_slide": {}, "unplaced": [], "deleted": 0, "extras": 0,
           "unmatched": 0, "manifest": None}
    if not shots:
        return res
    folder, _ = _manifest_path(shots)
    if not os.path.isdir(folder):
        raise FileNotFoundError(f"Screenshots folder not found: {folder}")
    listed, manifest = load_shots(shots)
    res["manifest"] = manifest
    known = {sc.slide_id for sc in model.screens}
    by_slide: dict = {}
    referenced = set()
    for sid, rows in listed.items():
        for sh in rows:
            referenced.add(os.path.normcase(os.path.abspath(sh.path)))
            if not os.path.isfile(sh.path):
                res["deleted"] += 1
                continue
            if sid not in known:
                res["unmatched"] += 1
                continue
            by_slide.setdefault(sid, []).append(sh)
    for name in sorted(os.listdir(folder), key=_natural):
        path = os.path.join(folder, name)
        if not name.lower().endswith(_IMAGE_EXT) or not os.path.isfile(path):
            continue
        if os.path.normcase(os.path.abspath(path)) in referenced:
            continue
        m = _SEQ_RE.match(name)
        sid = place_extra(name, model)
        sh = Shot(path, "extra", sid or "", int(m.group(1)) if m else None)
        if sid:
            res["extras"] += 1
            by_slide.setdefault(sid, []).append(sh)
        else:
            res["unplaced"].append(sh)
    for rows in by_slide.values():
        rows.sort(key=lambda sh: sh.sort_key)
    res["by_slide"] = by_slide
    return res


# ===================================================================== styling
def _style(doc, name, size=None, bold=None, color=None, before=None, after=None,
           align=None, italic=None):
    st = doc.styles[name]
    st.font.name = FONT
    rpr = st.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(qn(attr), FONT)
    for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        if rfonts.get(qn(attr)) is not None:
            del rfonts.attrib[qn(attr)]
    if size is not None:
        st.font.size = Pt(size)
    if bold is not None:
        st.font.bold = bold
    if italic is not None:
        st.font.italic = italic
    if color is not None:
        st.font.color.rgb = RGBColor.from_string(color)
    pf = st.paragraph_format
    if before is not None:
        pf.space_before = Pt(before)
    if after is not None:
        pf.space_after = Pt(after)
    if align is not None:
        pf.alignment = align
    return st


def _setup_document(footer: bool, watermark: bool = True):
    doc = Document()
    sec = doc.sections[0]
    sec.orientation = WD_ORIENT.PORTRAIT
    sec.page_width, sec.page_height = Inches(8.5), Inches(11)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(sec, side, Inches(1))

    _style(doc, "Normal", size=12, after=6)
    _style(doc, "Title", size=30, bold=True, color="000000", after=4,
           align=WD_ALIGN_PARAGRAPH.CENTER)
    _style(doc, "Subtitle", size=14, bold=True, italic=False, color="5A5A5A", after=18,
           align=WD_ALIGN_PARAGRAPH.CENTER)
    _style(doc, "Heading 1", size=18, bold=True, color="2F5496", before=18, after=6)
    _style(doc, "Heading 2", size=14, bold=True, color="2F5496", before=14, after=6)
    _style(doc, "Heading 3", size=13, bold=True, color="1F3763", before=10, after=4)
    _style(doc, "List Bullet", size=12, after=3)
    for h in ("Heading 1", "Heading 2", "Heading 3"):
        doc.styles[h].paragraph_format.keep_with_next = True
    # the default template draws a rule under the Title; the reference prints don't
    pbdr = doc.styles["Title"].element.find(qn("w:pPr") + "/" + qn("w:pBdr"))
    if pbdr is not None:
        pbdr.getparent().remove(pbdr)

    if footer:
        _style(doc, "Footer", size=8)
        p = sec.footer.paragraphs[0]
        p.text = ""
        p.add_run(FOOTER_TEXT + "\t")
        _page_field(p.add_run())
    if watermark:
        _add_watermark(sec.header.paragraphs[0])
    return doc


# Word's own "CONFIDENTIAL 1" watermark (Design > Watermark), as found in the
# Anti-Phishing Essentials print: a VML WordArt shape in the header, behind
# the text (negative z-index), silver at 50% opacity, rotated 315 degrees,
# centred on every page.
_WATERMARK_XML = (
    '<w:sdt xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office" '
    'xmlns:w10="urn:schemas-microsoft-com:office:word">'
    '<w:sdtPr><w:id w:val="-1761666155"/><w:docPartObj>'
    '<w:docPartGallery w:val="Watermarks"/><w:docPartUnique/></w:docPartObj></w:sdtPr>'
    '<w:sdtContent><w:r><w:rPr><w:noProof/></w:rPr><w:pict>'
    '<v:shapetype id="_x0000_t136" coordsize="21600,21600" o:spt="136" adj="10800" '
    'path="m@7,l@8,m@5,21600l@6,21600e"><v:formulas>'
    '<v:f eqn="sum #0 0 10800"/><v:f eqn="prod #0 2 1"/><v:f eqn="sum 21600 0 @1"/>'
    '<v:f eqn="sum 0 0 @2"/><v:f eqn="sum 21600 0 @3"/><v:f eqn="if @0 @3 0"/>'
    '<v:f eqn="if @0 21600 @1"/><v:f eqn="if @0 0 @2"/><v:f eqn="if @0 @4 21600"/>'
    '<v:f eqn="mid @5 @6"/><v:f eqn="mid @8 @5"/><v:f eqn="mid @7 @8"/>'
    '<v:f eqn="mid @6 @7"/><v:f eqn="sum @6 0 @5"/></v:formulas>'
    '<v:path textpathok="t" o:connecttype="custom" '
    'o:connectlocs="@9,0;@10,10800;@11,21600;@12,10800" o:connectangles="270,180,90,0"/>'
    '<v:textpath on="t" fitshape="t"/><v:handles><v:h position="#0,bottomRight" '
    'xrange="6629,14971"/></v:handles><o:lock v:ext="edit" text="t" shapetype="t"/>'
    '</v:shapetype>'
    '<v:shape id="PowerPlusWaterMarkObject1" o:spid="_x0000_s1025" type="#_x0000_t136" '
    'style="position:absolute;margin-left:0;margin-top:0;width:527.85pt;height:131.95pt;'
    'rotation:315;z-index:-251658752;mso-position-horizontal:center;'
    'mso-position-horizontal-relative:margin;mso-position-vertical:center;'
    'mso-position-vertical-relative:margin" o:allowincell="f" fillcolor="silver" stroked="f">'
    '<v:fill opacity=".5"/>'
    '<v:textpath style="font-family:&quot;Calibri&quot;;font-size:1pt" string="{text}"/>'
    '<w10:wrap anchorx="margin" anchory="margin"/></v:shape>'
    '</w:pict></w:r></w:sdtContent></w:sdt>'
)
WATERMARK_TEXT = "CONFIDENTIAL"


def _add_watermark(header_para, text: str = WATERMARK_TEXT):
    from docx.oxml import parse_xml
    header_para._p.append(parse_xml(_WATERMARK_XML.replace("{text}", html.escape(text))))


def _page_field(run):
    for kind, text in (("begin", None), (None, " PAGE "), ("separate", None),
                       (None, "1"), ("end", None)):
        if kind:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), kind)
        else:
            el = OxmlElement("w:instrText" if text.strip() == "PAGE" else "w:t")
            el.set(qn("xml:space"), "preserve")
            el.text = text
        run._r.append(el)


# ====================================================================== output
def _label(doc, text):
    p = doc.add_paragraph()
    p.add_run(text).bold = True
    p.paragraph_format.keep_with_next = True     # never strand "Content:" at a page foot


def _labelled(doc, label, text):
    p = doc.add_paragraph()
    p.add_run(label + " ").bold = True
    p.add_run(text)


def _picture(doc, path) -> bool:
    try:
        doc.add_picture(path, width=IMAGE_WIDTH)
        return True
    except Exception as e:  # unreadable / missing image: note it, keep going
        p = doc.add_paragraph()
        r = p.add_run(f"[ could not embed {os.path.basename(path)}: {e} ]")
        r.italic = True
        r.font.color.rgb = RGBColor(0xB0, 0x00, 0x00)
        return False


def _placeholder(doc, n):
    r = doc.add_paragraph().add_run(PLACEHOLDER_FMT.format(n=n))
    r.italic = True
    r.font.color.rgb = RGBColor(0xB0, 0x00, 0x00)


def _is_section_label(t: str) -> bool:
    return len(t) <= 40 and t.isupper() and any(c.isalpha() for c in t)


def _mark(t: str, correct: set) -> str:
    return f"{t} [Correct]" if correct and _norm(t) in correct else t


def emit_docx(model: CourseModel, out_path: str, shots: Optional[dict] = None,
              footer: bool = True, unplaced: Optional[list] = None,
              watermark: bool = True,
              progress: Optional[Callable[[int, int], None]] = None) -> dict:
    shots = shots or {}
    doc = _setup_document(footer, watermark)
    doc.add_paragraph(model.title, style="Title")
    if model.number:
        doc.add_paragraph(f"({model.number})", style="Subtitle")

    stats = {"images": 0, "screens_with_shots": 0, "placeholders": 0}
    all_screens = model.screens
    total = len(all_screens)
    done = 0
    for mod in model.modules:
        doc.add_paragraph(mod.heading, style="Heading 1")
        for sc in mod.screens:
            done += 1
            if progress:
                progress(done, total)
            doc.add_paragraph(f"Screen {sc.number} of {model.total} | {sc.title}",
                              style="Heading 2")

            mine = sorted((s for s in shots.get(sc.slide_id, []) if os.path.isfile(s.path)),
                          key=lambda s: s.sort_key)
            base = [s for s in mine if s.kind == "base"]
            states = [s for s in mine if s.kind != "base"]
            if not base and states:            # auto shot missing/deleted: first manual one leads
                base, states = states[:1], states[1:]
            if base:
                stats["screens_with_shots"] += 1
                for s in base:
                    stats["images"] += _picture(doc, s.path)
            else:
                stats["placeholders"] += 1
                _placeholder(doc, sc.number)

            _label(doc, "Content:")
            for t in sc.narration:
                doc.add_paragraph(t)

            spoken = _norm(" ".join(sc.narration))
            title_n = _norm(sc.title)
            extra = [t for t in sc.onscreen
                     if len(t) > 2 and _norm(t) != title_n and _norm(t) not in spoken]
            if extra:
                if sc.narration:
                    doc.add_paragraph()
                for t in extra:
                    if _is_section_label(t):
                        _label(doc, t)
                    else:
                        doc.add_paragraph(_mark(t, sc.correct), style="List Bullet")

            seen_feedback = set()
            fb_layers = [ly for ly in sc.layers if ly.feedback and ly.lines]
            distinct_fb = {_norm(" ".join(ly.lines)) for ly in fb_layers}
            for ly in sc.layers:
                if ly.feedback:
                    if not ly.lines:
                        continue
                    key = _norm(" ".join(ly.lines))
                    if key in seen_feedback:
                        continue
                    seen_feedback.add(key)
                    label = "Feedback:" if len(distinct_fb) <= 1 else f"Feedback ({ly.heading}):"
                    _labelled(doc, label, ly.lines[0])
                    for t in ly.lines[1:]:
                        doc.add_paragraph(t)
                    continue
                doc.add_paragraph(ly.heading, style="Heading 3")
                for t in ly.lines:
                    doc.add_paragraph(t)

            if states:
                doc.add_paragraph("Interactive states", style="Heading 3")
                for s in states:
                    stats["images"] += _picture(doc, s.path)

    if unplaced:
        # extras whose name doesn't say which screen they're from: keep them
        # (nothing silently dropped) so they can be dragged into place
        doc.add_paragraph("Unplaced screenshots", style="Heading 1")
        doc.add_paragraph("These images were in the screenshots folder but their file names "
                          "don't name a screen. Move each one to its screen, or rename the file "
                          "(e.g. 012_s3-2_extra.png or Screen 12.png) and build again.")
        for s in sorted(unplaced, key=lambda s: s.sort_key):
            _label(doc, os.path.basename(s.path))
            stats["images"] += _picture(doc, s.path)

    doc.save(out_path)
    stats.update(out=out_path, title=model.title, number=model.number,
                 modules=len(model.modules), screens=total, total=model.total)
    return stats


# ====================================================================== facade
def build(source: str, out_path: Optional[str] = None, shots: Optional[str] = None,
          number: Optional[str] = None, footer: bool = True, watermark: bool = True,
          progress: Optional[Callable[[int, int], None]] = None) -> dict:
    """Build the course print. Returns a summary dict (see emit_docx) plus
    ``unmatched_shots`` (manifest shots whose slide isn't in this package —
    usually a capture from another version), ``missing_files`` (manifest
    shots deleted from the folder), ``extra_shots`` (images added to the
    folder and placed by name) and ``unplaced_shots`` (added images whose
    name doesn't say which screen; they go in an appendix)."""
    model = build_model(source, number=number)
    c = collect_shots(shots, model)
    out = out_path or default_output(source, model.title, model.number)
    summary = emit_docx(model, out, c["by_slide"], footer=footer, unplaced=c["unplaced"],
                        watermark=watermark,
                        progress=progress)
    summary.update(unmatched_shots=c["unmatched"], missing_files=c["deleted"],
                   extra_shots=c["extras"], unplaced_shots=len(c["unplaced"]),
                   manifest=c["manifest"])
    return summary


def default_output(source: str, title: Optional[str] = None,
                   number: Optional[str] = None) -> str:
    """CoursePrint-<Title>_<number>.docx next to the package."""
    folder = os.path.dirname(os.path.abspath(source.rstrip("/\\")))
    if title is None:
        title, number = read_identity(source)
    name = re.sub(r"[^\w\- ]+", "", title).strip()
    name = re.sub(r"[\s-]+", "-", name) or "Course"
    return os.path.join(folder, f"CoursePrint-{name}" + (f"_{number}" if number else "") + ".docx")


# ==================================================================== cleanup
def is_shots_folder(folder: str) -> bool:
    """True only for a folder that looks like course print captures: it has
    a shots_manifest.json, or is named course_print_shots, or holds nothing
    but images (and json). Guards the clean-up button against binning the
    wrong folder."""
    if not folder or not os.path.isdir(folder):
        return False
    folder = os.path.abspath(folder)
    if os.path.dirname(folder) == folder:          # a drive root
        return False
    if os.path.normcase(folder) == os.path.normcase(os.path.expanduser("~")):
        return False
    if os.path.isfile(os.path.join(folder, MANIFEST_NAME)):
        return True
    if os.path.basename(folder).lower() == "course_print_shots":
        return True
    entries = os.listdir(folder)
    return bool(entries) and all(
        os.path.isfile(os.path.join(folder, e)) and e.lower().endswith(_IMAGE_EXT + (".json",))
        for e in entries)


def shots_folder_summary(folder: str) -> tuple:
    """(image count, total bytes) for the confirmation prompt."""
    n = size = 0
    for root, _, files in os.walk(folder):
        for f in files:
            if f.lower().endswith(_IMAGE_EXT):
                n += 1
            try:
                size += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return n, size


def send_to_recycle_bin(path: str) -> None:
    """Move a file or folder to the Windows Recycle Bin (restorable), never
    a permanent delete. Raises OSError if that isn't possible."""
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                        ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                        ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", ctypes.c_void_p),
                        ("lpszProgressTitle", wintypes.LPCWSTR)]

        FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = (
            3, 0x4, 0x10, 0x40, 0x400)
        op = SHFILEOPSTRUCTW(None, FO_DELETE, path + "\0", None,
                             FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI,
                             False, None, None)
        rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        if rc != 0 or op.fAnyOperationsAborted or os.path.exists(path):
            raise OSError(f"Couldn't move {path} to the Recycle Bin (code {rc}). "
                          "Is a file in it open?")
        return
    # not Windows (tests / dev machines): Qt's trash, if available
    try:
        from PySide6.QtCore import QFile
    except ImportError as e:
        raise OSError("No recycle bin available on this system") from e
    if not QFile.moveToTrash(path):
        raise OSError(f"Couldn't move {path} to the trash")


def find_shots_dir(package: str, search_roots) -> Optional[str]:
    """Look for <root>/<package stem>/course_print_shots/shots_manifest.json
    (where the QA Companion writes captures by default)."""
    stem = os.path.splitext(os.path.basename(package.rstrip("/\\")))[0]
    for root in search_roots:
        cand = os.path.join(str(root), stem, "course_print_shots")
        if os.path.isfile(os.path.join(cand, MANIFEST_NAME)):
            return cand
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build a course print .docx from a SCORM package")
    ap.add_argument("source", help="SCORM package .zip or extracted folder")
    ap.add_argument("--shots", help="course_print_shots folder (or shots_manifest.json)")
    ap.add_argument("-o", "--out")
    ap.add_argument("-n", "--number", help="course number (default: from the package)")
    ap.add_argument("--no-footer", action="store_true", help="leave out the confidentiality footer")
    ap.add_argument("--no-watermark", action="store_true", help="leave out the CONFIDENTIAL watermark")
    a = ap.parse_args(argv)
    s = build(a.source, a.out, shots=a.shots, number=a.number, footer=not a.no_footer,
              watermark=not a.no_watermark)
    print(f"{s['title']} ({s['number']}): {s['screens']} screens in {s['modules']} modules, "
          f"{s['images']} screenshots, {s['placeholders']} placeholder(s) -> {s['out']}")
    if s["missing_files"]:
        print(f"  {s['missing_files']} deleted screenshot(s) skipped (a screen whose auto shot "
              "was deleted leads with its first manual capture)")
    if s["extra_shots"]:
        print(f"  {s['extra_shots']} extra screenshot(s) placed by file name")
    if s["unplaced_shots"]:
        print(f"  {s['unplaced_shots']} extra screenshot(s) couldn't be placed by name; "
              "they're under 'Unplaced screenshots' at the end")
    if s["unmatched_shots"]:
        print(f"  note: {s['unmatched_shots']} screenshot(s) didn't match any screen in this package")


if __name__ == "__main__":
    main()
