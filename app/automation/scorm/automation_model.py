"""
Runtime-automation model for Storyline SCORM courses.

Builds the map a browser driver needs to play a course deterministically —
without discovering anything at runtime — and, just as importantly, decides
which screens are safely automatable and which must be handed to a human.

What it recovers per slide
--------------------------
* every clickable object (id, accessible name, which LAYER it lives on)
* the forward "continue" button, identified by ACTION (a trigger that jumps to
  another slide) rather than by label — so "START", "LET'S BEGIN", "Continue"
  are all found
* branch forks (more than one distinct forward target)
* the requirements that unlock forward progress, in the three grammars real
  Storyline courses actually use:

    1. AND / "click them all"  — `<obj>.#_visited == true` conditions, AND-ed,
       usually inside the continue button's OWN actionGroups (markers, tabs).
    2. OR / "pick one"        — the continue button lives on a non-base LAYER,
       and one or more objects reveal that layer via `show_slidelayer`
       (Yes/No buttons, knowledge-check choices). Clicking any one is enough.
    3. Quiz gate              — `<question>.$Answered == true` in the slide's
       NavigationRestrictionNextSlide block. Answer keys come from parser.py.

* per-question answer keys, so a driver can answer to PASS or FAIL on demand

Automatable vs manual review
----------------------------
Every slide is classified `auto` or `review`. Anything we cannot confidently
drive (text entry, a continue button on a layer nothing seems to reveal, no
forward control at all) is flagged with a reason and collected into
`model.manual_review` — the failsafe list a human works through. The driver is
expected to skip those screens (via the menu) rather than stall.

Runnable stand-alone:
    python -m app.automation.scorm.automation_model course.zip
"""

from __future__ import annotations

import json
import os
import re
import sys
import zipfile
from dataclasses import dataclass, field, asdict
from typing import Optional

from app.automation.scorm.parser import _parse_js_data, parse_scorm_zip, ScormData


# Trigger kinds that move the learner to another slide.
_ADVANCE_KINDS = {"gotoplay", "nextslide", "next_slide", "jumpto"}
_CLICK_EVENTS = {"onrelease", "onclick", "onpress", "ontouchend"}
# Trigger kinds that reveal a slide layer (this is how "Continue" often appears).
_LAYER_SHOW_KINDS = {"show_slidelayer"}
# Media playback actions — objects carrying these are player controls, not content.
_MEDIA_KINDS = {"media_play", "media_pause", "media_toggle", "media_seek"}
# Only a timeline-completion jump means the slide advances with no input.
_AUTO_ADVANCE_EVENTS = {"ontimelinecomplete"}
# Events that can reveal a layer without the learner clicking anything.
_PASSIVE_REVEAL_EVENTS = {
    "ontimelinecomplete", "ontimelinetick", "onslidestart", "ontransitionin",
}

_VISITED_RE = re.compile(r"(?:^|\.)([0-9A-Za-z]{11})\.#_visited$")
_STATE_RE = re.compile(r"(?:^|\.)([0-9A-Za-z]{11})\.#state$")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Requirement:
    kind: str            # 'answer_question' | 'question_status' | 'variable'
    target: str
    operator: str = "eq"
    value: object = True
    raw: str = ""


@dataclass
class RequiredClick:
    """An object that must be clicked before the slide lets you move on."""
    obj_id: str
    name: str = ""
    reveals_layer: str = ""   # set when this object reveals a layer holding Continue


@dataclass
class Clickable:
    obj_id: str
    name: str
    acc_type: str = ""
    layer_id: str = ""
    on_base: bool = True
    is_media: bool = False              # video / audio player control
    advances_to: Optional[str] = None
    direction: str = ""                 # 'forward' | 'back' | ''


@dataclass
class QuestionKey:
    obj_id: str
    lms_id: str
    qtype: str
    correct_choice_ids: list
    all_choice_ids: list
    is_survey: bool


