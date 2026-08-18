"""Phase-0 static extensions: the runtime model.

Extends the automation model with everything the deterministic driver needs,
all extracted statically from the package (no runtime discovery):

  * per-clickable click EFFECTS: which layers a click shows/hides, where it
    advances, whether it toggles media — so the driver can verify "the thing
    that opened is the thing that was supposed to open";
  * per-layer identifying objects (a layer renders as an anonymous div; we
    identify it in the DOM by its member objects' data-model-ids);
  * choice -> object binding for quiz answers (choice_<objid> strip-prefix,
    validated against the slide's object tree — unbound choices are reported,
    never guessed);
  * the graded-question inventory (denominator M for question coverage);
  * course-level graph + default forward route(s), entry -> terminal;
  * per-slide timeline duration (wait budget for locked builds).

Output: RuntimeModel (JSON-serializable), consumed by planner and driver.
"""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field, asdict

from ..parser import parse_scorm_zip, _parse_js_data
from ..automation_model import build_automation_model, AutomationModel

_CLICK_EVENTS = {"onrelease", "onclick", "onpress", "ontouchend"}


def _ref_id(a: dict) -> str:
    """Resolve an action's target reference. Storyline encodes it either as
    objRef.id or objRef.value with a path prefix ('_parent._parent.<id>',
    '_player.<sceneId>.<slideId>'). Always take the last path segment."""
    ref = a.get("objRef") or {}
    raw = ref.get("id") or ref.get("value") or a.get("id", "") or ""
    if not isinstance(raw, str):
        return ""
    if raw in ("_this", "_parent"):
        return "_this" if raw == "_this" else ""
    return raw.split(".")[-1] if "." in raw else raw


# ---------------------------------------------------------------- dataclasses

@dataclass
class ClickEffect:
    obj_id: str
    name: str = ""
    on_layer: str = ""            # layer this object lives on ("" = base)
    shows_layers: list = field(default_factory=list)
    hides_layers: list = field(default_factory=list)   # "_this" resolved to own layer
    hide_others: bool = False
    advances_to: str = ""
    exits: bool = False
    is_media_toggle: bool = False
    sets_visited: bool = False


@dataclass
class LayerBinding:
    layer_id: str
    modal: bool = False
    duration_ms: int = 0                               # the layer's own timeline
    ident_objects: list = field(default_factory=list)  # obj_ids that identify this layer in DOM
    close_objects: list = field(default_factory=list)  # obj_ids whose click hides this layer


@dataclass
class ChoiceBinding:
    choice_id: str
    obj_id: str
    text: str = ""
    bound: bool = True


@dataclass
class QuestionEntry:
    """One graded interaction — a row of the coverage denominator M."""
    interaction_id: str
    lms_id: str
    slide_id: str
    qtype: str
    is_survey: bool
    drivable: bool
    not_drivable_reason: str = ""
    choices: list = field(default_factory=list)        # ChoiceBinding dicts
    correct_choice_ids: list = field(default_factory=list)
    submit_obj: str = ""
    # matching (drag-drop) questions: statically-known correct pairs
    pairs: list = field(default_factory=list)          # [{drag_obj, drop_obj, drag_text, drop_text}]
    drop_objs: list = field(default_factory=list)      # all drop-target obj ids


@dataclass
class RuntimeSlide:
    slide_id: str
    lms_id: str
    title: str
    duration_ms: int = 0
    effects: dict = field(default_factory=dict)        # obj_id -> ClickEffect dict
    layers: list = field(default_factory=list)         # LayerBinding dicts
    text_entry: bool = False


