"""
Parses SCORM zip and Articulate Storyline .story files into structured data.

SCORM zip (Storyline HTML5 output) provides:
  - lms/scormdriver.js        — SCORM API implementation
  - html5/data/js/data.js     — scenes, slides, questions, quiz config, scoring
  - html5/data/js/frame.js    — player controls config (speed control, nav flow)
  - html5/data/js/<id>.js     — per-slide text content
  - imsmanifest.xml           — course title, SCORM version

.story file (Articulate project file, which is itself a ZIP) provides:
  - story/playerProps.xml     — authoritative player settings
"""

import json
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Low-level JS/JSON parsing
# ---------------------------------------------------------------------------

def _parse_js_data(raw_bytes: bytes, data_type: str) -> Optional[dict]:
    """
    Parse Storyline's globalProvideData JS format.

    Storyline serialises objects as JSON, then embeds that JSON string
    inside a single-quoted JS string.  Backslashes and unicode escapes
    inside the JSON value are therefore double-escaped.
    encode('raw_unicode_escape') + decode('unicode_escape') reliably
    reverses that double-escaping before passing to json.loads.
    """
    try:
        content = raw_bytes.decode("utf-8", errors="replace").lstrip("\ufeff")
        m = re.search(
            rf"window\.globalProvideData\('{re.escape(data_type)}',\s*'(\{{.+\}})'",
            content,
            re.DOTALL,
        )
        if not m:
            return None
        raw = m.group(1).encode("raw_unicode_escape").decode("unicode_escape")
        return json.loads(raw)
    except Exception:
        return None


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode basic entities."""
    text = re.sub(r"<[^>]+>", "", text)
    text = (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&nbsp;", " ")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
    )
    return text.strip()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Choice:
    id: str
    text: str


@dataclass
class Interaction:
    id: str
    lms_id: str
    question_text: str
    question_type: str  # 'multiplechoice', 'multipleresponse', 'truefalse', etc.
    choices: list          # list of Choice
    correct_choice_ids: list  # choice IDs whose status is 'correct'
    is_survey: bool


@dataclass
class AccObject:
    """
    Per-object accessibility data extracted from a slide layer.
    Captures everything a screen-reader / keyboard-nav check needs.
    """
    obj_id: str = ""
    kind: str = ""              # 'vectorshape', 'image', etc.
    acc_type: str = ""          # 'text' | 'button' | 'radio' | '' (missing)
    tab_index: int = -1         # -1 = missing
    tab_enabled: bool = False
    reference_name: str = ""    # developer-assigned name (used by JAWS when no text)
    radio_group: str = ""
    text: str = ""              # joined text from all spans (may be empty)
    has_text: bool = False
    alt_text: str = ""          # from states[].data.vectorData.altText
    has_click_event: bool = False   # has events list with onrelease/onclick
    max_font_size: float = 0.0
    x_pos: float = 0.0
    y_pos: float = 0.0
    width: float = 0.0
    height: float = 0.0


@dataclass
class LayerInfo:
    layer_id: str = ""
    is_base: bool = False
    present_as: str = ""        # 'layer' | 'dialog' | ''
    modal: bool = False
    labeled_by_id: str = ""
    described_by_id: str = ""
    objects: list = field(default_factory=list)  # list of AccObject


@dataclass
class SlideInfo:
    scene_number: int
    scene_id: str
    scene_title: str
    slide_number: int       # slideNumberInScene (1-based within scene)
    slide_id: str
    slide_title: str
    lms_id: str             # e.g. "Slide2"
    html5url: str           # relative path inside zip
    interactions: list      # list of Interaction
    texts: list = field(default_factory=list)     # extracted at load time
    external_links: list = field(default_factory=list)  # URLs from open_url actions
    is_in_menu: bool = False
    duration_ms: float = 0.0    # max timeline duration across layers (ms)
    layers: list = field(default_factory=list)    # list of LayerInfo (acc data)
    prev_nav_kind: str = ""       # '' | none | gotoplay | history_prev | conditional | unresolved
    prev_nav_target: str = ""     # target slide_id when prev_nav_kind == 'gotoplay'
    history_prev_buttons: int = 0 # base-layer custom Back buttons using history_prev


@dataclass
class ScormData:
    course_title: str
    scorm_version: str
    scenes_raw: list            # raw scene dicts from data.js
    slides: list                # list of SlideInfo (flat, all scenes)
    quiz_configs: list          # raw quiz dicts from data.js
    scoring_configs: list       # raw scoring dicts from data.js
    scormdriver_js: str         # raw text of scormdriver.js
    frame_data: dict            # parsed frame.js data
    nav_outline: list           # frame.navData.outline.links
    zip_path: str
    parse_errors: list = field(default_factory=list)

    # Derived convenience
    speed_control_enabled: Optional[bool] = None
    navigation_flow: Optional[str] = None  # 'free' | 'restricted'


@dataclass
class StoryData:
    speed_control_enabled: Optional[bool] = None
    navigation_flow: Optional[str] = None
    sidebar_enabled: Optional[bool] = None
    menu_autonumber: Optional[bool] = None
    player_title: Optional[str] = None
    story_path: str = ""
    parse_errors: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Slide text extraction
# ---------------------------------------------------------------------------

def _extract_texts_from_slide_data(slide_data: dict) -> list:
    """
    Walk the objects in a parsed slide JS data blob and collect all text
    strings from textLib[].vartext.blocks[].spans[].text.
    """
    texts = []
    for layer in slide_data.get("slideLayers", []):
        for obj in layer.get("objects", []):
            tl = obj.get("textLib", [])
            if isinstance(tl, dict):
                tl = [tl]
            if not isinstance(tl, list):
                continue
            for td in tl:
                for block in td.get("vartext", {}).get("blocks", []):
                    for span in block.get("spans", []):
                        t = span.get("text", "").strip()
                        if t:
                            texts.append(t)
    return texts


def _extract_duration_from_slide_data(slide_data: dict) -> float:
    """
    Return the max timeline duration (in ms) across all slide layers.

    Storyline stores each layer's timeline as {"duration": <ms>} inside
    slideLayers[].timeline.  The base layer's duration is the best proxy
    for how long the slide plays before advancing.
    """
    max_dur = 0.0
    for layer in slide_data.get("slideLayers", []):
        tl = layer.get("timeline", {})
        dur = tl.get("duration", 0)
        if isinstance(dur, (int, float)) and dur > max_dur:
            max_dur = dur
    return max_dur


def _build_acc_object(obj: dict) -> AccObject:
    """
    Build an AccObject from a single slideLayers[].objects[] entry.
    Pulls accType / tabIndex / textLib / states / events for screen-reader checks.
    """
    ti_raw = obj.get("tabIndex", -1)
    try:
        tab_index = int(ti_raw)
    except (TypeError, ValueError):
        tab_index = -1

    ao = AccObject(
        obj_id=obj.get("id", "") or "",
        kind=obj.get("kind", "") or "",
        acc_type=obj.get("accType", "") or "",
        tab_index=tab_index,
        tab_enabled=bool(obj.get("tabEnabled", False)),
        reference_name=obj.get("referenceName", "") or "",
        radio_group=obj.get("radioGroup", "") or "",
        x_pos=float(obj.get("xPos", 0) or 0),
        y_pos=float(obj.get("yPos", 0) or 0),
        width=float(obj.get("width", 0) or 0),
        height=float(obj.get("height", 0) or 0),
    )

    # textLib → joined text + max font size across spans
    tl = obj.get("textLib", [])
    if isinstance(tl, dict):
        tl = [tl]
    if isinstance(tl, list):
        text_parts = []
        max_font = 0.0
        for td in tl:
            for block in td.get("vartext", {}).get("blocks", []):
                for span in block.get("spans", []):
                    t = span.get("text", "") or ""
                    if t:
                        text_parts.append(t)
                    style = span.get("style", {}) or {}
                    fs = style.get("fontSize") or style.get("fontsize") or 0
                    try:
                        fsv = float(fs)
                        if fsv > max_font:
                            max_font = fsv
                    except (TypeError, ValueError):
                        pass
        ao.text = " ".join(text_parts).strip()
        ao.has_text = bool(ao.text)
        ao.max_font_size = max_font

    # alt text from button states (Storyline stores button alt text here)
    states = obj.get("states", [])
    if isinstance(states, list):
        for st in states:
            data = st.get("data", {}) or {}
            vd = data.get("vectorData", {}) or {}
            alt = vd.get("altText") or ""
            if alt:
                ao.alt_text = alt
                break

    # events → click handler detection
    events = obj.get("events")
    if isinstance(events, list):
        for ev in events:
            kind = ev.get("kind", "") if isinstance(ev, dict) else ""
            if kind in ("onrelease", "onclick", "onpress", "ontouchend"):
                ao.has_click_event = True
                break

    return ao


def _walk_nested_objects(raw_obj: dict, out: list):
    """
    Recursively walk a Storyline object and append AccObject for the object
    itself plus every nested child found inside container kinds (scrollarea,
    shufflegroup, group, etc.).  Storyline nests interactive items several
    levels deep — e.g. quiz radio buttons live at
    scrollarea > shufflegroup > vectorshape — so a flat one-level walk would
    miss them entirely.
    """
    out.append(_build_acc_object(raw_obj))
    children = raw_obj.get("objects")
    if isinstance(children, list):
        for child in children:
            if isinstance(child, dict):
                _walk_nested_objects(child, out)


def _extract_layers_from_slide_data(slide_data: dict) -> list:
    """
    Walk slideLayers and produce a list of LayerInfo with per-object AccObject
    data, recursively flattening nested object containers so that quiz radios
    inside scrollarea/shufflegroup containers are visible to JAWS checks.
    Used by JAWS / screen-reader checks only — the flat texts list used by
    the other checks is left alone to avoid shifting unrelated results.
    """
    layers = []
    for layer in slide_data.get("slideLayers", []):
        li = LayerInfo(
            layer_id=layer.get("id", "") or "",
            is_base=bool(layer.get("isBaseLayer", False)),
            present_as=layer.get("presentAs", "") or "",
            modal=bool(layer.get("modal", False)),
            labeled_by_id=layer.get("labeledById", "") or "",
            described_by_id=layer.get("describedById", "") or "",
        )
        for obj in layer.get("objects", []):
            _walk_nested_objects(obj, li.objects)
        layers.append(li)
    return layers


def _extract_external_links_from_slide_data(slide_data: dict) -> list:
    """
    Recursively walk a parsed slide JS data blob and return all URLs from
    open_url actions (i.e. links to external websites).
    """
    urls = []

    def _walk(obj):
        if isinstance(obj, dict):
            if obj.get("kind") == "open_url":
                url = obj.get("url", "")
                if url.startswith("http"):
                    urls.append(url)
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for v in obj:
                _walk(v)

    _walk(slide_data)
    return urls


# ---------------------------------------------------------------------------
# Interaction (question) parsing
# ---------------------------------------------------------------------------

def _parse_interactions(raw_interactions: list) -> list:
    result = []
    for ia in raw_interactions:
        # Collect choices
        choices = [
            Choice(id=c["id"], text=c.get("lmstext", "").strip())
            for c in ia.get("choices", [])
        ]
        choice_by_id = {c.id: c for c in choices}

        # Determine correct choices from answers with status='correct'
        # choiceid in statements is "choices.choice_XXXX"; strip only "choices."
        # so the remainder "choice_XXXX" matches the Choice.id field.
        correct_ids = []
        for ans in ia.get("answers", []):
            if ans.get("status") == "correct":
                for stmt in ans.get("evaluate", {}).get("statements", []):
                    cid_raw = stmt.get("choiceid", "")
                    cid = cid_raw.replace("choices.", "")  # → "choice_XXXX"
                    if cid and cid in choice_by_id:
                        correct_ids.append(cid)
                break  # only need the first 'correct' answer block

        result.append(
            Interaction(
                id=ia.get("id", ""),
                lms_id=ia.get("lmsId", ""),
                question_text=ia.get("lmstext", "").strip(),
                question_type=ia.get("type", "unknown"),
                choices=choices,
                correct_choice_ids=correct_ids,
                is_survey=ia.get("issurvey", False),
            )
        )
    return result


# ---------------------------------------------------------------------------
# Nav outline helpers
# ---------------------------------------------------------------------------

def _collect_menu_slide_ids(links: list, ids: set):
    """Recursively collect all slide IDs present in the nav outline."""
    for link in links:
        raw_id = link.get("slideid", "")
        # format: "_player.sceneId.slideId" or "_player.sceneId"
        parts = raw_id.lstrip("_player.").split(".")
        if len(parts) == 2:
            ids.add(parts[1])
        _collect_menu_slide_ids(link.get("links", []), ids)


# ---------------------------------------------------------------------------
# Previous-button navigation extraction
# ---------------------------------------------------------------------------

_PREV_BUTTON_GROUP = "ActGrpOnPrevButtonClick"
_CLICK_EVENTS = ("onrelease", "onclick", "onpress", "ontouchend")


def _collect_nav_actions(node) -> list:
    """Collect ('gotoplay', last_segment, in_conditional) and
    ('history_prev', '', in_conditional) tuples from an action subtree."""
    out = []

    def walk(o, in_cond):
        if isinstance(o, dict):
            k = o.get("kind")
            if k == "gotoplay":
                val = o.get("objRef", {}).get("value", "") or ""
                out.append(("gotoplay", val.split(".")[-1], in_cond))
            elif k == "history_prev":
                out.append(("history_prev", "", in_cond))
            cond = in_cond or k == "if_action"
            for v in o.values():
                walk(v, cond)
        elif isinstance(o, list):
            for v in o:
                walk(v, in_cond)

    walk(node, False)
    return out


def _extract_prev_nav(slide_data: dict, valid_slide_ids: set):
    """Resolve the player Previous button target for one slide.

    Returns (kind, target):
      'none'         no ActGrpOnPrevButtonClick (default runtime prev),
      'gotoplay'     single fixed target -> target is a slide id,
      'history_prev' returns to the last-viewed slide,
      'conditional'  branching / multiple / unresolvable target,
      'unresolved'   group present but no readable navigation action.
    """
    groups = slide_data.get("actionGroups", {})
    if not isinstance(groups, dict) or _PREV_BUTTON_GROUP not in groups:
        return ("none", "")
    grp = groups.get(_PREV_BUTTON_GROUP) or {}
    actions = grp.get("actions", grp)
    navs = _collect_nav_actions(actions)
    if not navs:
        return ("unresolved", "")
    if any(n[0] == "history_prev" for n in navs):
        return ("history_prev", "")
    targets = {n[1] for n in navs if n[0] == "gotoplay"}
    branched = any(n[2] for n in navs)
    if branched or len(targets) != 1:
        return ("conditional", "")
    target = next(iter(targets))
    if target not in valid_slide_ids:
        # a scene-level entry or an unknown id — cannot compare statically
        return ("conditional", target)
    return ("gotoplay", target)


def _event_has_history_prev(node) -> bool:
    if isinstance(node, dict):
        if node.get("kind") == "history_prev":
            return True
        return any(_event_has_history_prev(v) for v in node.values())
    if isinstance(node, list):
        return any(_event_has_history_prev(v) for v in node)
    return False


def _count_base_history_prev_buttons(slide_data: dict) -> int:
    """Count base-layer objects whose click event fires history_prev
    (custom Back buttons using last-viewed navigation)."""
    count = 0

    def walk_obj(o):
        nonlocal count
        if isinstance(o, dict):
            if o.get("kind") in ("vectorshape", "image", "group") and isinstance(
                o.get("events"), list
            ):
                for ev in o["events"]:
                    if (
                        isinstance(ev, dict)
                        and ev.get("kind") in _CLICK_EVENTS
                        and _event_has_history_prev(ev)
                    ):
                        count += 1
                        break
            for v in o.values():
                walk_obj(v)
        elif isinstance(o, list):
            for v in o:
                walk_obj(v)

    for layer in slide_data.get("slideLayers", []):
        if not layer.get("isBaseLayer", False):
            continue
        for obj in layer.get("objects", []):
            walk_obj(obj)
    return count


# ---------------------------------------------------------------------------
# Main parsers
# ---------------------------------------------------------------------------

def parse_scorm_zip(zip_path: str) -> ScormData:
    errors = []
    data = ScormData(
        course_title="",
        scorm_version="",
        scenes_raw=[],
        slides=[],
        quiz_configs=[],
        scoring_configs=[],
        scormdriver_js="",
        frame_data={},
        nav_outline=[],
        zip_path=zip_path,
    )

    try:
        with zipfile.ZipFile(zip_path) as zf:
            names_set = set(zf.namelist())

            # --- imsmanifest.xml ---
            if "imsmanifest.xml" in names_set:
                try:
                    raw = zf.read("imsmanifest.xml").decode("utf-8-sig", errors="replace")
                    # Strip default namespace for easier parsing
                    raw_ns = re.sub(r' xmlns="[^"]+"', "", raw)
                    root = ET.fromstring(raw_ns)
                    title_el = root.find(".//organizations/organization/title")
                    if title_el is not None and title_el.text:
                        data.course_title = title_el.text.strip()
                    meta = root.find(".//metadata/schemaversion")
                    if meta is not None and meta.text:
                        data.scorm_version = meta.text.strip()
                except Exception as e:
                    errors.append(f"imsmanifest.xml: {e}")

            # --- scormdriver.js ---
            if "lms/scormdriver.js" in names_set:
                try:
                    data.scormdriver_js = zf.read("lms/scormdriver.js").decode(
                        "utf-8", errors="replace"
                    )
                except Exception as e:
                    errors.append(f"scormdriver.js: {e}")

            # --- frame.js ---
            if "html5/data/js/frame.js" in names_set:
                try:
                    frame = _parse_js_data(zf.read("html5/data/js/frame.js"), "frame")
                    if frame:
                        data.frame_data = frame
                        data.nav_outline = (
                            frame.get("navData", {}).get("outline", {}).get("links", [])
                        )
                        co = frame.get("controlOptions", {})
                        controls = co.get("controls", {})
                        data.speed_control_enabled = controls.get(
                            "playbackSpeedControl", None
                        )
                        nav_opts = frame.get("navData", {}).get("options", {})
                        data.navigation_flow = nav_opts.get("flow", None)
                except Exception as e:
                    errors.append(f"frame.js: {e}")

            # --- data.js ---
            if "html5/data/js/data.js" in names_set:
                try:
                    js_data = _parse_js_data(zf.read("html5/data/js/data.js"), "data")
                    if js_data:
                        data.quiz_configs = js_data.get("quizzes", [])
                        data.scoring_configs = js_data.get("scorings", [])

                        # Collect menu slide IDs from nav outline
                        menu_ids: set = set()
                        _collect_menu_slide_ids(data.nav_outline, menu_ids)

                        # Also get navigation_flow from data.js if not in frame
                        if data.navigation_flow is None:
                            for scene in js_data.get("scenes", []):
                                pass  # flow is in playerProps/frame, not data.js

                        # Build flat slide list
                        for scene in js_data.get("scenes", []):
                            if scene.get("isMessageScene"):
                                continue
                            scene_num = scene.get("sceneNumber", 0)
                            scene_id = scene.get("id", "")
                            for slide in scene.get("slides", []):
                                slide_id = slide.get("id", "")
                                slide_title = _strip_html(slide.get("title", ""))
                                interactions = _parse_interactions(
                                    slide.get("interactions", [])
                                )
                                si = SlideInfo(
                                    scene_number=scene_num,
                                    scene_id=scene_id,
                                    scene_title="",  # filled below from nav
                                    slide_number=slide.get("slideNumberInScene", 0),
                                    slide_id=slide_id,
                                    slide_title=slide_title,
                                    lms_id=slide.get("lmsId", ""),
                                    html5url=slide.get("html5url", ""),
                                    interactions=interactions,
                                    is_in_menu=(slide_id in menu_ids),
                                )
                                data.slides.append(si)
                        data.scenes_raw = js_data.get("scenes", [])
                except Exception as e:
                    errors.append(f"data.js: {e}")

            # Backfill scene titles from nav outline
            _backfill_scene_titles(data)

            # --- Per-slide JS files (text extraction) ---
            html5_js_prefix = "html5/data/js/"
            slide_by_id = {s.slide_id: s for s in data.slides}
            for slide in data.slides:
                if not slide.html5url:
                    continue
                # html5url is like "html5/data/js/<id>.js"
                if slide.html5url in names_set:
                    try:
                        sd = _parse_js_data(zf.read(slide.html5url), "slide")
                        if sd:
                            slide.texts = _extract_texts_from_slide_data(sd)
                            slide.external_links = _extract_external_links_from_slide_data(sd)
                            slide.duration_ms = _extract_duration_from_slide_data(sd)
                            slide.layers = _extract_layers_from_slide_data(sd)
                            _valid_ids = set(slide_by_id.keys())
                            slide.prev_nav_kind, slide.prev_nav_target = _extract_prev_nav(sd, _valid_ids)
                            slide.history_prev_buttons = _count_base_history_prev_buttons(sd)
                    except Exception as e:
                        errors.append(f"{slide.html5url}: {e}")

    except zipfile.BadZipFile as e:
        errors.append(f"Cannot open ZIP: {e}")

    data.parse_errors = errors
    return data


def _backfill_scene_titles(data: ScormData):
    """
    Use the nav outline to fill in scene_title for each slide.
    Nav outline top-level links are modules/scenes.
    """
    scene_title_map: dict = {}
    for link in data.nav_outline:
        raw_id = link.get("slideid", "").lstrip("_player.")
        # Top-level links with child links are scene entries
        if link.get("links"):
            scene_title_map[raw_id] = link.get("displaytext", "")
    for slide in data.slides:
        if slide.scene_id in scene_title_map:
            slide.scene_title = scene_title_map[slide.scene_id]


def parse_story_file(story_path: str) -> StoryData:
    sd = StoryData(story_path=story_path)
    errors = []

    try:
        with zipfile.ZipFile(story_path) as zf:
            if "story/playerProps.xml" not in set(zf.namelist()):
                errors.append("story/playerProps.xml not found")
                sd.parse_errors = errors
                return sd

            raw = zf.read("story/playerProps.xml").decode("utf-8-sig", errors="replace")
            # playerProps is dense single-line XML; use regex for key settings
            # since namespace handling is complex

            def _opt_val(name: str) -> Optional[str]:
                # Match <option name="X" value="Y"> or <option value="Y" name="X">
                m = re.search(
                    rf'<option\s+[^>]*?name="{re.escape(name)}"[^>]*?value="([^"]+)"',
                    raw,
                )
                if m:
                    return m.group(1)
                m = re.search(
                    rf'<option\s+[^>]*?value="([^"]+)"[^>]*?name="{re.escape(name)}"',
                    raw,
                )
                return m.group(1) if m else None

            speed_val = _opt_val("playbackSpeedControl")
            sd.speed_control_enabled = (
                speed_val.lower() == "true" if speed_val else None
            )

            flow_val = _opt_val("flow")
            sd.navigation_flow = flow_val  # 'free' or 'restricted'

            sidebar_val = _opt_val("sidebar_enabled")
            sd.sidebar_enabled = (
                sidebar_val.lower() == "true" if sidebar_val else None
            )

            autonumber_val = _opt_val("autonumber")
            sd.menu_autonumber = (
                autonumber_val.lower() == "true" if autonumber_val else None
            )

            # Player title
            m_title = re.search(r'name="title_text"\s+value="([^"]+)"', raw)
            if m_title:
                sd.player_title = m_title.group(1)

    except zipfile.BadZipFile as e:
        errors.append(f"Cannot open .story file: {e}")

    sd.parse_errors = errors
    return sd