@dataclass
class AutomationSlide:
    slide_id: str
    lms_id: str
    scene_id: str
    title: str
    order: int
    in_menu: bool = False

    clickables: list = field(default_factory=list)        # list[Clickable]
    continue_buttons: list = field(default_factory=list)  # forward nav buttons
    branch_targets: list = field(default_factory=list)

    # requirements
    required_all: list = field(default_factory=list)      # list[RequiredClick]  (AND)
    required_any: list = field(default_factory=list)      # list[RequiredClick]  (OR)
    gate: list = field(default_factory=list)              # list[Requirement]    (quiz)
    questions: list = field(default_factory=list)         # list[QuestionKey]

    # passive behaviour (no click needed)
    auto_advance: Optional[str] = None      # slide jumps here on its own (timeline end)
    auto_revealed_layers: list = field(default_factory=list)  # Continue layers the timeline reveals

    # classification
    automation: str = "auto"                              # 'auto' | 'review'
    review_reasons: list = field(default_factory=list)
    flags: list = field(default_factory=list)             # e.g. 'text_entry', 'video'

    @property
    def is_gated(self) -> bool:
        return bool(self.required_all or self.required_any or self.gate)

    def to_dict(self):
        d = asdict(self)
        d["is_gated"] = self.is_gated
        return d


@dataclass
class AutomationModel:
    course_title: str
    scorm_version: str
    navigation_flow: Optional[str]
    entry_slide: str
    slides: list = field(default_factory=list)

    @property
    def manual_review(self) -> list:
        return [s for s in self.slides if s.automation == "review"]

    def to_dict(self):
        return {
            "course_title": self.course_title,
            "scorm_version": self.scorm_version,
            "navigation_flow": self.navigation_flow,
            "entry_slide": self.entry_slide,
            "slides": [s.to_dict() for s in self.slides],
            "manual_review": [s.slide_id for s in self.manual_review],
        }


# ---------------------------------------------------------------------------
# Walking helpers
# ---------------------------------------------------------------------------

def _iter_objects(node, out):
    """Collect every object dict (anything with an id that can carry behaviour)."""
    if isinstance(node, dict):
        if node.get("id") and ("events" in node or "accType" in node or "states" in node):
            out.append(node)
        for v in node.values():
            _iter_objects(v, out)
    elif isinstance(node, list):
        for v in node:
            _iter_objects(v, out)


def _acc_name(o: dict) -> str:
    for st in o.get("states", []) or []:
        vd = (st.get("data", {}) or {}).get("vectorData", {}) or {}
        if vd.get("altText"):
            return vd["altText"].strip()
    tl = o.get("textLib", [])
    if isinstance(tl, dict):
        tl = [tl]
    parts = []
    for td in (tl or []):
        for b in td.get("vartext", {}).get("blocks", []):
            for s in b.get("spans", []):
                if s.get("text"):
                    parts.append(s["text"])
    if parts:
        return " ".join(parts).strip()
    return (o.get("referenceName", "") or "").strip()


def _has_click(o: dict) -> bool:
    return any(
        isinstance(ev, dict) and ev.get("kind") in _CLICK_EVENTS
        for ev in (o.get("events", []) or [])
    )


def _collect_action_groups(slide_data: dict) -> dict:
    """Every actionGroup in the slide — slide-level AND per-object.

    Storyline routes most trigger logic through `exe_actiongroup` indirection,
    so effects must be resolved through this map, not read inline.
    """
    groups: dict = {}

    def rec(n):
        if isinstance(n, dict):
            ag = n.get("actionGroups")
            if isinstance(ag, dict):
                groups.update(ag)
            for v in n.values():
                rec(v)
        elif isinstance(n, list):
            for v in n:
                rec(v)

    rec(slide_data)
    return groups


def _tail_id(value: str) -> Optional[str]:
    """'_player.<scene>.<slide>' / '_parent.<layer>' -> last token."""
    if not value:
        return None
    return value.split(".")[-1] or None


def _effects_in(node, groups: dict) -> tuple:
    """Scan any node for its effects: (advance_target, revealed_layer_ids).

    Follows `exe_actiongroup` indirection so effects defined in named action
    groups are seen (Storyline routes most trigger logic that way).
    """
    advance = None
    layers: set = set()
    seen_groups: set = set()

    def rec(x):
        nonlocal advance
        if isinstance(x, dict):
            k = x.get("kind")
            if k in _ADVANCE_KINDS and advance is None:
                ref = x.get("objRef", {})
                val = ref.get("value", "") if isinstance(ref, dict) else ""
                t = _tail_id(val)
                if t:
                    advance = t
            elif k in _LAYER_SHOW_KINDS:
                ref = x.get("objRef", {})
                val = ref.get("value", "") if isinstance(ref, dict) else ""
                t = _tail_id(val)
                if t:
                    layers.add(t)
            elif k == "exe_actiongroup":
                gid = x.get("id")
                if gid and gid not in seen_groups:
                    seen_groups.add(gid)
                    rec(groups.get(gid, {}))
            for v in x.values():
                rec(v)
        elif isinstance(x, list):
            for v in x:
                rec(v)

    rec(node)
    return advance, layers


