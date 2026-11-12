"""Course print builder: SCORM package + captured screenshots -> .docx.

Builds a tiny synthetic Storyline package (data.js, frame.js, one js file per
slide, meta.xml) and a capture manifest, so no real course files are needed.
"""

import io
import json
import os
import zipfile

from docx import Document
from docx.oxml.ns import qn
from PIL import Image

from app.automation.course_print import build_print as bp
from app.automation.course_print import docx_to_pptx as d2p


def _gpd(kind, obj):
    payload = json.dumps(obj).replace("\\", "\\\\").replace("'", "\\'")
    return f"window.globalProvideData('{kind}', '{payload}');"


def _text_obj(*paras, acc="text", y=0):
    return {"kind": "vectorshape", "accType": acc, "yPos": y,
            "textLib": [{"vartext": {"blocks": [
                {"spans": [{"text": w} for w in p]} for p in paras]}}]}


def _make_package(path):
    scenes = [
        {"id": "scA", "slides": [{"id": "s1", "title": "Welcome"}]},
        {"id": "scB", "slides": [
            {"id": "s2", "title": "What is Phishing?"},
            {"id": "s3", "title": "Quick Check", "interactions": [{
                "id": "q1", "type": "choice", "issurvey": False,
                "choices": [{"id": "choice_a", "lmstext": "Report it"},
                            {"id": "choice_b", "lmstext": "Click the link"}],
                "answers": [{"status": "correct", "evaluate": {"statements": [
                    {"choiceid": "choices.choice_a"}]}}]}]},
        ]},
        {"id": "scC", "slides": [{"id": "s4", "title": "Thank You"}]},
    ]
    data = {"slideCount": 4, "scenes": scenes}
    frame = {
        "notesData": [
            {"slideId": "scA.s1", "content": "<p>Welcome to the course.</p>"},
            {"slideId": "scB.s2", "content": "<p>Phishing is a scam.</p><p>Stay alert.</p>"},
            {"slideId": "scB.s3", "content": "<p>What should you do?</p>"},
        ],
        "navData": {"outline": {"links": [
            {"slideid": "_player.scA.s1", "displaytext": "Welcome"},     # top-level slide, no group
            {"slideid": "_player.scB", "displaytext": "Module 1: Basics",
             "links": [{"slideid": "_player.scB.s2"}, {"slideid": "_player.scB.s3"}]},
            {"slideid": "_player.scC.s4", "displaytext": "Thank You"},
        ]}},
    }
    slides = {
        "s1": {"slideLayers": [{"isBaseLayer": True, "objects": [
            _text_obj(["%_playerVars.projectSlideTitle%"]),
            _text_obj(["Welcome to the course."]),               # spoken -> not repeated
            _text_obj(["v4-03"]),
            {"kind": "vectorshape", "accType": "button", **_text_obj(["START"])},
        ]}]},
        "s2": {"slideLayers": [
            {"isBaseLayer": True, "objects": [
                _text_obj(["BEST PRACTICES"]),
                _text_obj(["Check the ", "sender", "."]),          # split runs joined
                _text_obj(["1"]),
                {"kind": "vectorshape", "accType": "button", **_text_obj(["NEXT >"])},
            ]},
            {"objects": [_text_obj(["Fake bank email"], ["EXAMPLE 2 OF 2"])]},
            {"objects": [_text_obj(["Fake prize email"], ["EXAMPLE 1 OF 2"])]},
        ]},
        "s3": {"slideLayers": [
            {"isBaseLayer": True, "objects": [
                {"kind": "scrollarea", "objects": [
                    _text_obj(["Click the link"], acc="radio", y=400),   # stored bottom-up
                    _text_obj(["Report it"], acc="radio", y=300)]}]},
            {"objects": [_text_obj(["CORRECT!"], ["Reporting is right."])]},
            {"objects": [_text_obj(["INCORRECT"], ["Reporting is right."])]},
        ]},
        "s4": {"slideLayers": [
            {"isBaseLayer": True, "objects": [_text_obj(["Thanks for taking the course."])]},
            {"objects": [_text_obj(["THAT'S RISKY!"], ["Always verify first."])]},
            {"objects": [_text_obj(["SMART MOVE!"], ["Always verify first."])]},
        ]},
    }
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("meta.xml", '<meta title="Sample Course 12345" />')
        z.writestr("html5/data/js/data.js", _gpd("data", data))
        z.writestr("html5/data/js/frame.js", _gpd("frame", frame))
        for sid, body in slides.items():
            z.writestr(f"html5/data/js/{sid}.js", _gpd("slide", body))