@dataclass
class RuntimeModel:
    course_title: str = ""
    scorm_version: str = ""
    entry_slide: str = ""
    pass_percent: float = 80.0
    pass_status: str = "pass"
    fail_status: str = "incomplete"
    slides_viewed_mode: str = ""
    view_threshold: int = 0
    slides: dict = field(default_factory=dict)         # slide_id -> RuntimeSlide dict
    questions: list = field(default_factory=list)      # QuestionEntry dicts (inventory, M)
    automodel: dict = field(default_factory=dict)      # embedded automation model
    menu_items: list = field(default_factory=list)     # flattened outline, DOM order
    routes: dict = field(default_factory=dict)         # name -> [slide_id, ...]
    route_notes: dict = field(default_factory=dict)    # name -> free-text provenance
    unbound_choices: list = field(default_factory=list)
    build_warnings: list = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=1)


# ---------------------------------------------------------------- extraction

def _resolve_groups(slide_raw: dict) -> dict:
    groups = {}

    def walk(o):
        if isinstance(o, dict):
            for g in (o.get("actionGroups") or {}).items() if isinstance(o.get("actionGroups"), dict) else []:
                groups[g[0]] = g[1]
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(slide_raw)
    return groups


def _effects_in(actions, groups, depth=0):
    """Flatten actions, following exe_actiongroup indirection."""
    out = []
    if depth > 6 or not isinstance(actions, list):
        return out
    for a in actions:
        if not isinstance(a, dict):
            continue
        kind = a.get("kind", "")
        if kind == "exe_actiongroup":
            g = groups.get(a.get("id", ""))
            if isinstance(g, dict):
                out.extend(_effects_in(g.get("actions", []), groups, depth + 1))
        else:
            out.append(a)
            # conditional branches
            for key in ("thenActions", "elseActions", "actions"):
                if key in a and isinstance(a.get(key), list):
                    out.extend(_effects_in(a[key], groups, depth + 1))
    return out


def _iter_objects(o, layer_id="", out=None):
    """Yield (object_dict, layer_id) for every object in the slide tree."""
    if out is None:
        out = []
    if isinstance(o, dict):
        this_layer = layer_id
        if o.get("kind") == "slidelayer" or ("slideLayers" not in o and o.get("isBaseLayer") is not None):
            this_layer = "" if o.get("isBaseLayer") else o.get("id", layer_id)
        if o.get("id") and ("events" in o or "accType" in o or "states" in o):
            out.append((o, this_layer))
        for k, v in o.items():
            if k == "slideLayers" and isinstance(v, list):
                for lay in v:
                    lid = "" if lay.get("isBaseLayer") else lay.get("id", "")
                    _iter_objects(lay, lid, out)
            else:
                _iter_objects(v, this_layer, out)
    elif isinstance(o, list):
        for v in o:
            _iter_objects(v, layer_id, out)
    return out


def _obj_name(o) -> str:
    for st in o.get("states", []) or []:
        alt = ((st.get("data") or {}).get("vectorData") or {}).get("altText")
        if alt:
            return alt
    for tl in o.get("textLib", []) or []:
        for blk in (tl.get("vartext") or {}).get("blocks", []) or []:
            for sp in blk.get("spans", []) or []:
                if sp.get("text"):
                    return sp["text"][:60]
    return o.get("referenceName", "") or ""