def _click_effects(o: dict, groups: dict) -> tuple:
    """What does CLICKING this object do? -> (advance_target, revealed_layers)"""
    advance = None
    layers: set = set()
    for ev in o.get("events", []) or []:
        if isinstance(ev, dict) and ev.get("kind") in _CLICK_EVENTS:
            a, ls = _effects_in(ev, groups)
            if a and not advance:
                advance = a
            layers |= ls
    return advance, layers


def _passive_effects(slide_data: dict, groups: dict) -> tuple:
    """Effects that happen WITHOUT the learner clicking anything.

    Storyline slides very often advance on their own (`ontimelinecomplete` ->
    gotoplay) and reveal layers on a timeline tick. Such screens need no click —
    the driver just waits. Missing this made auto-advancing slides look like
    dead ends.

    Returns (auto_advance_target, auto_revealed_layer_ids).
    """
    advance = None
    layers: set = set()

    def rec(node):
        nonlocal advance
        if isinstance(node, dict):
            k = node.get("kind")
            if isinstance(k, str) and k.startswith("on") and k not in _CLICK_EVENTS:
                a, ls = _effects_in(node, groups)
                # ONLY a timeline-completion jump is a true auto-advance.
                # `onslidestart` also contains gotoplay (branch/resume logic) and
                # `onnextslide` is the player's Next handler — neither means the
                # slide moves on by itself.
                if k in _AUTO_ADVANCE_EVENTS and a and not advance:
                    advance = a
                if k in _PASSIVE_REVEAL_EVENTS:
                    layers.update(ls)   # mutate: `|=` would rebind as a local
            for v in node.values():
                rec(v)
        elif isinstance(node, list):
            for v in node:
                rec(v)

    rec(slide_data)
    return advance, layers


def _is_media(o: dict) -> bool:
    if o.get("kind") == "video":
        return True
    found = False

    def rec(x):
        nonlocal found
        if found:
            return
        if isinstance(x, dict):
            if x.get("kind") in _MEDIA_KINDS:
                found = True
                return
            for v in x.values():
                rec(v)
        elif isinstance(x, list):
            for v in x:
                rec(v)

    rec(o.get("events", []) or [])
    return found


def _layer_map(slide_data: dict) -> tuple:
    """obj_id -> (layer_id, is_base), plus the base layer id."""
    obj_layer: dict = {}
    base_id = ""
    for layer in slide_data.get("slideLayers", []) or []:
        lid = layer.get("id", "") or ""
        is_base = bool(layer.get("isBaseLayer", False))
        if is_base:
            base_id = lid
        objs: list = []
        _iter_objects(layer.get("objects", []), objs)
        for o in objs:
            oid = o.get("id")
            if oid and oid not in obj_layer:
                obj_layer[oid] = (lid, is_base)
    return obj_layer, base_id


def _find_kind(slide_data: dict, kind: str) -> bool:
    found = False

    def rec(x):
        nonlocal found
        if found:
            return
        if isinstance(x, dict):
            if x.get("kind") == kind:
                found = True
                return
            for v in x.values():
                rec(v)
        elif isinstance(x, list):
            for v in x:
                rec(v)

    rec(slide_data)
    return found


def _has_text_entry(slide_data: dict) -> bool:
    return _find_kind(slide_data, "textinput")


def _has_exit(slide_data: dict) -> bool:
    """A screen that closes the course (Exit button) — legitimately terminal."""
    return _find_kind(slide_data, "close_player")


# ---------------------------------------------------------------------------
# Requirement extraction
# ---------------------------------------------------------------------------