def _png(path, color):
    Image.new("RGB", (320, 180), color).save(path, "PNG")


def _make_shots(folder):
    os.makedirs(folder)
    rows = [("001_s1-1_s1_base.png", "s1", "base", "red"),
            ("002_s2-1_s2_base.png", "s2", "base", "blue"),
            ("003_s2-1_s2_manual.png", "s2", "manual", "green"),
            ("004_s3-1_s3_manual.png", "s3", "manual", "gray"),     # manual only
            ("005_x_other_base.png", "zzz", "base", "black")]       # not in package
    for f, _, _, c in rows:
        _png(os.path.join(folder, f), c)
    manifest = {"shots": [{"seq": i + 1, "file": f, "slide_id": s, "kind": k}
                          for i, (f, s, k, _) in enumerate(rows)]}
    with open(os.path.join(folder, "shots_manifest.json"), "w") as fh:
        json.dump(manifest, fh)


def _pics(p):
    return len(p._p.findall(".//" + qn("w:drawing")))


def _build(tmp_path, shots=True, **kw):
    pkg = tmp_path / "GLSsh_12345_en-US_Sample_v1.zip"
    _make_package(str(pkg))
    shots_dir = tmp_path / "course_print_shots"
    if shots:
        _make_shots(str(shots_dir))
    out = tmp_path / "print.docx"
    summary = bp.build(str(pkg), str(out), shots=str(shots_dir) if shots else None, **kw)
    return summary, Document(str(out))


def test_model_structure(tmp_path):
    pkg = tmp_path / "p.zip"
    _make_package(str(pkg))
    m = bp.build_model(str(pkg))
    assert (m.title, m.number, m.total) == ("Sample Course", "12345", 4)
    assert [mod.heading for mod in m.modules] == ["Introduction", "Module 1: Basics", "Conclusion"]
    s2 = m.screens[1]
    assert s2.onscreen == ["BEST PRACTICES", "Check the sender."]
    assert [ly.heading for ly in s2.layers] == ["Example 1 of 2", "Example 2 of 2"]
    s3 = m.screens[2]
    assert s3.correct == {"report it"}
    assert all(ly.feedback for ly in s3.layers)
    assert s3.onscreen == ["Report it", "Click the link"]     # sorted top-to-bottom
    s4 = m.screens[3]
    assert [ly.heading for ly in s4.layers] == ["That's Risky!", "Smart Move!"]
    assert all(ly.feedback for ly in s4.layers)                # same text -> feedback


def test_document_layout(tmp_path):
    summary, doc = _build(tmp_path)
    paras = [(p.style.name, p.text) for p in doc.paragraphs]
    assert paras[0] == ("Title", "Sample Course")
    assert paras[1] == ("Subtitle", "(12345)")
    h2 = [t for s, t in paras if s == "Heading 2"]
    assert h2 == ["Screen 1 of 4 | Welcome", "Screen 2 of 4 | What is Phishing?",
                  "Screen 3 of 4 | Quick Check", "Screen 4 of 4 | Thank You"]
    texts = [t for _, t in paras]
    assert texts.count("Content:") == 4
    # narration not repeated as on-screen text; chrome / buttons / version stamps dropped
    assert texts.count("Welcome to the course.") == 1
    for junk in ("START", "NEXT >", "v4-03", "1"):
        assert junk not in texts
    assert ("List Bullet", "Report it [Correct]") in paras
    assert ("List Bullet", "Click the link") in paras
    # identical correct/incorrect feedback collapses to one line
    assert texts.count("Feedback: Reporting is right.") == 1
    assert texts.count("Feedback: Always verify first.") == 1
    assert ("Heading 3", "Interactive states") in paras
    # screen 4 had no capture -> placeholder that docx_to_pptx recognises
    ph = bp.PLACEHOLDER_FMT.format(n=4)
    assert ph in texts and d2p._PLACEHOLDER_RE.match(ph)

    assert summary["images"] == 4
    assert summary["placeholders"] == 1
    assert summary["unmatched_shots"] == 1
    assert sum(_pics(p) for p in doc.paragraphs) == 4