def _extract_slide_effects(slide_raw: dict) -> tuple[dict, dict]:
    """Return (effects: obj_id->ClickEffect, layers: layer_id->LayerBinding)."""
    groups = _resolve_groups(slide_raw)
    effects: dict[str, ClickEffect] = {}
    layers: dict[str, LayerBinding] = {}

    # layer scaffolding (modal flags + members)
    def find_layers(o):
        if isinstance(o, dict):
            if isinstance(o.get("slideLayers"), list):
                for lay in o["slideLayers"]:
                    if lay.get("isBaseLayer"):
                        continue
                    lid = lay.get("id", "")
                    if lid and lid not in layers:
                        dur = 0
                        tl = lay.get("timeline")
                        if isinstance(tl, dict):
                            dur = int(tl.get("duration", 0) or 0)
                        layers[lid] = LayerBinding(
                            layer_id=lid,
                            modal=bool((lay.get("dialogOptions") or {}).get("modal")
                                       or lay.get("modal")),
                            duration_ms=dur)
            for v in o.values():
                find_layers(v)
        elif isinstance(o, list):
            for v in o:
                find_layers(v)

    find_layers(slide_raw)

    for o, layer_id in _iter_objects(slide_raw):
        oid = o.get("id", "")
        if layer_id and layer_id in layers and oid:
            if len(layers[layer_id].ident_objects) < 6:
                layers[layer_id].ident_objects.append(oid)
        ev_actions = []
        for ev in o.get("events", []) or []:
            if ev.get("kind") in _CLICK_EVENTS:
                ev_actions.extend(_effects_in(ev.get("actions", []), groups))
        if not ev_actions:
            continue
        fx = ClickEffect(obj_id=oid, name=_obj_name(o), on_layer=layer_id)
        for a in ev_actions:
            kind = a.get("kind", "")
            if kind == "show_slidelayer":
                target = _ref_id(a)
                if target:
                    fx.shows_layers.append(target)
                if a.get("hideOthers") in (True, "true", "all", "oncomplete"):
                    fx.hide_others = True
            elif kind == "hide_slidelayer":
                target = _ref_id(a)
                if target in ("_this", "", None):
                    target = layer_id or "_this"
                fx.hides_layers.append(target)
            elif kind in ("gotoplay", "nextslide", "next_slide", "jumpto"):
                ref = _ref_id(a)
                if ref:
                    fx.advances_to = ref
            elif kind == "close_player":
                fx.exits = True
            elif kind in ("media_play", "media_pause", "media_toggle", "media_seek"):
                fx.is_media_toggle = True
            elif kind == "exe_actiongroup" and a.get("id") == "ActGrpSetVisitedState":
                fx.sets_visited = True
        if any(a.get("kind") == "exe_actiongroup" and a.get("id") == "ActGrpSetVisitedState"
               for ev in o.get("events", []) or [] if ev.get("kind") in _CLICK_EVENTS
               for a in ev.get("actions", []) or []):
            fx.sets_visited = True
        effects[oid] = fx

    # close objects per layer: any object on layer L whose click hides L, or —
    # equally effective for clearing L — shows another layer with hideOthers
    for oid, fx in effects.items():
        for hid in fx.hides_layers:
            if hid in layers and oid not in layers[hid].close_objects:
                layers[hid].close_objects.append(oid)
        if fx.on_layer and fx.hide_others and fx.on_layer in layers \
                and oid not in layers[fx.on_layer].close_objects:
            layers[fx.on_layer].close_objects.append(oid)
    return effects, layers


# ---------------------------------------------------------------- graph/route

def _default_route(am: AutomationModel) -> tuple[list, list]:
    """Deterministic forward route entry -> terminal via continue/auto-advance.

    Hub-aware: traverse toward unvisited slides and never re-take an edge (the
    notes' traversal rule). At a hub (many continue buttons), each revisit takes
    the next unused spoke; a spoke's jump back to the hub is a fresh edge."""
    by_id = {s.slide_id: s for s in am.slides}
    route, notes = [], []
    seen_edges: set = set()
    visited: set = set()
    cur = am.entry_slide
    for _ in range(600):
        if not cur or cur not in by_id:
            break
        route.append(cur)
        visited.add(cur)
        s = by_id[cur]
        if "terminal" in s.flags:
            notes.append(f"terminal at {s.lms_id}")
            break
        candidates = []
        if s.auto_advance:
            candidates.append(s.auto_advance)
        candidates.extend(c.advances_to for c in s.continue_buttons)
        # prefer an unused edge to an unvisited slide, then any unused edge
        nxt = next((t for t in candidates
                    if t and (cur, t) not in seen_edges and t not in visited),
                   None)
        if nxt is None:
            nxt = next((t for t in candidates
                        if t and (cur, t) not in seen_edges), None)
        if nxt is None:
            if any(candidates):
                notes.append(f"route ends at {s.lms_id} ({s.title!r}): "
                             f"all forward edges already taken")
            else:
                notes.append(f"route ends at {s.lms_id} ({s.title!r}): "
                             f"no forward control")
            break
        seen_edges.add((cur, nxt))
        cur = nxt
    else:
        notes.append("route build hit 600-step bound — truncated")
    return route, notes