def _collect_visited_requirements(slide_data: dict) -> list:
    """Objects that must be VISITED (AND) — `<obj>.#_visited == true` compares.

    Only counts real gating compares, not each object's own `_visited` variable
    declaration (which every object carries).
    """
    ids: list = []
    seen = set()

    def rec(node):
        if isinstance(node, dict):
            if node.get("kind") == "compare":
                va = node.get("valuea", "") or ""
                m = _VISITED_RE.search(va)
                if m and node.get("valueb") is True:
                    oid = m.group(1)
                    if oid not in seen:
                        seen.add(oid)
                        ids.append(oid)
                else:
                    ms = _STATE_RE.search(va)
                    if ms and str(node.get("valueb", "")).lower() == "visited":
                        oid = ms.group(1)
                        if oid not in seen:
                            seen.add(oid)
                            ids.append(oid)
            for v in node.values():
                rec(v)
        elif isinstance(node, list):
            for v in node:
                rec(v)

    rec(slide_data)
    return ids


def _classify_ref(valuea: str, operator: str, valueb) -> Requirement:
    raw = valuea or ""
    if ".$Answered" in raw:
        return Requirement("answer_question", raw.split(".$Answered")[0], operator, valueb, raw)
    if ".$Status" in raw:
        return Requirement("question_status", raw.split(".$Status")[0], operator, valueb, raw)
    return Requirement("variable", raw, operator, valueb, raw)


def _parse_quiz_gate(action_groups: dict, slide_id: str) -> list:
    """Quiz gate from the slide-level NavigationRestrictionNextSlide block."""
    block = action_groups.get(f"NavigationRestrictionNextSlide_{slide_id}")
    if not block:
        return []
    compares: list = []

    def rec(n):
        if isinstance(n, dict):
            if n.get("kind") == "compare":
                compares.append(n)
            for v in n.values():
                rec(v)
        elif isinstance(n, list):
            for v in n:
                rec(v)

    rec(block)
    reqs, seen = [], set()
    for c in compares:
        r = _classify_ref(c.get("valuea", ""), c.get("operator", "eq"),
                          c.get("value", c.get("valueb", True)))
        sig = (r.kind, r.target)
        if sig not in seen:
            seen.add(sig)
            reqs.append(r)
    return reqs


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build_automation_model(zip_path: str, data: Optional[ScormData] = None) -> AutomationModel:
    if data is None:
        data = parse_scorm_zip(zip_path)

    order_of = {s.slide_id: i for i, s in enumerate(data.slides)}
    scene_ids = {s.scene_id for s in data.slides}

    model = AutomationModel(
        course_title=data.course_title,
        scorm_version=data.scorm_version,
        navigation_flow=data.navigation_flow,
        entry_slide=data.slides[0].slide_id if data.slides else "",
    )

    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        for s in data.slides:
            a = AutomationSlide(
                slide_id=s.slide_id, lms_id=s.lms_id, scene_id=s.scene_id,
                title=s.slide_title, order=order_of.get(s.slide_id, -1),
                in_menu=bool(getattr(s, "is_in_menu", False)),
            )
            for ia in s.interactions:
                a.questions.append(QuestionKey(
                    obj_id=ia.id, lms_id=ia.lms_id, qtype=ia.question_type,
                    correct_choice_ids=list(ia.correct_choice_ids),
                    all_choice_ids=[c.id for c in ia.choices],
                    is_survey=ia.is_survey,
                ))

            if s.html5url in names:
                sd = _parse_js_data(zf.read(s.html5url), "slide")
                if sd:
                    _build_slide(a, sd, s.slide_id, order_of, scene_ids)

            _classify(a)
            model.slides.append(a)

    return model