def test_screenshot_placement(tmp_path):
    _, doc = _build(tmp_path)
    ps = doc.paragraphs
    idx = {p.text: i for i, p in enumerate(ps) if p.text}
    s2 = idx["Screen 2 of 4 | What is Phishing?"]
    assert _pics(ps[s2 + 1]) == 1                      # base shot right under the heading
    assert ps[s2 + 2].text == "Content:"
    states = idx["Interactive states"]
    assert _pics(ps[states + 1]) == 1                  # the manual state
    s3 = idx["Screen 3 of 4 | Quick Check"]
    assert _pics(ps[s3 + 1]) == 1                      # manual-only screen still leads with its shot


def test_page_setup_and_fonts(tmp_path):
    _, doc = _build(tmp_path)
    sec = doc.sections[0]
    assert round(sec.page_width.inches, 2) == 8.5 and round(sec.page_height.inches, 2) == 11
    assert all(round(getattr(sec, m).inches, 2) == 1.0
               for m in ("left_margin", "right_margin", "top_margin", "bottom_margin"))
    for name in ("Normal", "Title", "Heading 1", "Heading 2", "Heading 3"):
        rf = doc.styles[name].element.rPr.find(qn("w:rFonts"))
        assert rf.get(qn("w:ascii")) == "Open Sans"
        assert rf.get(qn("w:asciiTheme")) is None
    pic = next(p for p in doc.paragraphs if _pics(p))
    ext = pic._p.find(".//" + qn("wp:extent"))
    assert abs(int(ext.get("cx")) - 5943600) < 10       # 6.5" wide
    assert bp.FOOTER_TEXT in sec.footer.paragraphs[0].text


def test_no_footer_no_shots(tmp_path):
    summary, doc = _build(tmp_path, shots=False, footer=False)
    assert summary["images"] == 0 and summary["placeholders"] == 4
    assert bp.FOOTER_TEXT not in doc.sections[0].footer.paragraphs[0].text


def test_feeds_powerpoint_conversion(tmp_path):
    summary, _ = _build(tmp_path)
    cp = d2p.parse_course_print(summary["out"])
    assert cp.title == "Sample Course"
    assert [n.heading for n in cp.nodes if n.level == 2][0] == "Screen 1 of 4 | Welcome"


def test_find_shots_dir(tmp_path):
    root = tmp_path / "runtime_qa"
    _make_shots(str(root / "GLSsh_12345_x" / "course_print_shots"))
    assert bp.find_shots_dir(str(tmp_path / "GLSsh_12345_x.zip"), [root]).endswith("course_print_shots")
    assert bp.find_shots_dir(str(tmp_path / "other.zip"), [root]) is None


def test_course_number_from_filename():
    assert bp.guess_course_number("GLSsh_11594_en-US_AntiPhishEss_v4-03_sc24.zip") == "11594"
    assert bp.guess_course_number("GLSsh_11570-OWASP2025_en-us_v1_27.zip") == "11570"


def test_default_output_name(tmp_path):
    pkg = tmp_path / "GLSsh_12345_en-US_Sample_v1.zip"
    _make_package(str(pkg))
    assert bp.default_output(str(pkg)) == str(tmp_path / "CoursePrint-Sample-Course_12345.docx")
    summary = bp.build(str(pkg))
    assert summary["out"] == str(tmp_path / "CoursePrint-Sample-Course_12345.docx")


# ------------------------------------------------ extra / deleted screenshots
def _colors(doc, para):
    """Colour of each picture in a paragraph, in order (shots are solid fills)."""
    out = []
    for blip in para._p.iter(qn("a:blip")):
        part = doc.part.related_parts[blip.get(qn("r:embed"))]
        out.append(Image.open(io.BytesIO(part.blob)).convert("RGB").getpixel((5, 5)))
    return out


RED, BLUE, GREEN, GRAY = (255, 0, 0), (0, 0, 255), (0, 128, 0), (128, 128, 128)
YELLOW, PURPLE, ORANGE, WHITE = (255, 255, 0), (128, 0, 128), (255, 165, 0), (255, 255, 255)


def _section_pics(doc, heading):
    """Picture colours between `heading` and the next Heading 1/2."""
    ps, out, on = doc.paragraphs, [], False
    for p in ps:
        if p.text == heading:
            on = True
            continue
        if on and p.style.name in ("Heading 1", "Heading 2"):
            break
        if on:
            out.extend(_colors(doc, p))
    return out