# ---------------------------------------------------------------- build

def build_runtime_model(zip_path: str) -> RuntimeModel:
    data = parse_scorm_zip(zip_path)
    am = build_automation_model(zip_path, data)
    rm = RuntimeModel(
        course_title=data.course_title,
        scorm_version=data.scorm_version,
        entry_slide=am.entry_slide,
        automodel=am.to_dict(),
    )
    for sc in data.scoring_configs or []:
        if sc.get("type") == "quiz":
            rm.pass_percent = float(sc.get("passPercent", 80))
            rm.pass_status = sc.get("passStatus", "pass")
            rm.fail_status = sc.get("failStatus", "incomplete")
            rm.slides_viewed_mode = sc.get("slidesViewedMode", "")
            rm.view_threshold = int(sc.get("viewThreshold", 0) or 0)

    zf = zipfile.ZipFile(zip_path)
    slide_info = {s.slide_id: s for s in data.slides}
    am_by_id = {s.slide_id: s for s in am.slides}

    for s in data.slides:
        try:
            raw = _parse_js_data(zf.read(s.html5url), "slide")
        except Exception as e:                                   # noqa: BLE001
            rm.build_warnings.append(f"slide {s.lms_id}: cannot re-parse JS ({e})")
            raw = None
        effects, layers = _extract_slide_effects(raw) if raw else ({}, {})
        ams = am_by_id.get(s.slide_id)
        rm.slides[s.slide_id] = asdict(RuntimeSlide(
            slide_id=s.slide_id,
            lms_id=s.lms_id,
            title=s.slide_title,
            duration_ms=s.duration_ms or 0,
            effects={k: asdict(v) for k, v in effects.items()},
            layers=[asdict(v) for v in layers.values()],
            text_entry=bool(ams and "text_entry" in ams.flags),
        ))

    # matching (drag-drop) answer keys from the raw interaction definitions:
    # answers[status=correct].evaluate.statements[] pairs choice_<dragObj> with
    # statement_<dropObj> — both strip-prefix to slide object ids
    def _matching_keys(scenes_raw):
        keys = {}

        def walk(o):
            if isinstance(o, dict):
                if o.get("kind") == "interaction" and o.get("type") == "matching":
                    pairs, drops = [], []
                    stmt_text = {st.get("id", ""): st.get("lmstext", "")
                                 for st in o.get("statements", [])}
                    ch_text = {c.get("id", ""): c.get("lmstext", "")
                               for c in o.get("choices", [])}
                    for st in o.get("statements", []):
                        sid_ = st.get("id", "")
                        if sid_.startswith("statement_"):
                            drops.append(sid_[len("statement_"):])
                    for a in o.get("answers", []):
                        if a.get("status") != "correct":
                            continue
                        for pr in (a.get("evaluate") or {}).get("statements", []):
                            cid = (pr.get("choiceid") or "").split(".")[-1]
                            stid = (pr.get("statementid") or "").split(".")[-1]
                            if cid.startswith("choice_") and stid.startswith("statement_"):
                                pairs.append({
                                    "drag_obj": cid[len("choice_"):],
                                    "drop_obj": stid[len("statement_"):],
                                    "drag_text": ch_text.get(cid, ""),
                                    "drop_text": stmt_text.get(stid, ""),
                                })
                    keys[o.get("id", "")] = {"pairs": pairs, "drop_objs": drops}
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)

        walk(scenes_raw)
        return keys

    matching_keys = _matching_keys(data.scenes_raw or [])

    # graded-question inventory (denominator M) with choice bindings
    for s in data.slides:
        ams = am_by_id.get(s.slide_id)
        submit = ""
        if ams:
            for c in ams.clickables:
                if (c.name or "").strip().lower() == "submit":
                    submit = c.obj_id
        slide_fx = rm.slides.get(s.slide_id, {}).get("effects", {})
        for ia in s.interactions:
            bindings, all_bound = [], True
            for ch in ia.choices:
                oid = ch.id[len("choice_"):] if ch.id.startswith("choice_") else ""
                bound = bool(oid) and (oid in slide_fx or _obj_exists(zf, s, oid))
                if not bound:
                    all_bound = False
                    rm.unbound_choices.append({"slide": s.lms_id, "choice": ch.id})
                bindings.append(asdict(ChoiceBinding(
                    choice_id=ch.id, obj_id=oid, text=ch.text, bound=bound)))
            mk = matching_keys.get(ia.id, {}) if ia.question_type == "matching" else {}
            drivable, reason = True, ""
            if ia.is_survey:
                drivable, reason = False, "survey (ungraded)"
            elif ia.question_type in ("fillin", "essay", "shortanswer", "text"):
                drivable, reason = False, "text-entry question (human pass)"
            elif ia.question_type == "matching":
                if not mk.get("pairs"):
                    drivable, reason = False, "matching question without extractable pairs"
            elif not all_bound:
                drivable, reason = False, "one or more choices unbound to objects"
            elif not submit:
                drivable, reason = False, "no submit control identified"
            rm.questions.append(asdict(QuestionEntry(
                interaction_id=ia.id, lms_id=ia.lms_id, slide_id=s.slide_id,
                qtype=ia.question_type, is_survey=ia.is_survey,
                drivable=drivable, not_drivable_reason=reason,
                choices=bindings, correct_choice_ids=list(ia.correct_choice_ids),
                submit_obj=submit,
                pairs=mk.get("pairs", []), drop_objs=mk.get("drop_objs", []))))

    # flattened menu outline in DOM order (scene headers included) — the
    # deterministic index base for unlocked-menu navigation
    def flatten_nav(links, out):
        for l in links or []:
            sid = ""
            raw = l.get("slideid", "")
            if raw.startswith("_player."):
                parts = raw.split(".")
                sid = parts[-1] if len(parts) >= 2 else ""
            out.append({"text": (l.get("displaytext") or "").strip(), "slide_id": sid})
            flatten_nav(l.get("links"), out)

    menu_items: list = []
    flatten_nav(data.nav_outline or [], menu_items)
    rm.menu_items = menu_items

    route, notes = _default_route(am)
    rm.routes["forward"] = route
    rm.route_notes["forward"] = "; ".join(notes) or "clean entry->terminal"

    # branch routes: any slide with >1 forward continue creates an alternative
    for s in am.slides:
        fwd = [c for c in s.continue_buttons]
        if len(fwd) > 1:
            for c in fwd[1:]:
                rm.route_notes[f"branch@{s.lms_id}:{c.obj_id}"] = (
                    f"alternative branch at {s.title!r} via {c.name!r} -> {c.advances_to}")
    return rm


_obj_cache: dict = {}


def _obj_exists(zf, slide, oid: str) -> bool:
    key = (zf.filename, slide.slide_id)
    if key not in _obj_cache:
        try:
            raw = _parse_js_data(zf.read(slide.html5url), "slide")
            _obj_cache[key] = json.dumps(raw)
        except Exception:                                        # noqa: BLE001
            _obj_cache[key] = ""
    return f'"{oid}"' in _obj_cache[key]


if __name__ == "__main__":
    import sys
    rm = build_runtime_model(sys.argv[1])
    out = sys.argv[1] + ".runtime_model.json"
    with open(out, "w") as fh:
        fh.write(rm.to_json())
    drivable = sum(1 for q in rm.questions if q["drivable"] and not q["is_survey"])
    graded = sum(1 for q in rm.questions if not q["is_survey"])
    print(f"{rm.course_title}: {len(rm.slides)} slides, "
          f"{graded} graded questions ({drivable} drivable), "
          f"route len {len(rm.routes['forward'])}, "
          f"unbound choices: {len(rm.unbound_choices)}")
    print("route note:", rm.route_notes["forward"])
    print("wrote", out)