def _build_slide(a: AutomationSlide, sd: dict, slide_id: str,
                 order_of: dict, scene_ids: set):
    groups = _collect_action_groups(sd)
    obj_layer, base_layer = _layer_map(sd)

    objs: list = []
    _iter_objects(sd.get("slideLayers", []), objs)

    name_by_id: dict = {}
    reveals: dict = {}          # obj_id -> set(layer ids it reveals)
    seen: set = set()
    fwd_targets: list = []

    for o in objs:
        oid = o.get("id", "")
        if not oid or oid in seen:
            continue
        seen.add(oid)
        nm = _acc_name(o)
        if nm:
            name_by_id[oid] = nm[:60]
        if not _has_click(o):
            continue

        advance, layers = _click_effects(o, groups)
        if layers:
            reveals[oid] = layers

        lid, on_base = obj_layer.get(oid, ("", True))
        direction = ""
        if advance:
            if advance in order_of and slide_id in order_of:
                direction = "forward" if order_of[advance] > order_of[slide_id] else "back"
            else:
                direction = "forward"

        c = Clickable(
            obj_id=oid, name=(nm or "")[:60], acc_type=o.get("accType", "") or "",
            layer_id=lid, on_base=on_base, is_media=_is_media(o),
            advances_to=advance, direction=direction,
        )
        a.clickables.append(c)
        if advance and direction == "forward":
            a.continue_buttons.append(c)
            if advance not in fwd_targets:
                fwd_targets.append(advance)

    if len(fwd_targets) > 1:
        a.branch_targets = fwd_targets

    # --- quiz gate (slide-level nav restriction) ---
    a.gate = _parse_quiz_gate(groups, slide_id)

    # --- AND requirement: click every marker/tab ---
    for oid in _collect_visited_requirements(sd):
        a.required_all.append(RequiredClick(obj_id=oid, name=name_by_id.get(oid, "")))

    # --- passive behaviour: does the slide advance / reveal layers on its own? ---
    auto_target, auto_layers = _passive_effects(sd, groups)
    if auto_target and auto_target != slide_id:
        a.auto_advance = auto_target

    # --- OR requirement: Continue lives on a layer; who reveals that layer? ---
    layers_with_continue = {
        c.layer_id for c in a.continue_buttons if not c.on_base and c.layer_id
    }
    # a layer the timeline reveals by itself needs no click
    a.auto_revealed_layers = sorted(auto_layers & layers_with_continue)
    if layers_with_continue:
        for oid, revealed in reveals.items():
            hit = revealed & layers_with_continue
            if hit:
                a.required_any.append(RequiredClick(
                    obj_id=oid, name=name_by_id.get(oid, ""),
                    reveals_layer=sorted(hit)[0],
                ))

    # --- interaction flags ---
    if _has_text_entry(sd):
        a.flags.append("text_entry")
    if any(c.is_media for c in a.clickables):
        a.flags.append("media")
    if _has_exit(sd):
        a.flags.append("terminal")


def _classify(a: AutomationSlide):
    """Decide whether we can drive this slide, or a human must review it.

    Three things make a slide driveable:
      * a quiz slide with an answer key + Submit — Storyline's quiz engine reveals
        the feedback layer (and its Continue) after Submit, so there is no
        `show_slidelayer` trigger to find. We can still drive it deterministically.
      * a terminal screen (Exit / close_player) — the end of the course, not a stall.
      * a normal screen whose forward control we can reach.

    Anything else gets a reason and lands on the manual-review list. NOTE: this is
    a static best guess. The runtime driver still needs its own failsafe — if a
    screen doesn't progress after doing everything the model says, flag it and skip.
    """
    reasons: list = []

    # A learner must physically type — never automatable.
    if "text_entry" in a.flags:
        reasons.append("has a text-entry field (learner must type)")

    has_answer_key = any(q.correct_choice_ids for q in a.questions)
    has_submit = any("submit" in (c.name or "").lower() for c in a.clickables)
    is_quiz = bool(a.questions) and has_answer_key and has_submit
    if is_quiz and "quiz" not in a.flags:
        a.flags.append("quiz")

    if a.auto_advance:
        # Slide moves on by itself when the timeline ends — nothing to click.
        if "auto_advance" not in a.flags:
            a.flags.append("auto_advance")
    elif is_quiz:
        pass                      # answer from the key, Submit, then Continue
    elif "terminal" in a.flags:
        pass                      # Exit screen — end of course
    elif not a.continue_buttons:
        if not a.clickables:
            reasons.append("no forward control and nothing clickable")
        else:
            reasons.append("no forward control found")
    else:
        only_on_layer = all(not c.on_base for c in a.continue_buttons)
        if only_on_layer and not a.required_any and not a.auto_revealed_layers:
            reasons.append(
                "continue button is on a layer, but no object was found that reveals it"
            )

    a.review_reasons = reasons
    a.automation = "review" if reasons else "auto"


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _req_str(r: Requirement) -> str:
    if r.kind == "answer_question":
        return f"answer question {r.target}"
    if r.kind == "question_status":
        return f"question {r.target} status == {r.value!r}"
    if r.kind == "variable":
        return f"variable {r.target} {r.operator} {r.value!r}"
    return f"{r.kind} {r.target}"