def test_deleted_base_replaced_by_first_manual(tmp_path):
    pkg = tmp_path / "GLSsh_12345_x.zip"
    _make_package(str(pkg))
    shots = tmp_path / "shots"
    _make_shots(str(shots))
    os.remove(shots / "002_s2-1_s2_base.png")              # reviewer deleted a bad auto shot
    s = bp.build(str(pkg), str(tmp_path / "o.docx"), shots=str(shots))
    doc = Document(str(tmp_path / "o.docx"))
    assert _section_pics(doc, "Screen 2 of 4 | What is Phishing?") == [GREEN]
    texts = [p.text for p in doc.paragraphs]
    idx = texts.index("Screen 2 of 4 | What is Phishing?")
    assert _colors(doc, doc.paragraphs[idx + 1]) == [GREEN]  # manual shot now leads
    assert s["missing_files"] == 1 and s["placeholders"] == 1


def test_extra_screenshots_placed_by_name_and_number(tmp_path):
    pkg = tmp_path / "GLSsh_12345_x.zip"
    _make_package(str(pkg))
    shots = tmp_path / "shots"
    _make_shots(str(shots))
    _png(shots / "000_s2-1_early.png", YELLOW)             # screen code, numbered before the base
    _png(shots / "002a_s2-1_extra.png", PURPLE)            # between base (2) and manual (3)
    _png(shots / "Screen 4.png", ORANGE)                   # "Screen X of N" number
    _png(shots / "011_s4_extra.png", WHITE)                # slide id in the name
    _png(shots / "random photo.png", GRAY)                 # no screen in the name
    s = bp.build(str(pkg), str(tmp_path / "o.docx"), shots=str(shots))
    doc = Document(str(tmp_path / "o.docx"))
    # base leads, then the rest in file-number order
    assert _section_pics(doc, "Screen 2 of 4 | What is Phishing?") == [BLUE, YELLOW, PURPLE, GREEN]
    # screen 4 had no capture: first extra (by number) leads, un-numbered after
    assert _section_pics(doc, "Screen 4 of 4 | Thank You") == [WHITE, ORANGE]
    assert _section_pics(doc, "Unplaced screenshots") == [GRAY]
    assert s["extra_shots"] == 4 and s["unplaced_shots"] == 1 and s["placeholders"] == 0


def test_folder_without_manifest(tmp_path):
    pkg = tmp_path / "GLSsh_12345_x.zip"
    _make_package(str(pkg))
    shots = tmp_path / "shots"
    os.makedirs(shots)
    _png(shots / "Screen 1.png", RED)
    s = bp.build(str(pkg), str(tmp_path / "o.docx"), shots=str(shots))
    assert s["extra_shots"] == 1 and s["placeholders"] == 3 and s["manifest"] is None


# ------------------------------------------------------ watermark / clean-up
def test_confidential_watermark(tmp_path):
    _, doc = _build(tmp_path)
    hdr = doc.sections[0].header._element.xml
    assert "PowerPlusWaterMarkObject" in hdr and 'string="CONFIDENTIAL"' in hdr
    assert 'fillcolor="silver"' in hdr and "rotation:315" in hdr and "z-index:-" in hdr
    os.makedirs(tmp_path / "nw")
    _, doc2 = _build(tmp_path / "nw", shots=False, watermark=False)
    assert "PowerPlusWaterMarkObject" not in doc2.sections[0].header._element.xml


def test_is_shots_folder_guards(tmp_path):
    shots = tmp_path / "course_print_shots"
    _make_shots(str(shots))
    assert bp.is_shots_folder(str(shots))
    assert bp.shots_folder_summary(str(shots))[0] == 5
    plain = tmp_path / "pics"
    os.makedirs(plain)
    _png(plain / "a.png", RED)
    assert bp.is_shots_folder(str(plain))                       # images only: ok
    (plain / "notes.docx").write_bytes(b"x")
    assert not bp.is_shots_folder(str(plain))                   # anything else: refuse
    assert not bp.is_shots_folder(str(tmp_path / "missing"))
    assert not bp.is_shots_folder(os.path.abspath(os.sep))
    assert not bp.is_shots_folder(os.path.expanduser("~"))