def format_report(model: AutomationModel, max_slides: int = 0) -> str:
    lines = []
    lines.append("=" * 88)
    lines.append(f"COURSE: {model.course_title}  [{model.scorm_version}]")
    auto = [s for s in model.slides if s.automation == "auto"]
    review = model.manual_review
    lines.append(
        f"  slides={len(model.slides)}  nav_flow={model.navigation_flow}  "
        f"AUTOMATABLE={len(auto)}  NEEDS REVIEW={len(review)}  "
        f"questions={sum(len(s.questions) for s in model.slides)}  "
        f"clickables={sum(len(s.clickables) for s in model.slides)}"
    )

    # ---- manual review list first: this is the failsafe worklist ----
    if review:
        lines.append("")
        lines.append("-" * 88)
        lines.append(f"MANUAL REVIEW LIST ({len(review)} screens) — automation should skip these")
        lines.append("-" * 88)
        for s in review:
            lines.append(f"  {s.slide_id} ({s.lms_id}) — {s.title[:52]!r}")
            for r in s.review_reasons:
                lines.append(f"       ! {r}")
    lines.append("")
    lines.append("-" * 88)
    lines.append("PER-SCREEN MAP")
    lines.append("-" * 88)

    shown = 0
    for s in model.slides:
        if not (s.clickables or s.questions):
            continue
        if max_slides and shown >= max_slides:
            break
        shown += 1
        tag = "AUTO " if s.automation == "auto" else "REVIEW"
        lines.append(f"\n  [{tag}] slide {s.slide_id} ({s.lms_id}) — {s.title[:50]!r}")
        for r in s.review_reasons:
            lines.append(f"     ! {r}")
        if s.flags:
            lines.append(f"     flags: {', '.join(s.flags)}")

        if s.auto_advance:
            lines.append(f"     AUTO-ADVANCE → {s.auto_advance}  (timeline ends; no click needed)")
        if s.required_all:
            lines.append(f"     MUST CLICK ALL ({len(s.required_all)}):")
            for rc in s.required_all:
                lines.append(f"        · {rc.obj_id}  {(rc.name or '(unnamed)')!r}")
        if s.required_any:
            lines.append(f"     MUST CLICK ONE OF ({len(s.required_any)}) — reveals the Continue layer:")
            for rc in s.required_any:
                lines.append(f"        · {rc.obj_id}  {(rc.name or '(unnamed)')!r} → layer {rc.reveals_layer}")
        if s.gate:
            lines.append("     QUIZ GATE: " + "; ".join(_req_str(r) for r in s.gate))
        if s.branch_targets:
            lines.append(f"     BRANCH → {', '.join(s.branch_targets)}")

        for c in s.continue_buttons:
            where = "base" if c.on_base else f"layer {c.layer_id}"
            lines.append(f"     continue[{c.direction}] id={c.obj_id} {c.name!r} → {c.advances_to}  ({where})")
        for qk in s.questions:
            lines.append(f"     question {qk.obj_id} ({qk.qtype}) correct={qk.correct_choice_ids}")

        req_ids = {rc.obj_id for rc in s.required_all} | {rc.obj_id for rc in s.required_any}
        others = [c for c in s.clickables if not c.advances_to and c.obj_id not in req_ids]
        if others:
            lines.append(f"     other clickable objects ({len(others)}):")
            for c in others:
                label = c.name or "(unnamed)"
                extra = " [media]" if c.is_media else ""
                where = "" if c.on_base else f" (layer {c.layer_id})"
                lines.append(f"        · {c.obj_id}  {label!r}{extra}{where}")
    return "\n".join(lines)


def _main(argv):
    for zp in argv:
        model = build_automation_model(zp)
        print(format_report(model))
        out = os.path.splitext(zp)[0] + ".automodel.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(model.to_dict(), f, indent=2)
        print(f"\n  → wrote full model: {out}\n")


if __name__ == "__main__":
    _main(sys.argv[1:])
# end of module
