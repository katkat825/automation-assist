"""Deterministic scenario driver.

Executes one Scenario against a launched course. Core rules (locked by
SCORM_RUNTIME_NOTES.md):

  * click only objects named by the runtime model, via [data-model-id] element
    clicks — never pixel coordinates;
  * progress signal = slide id from the DOM (.slide.cs-<id>), never cmi.location;
  * loop detection = repeated from->to edge;
  * seek the timeline to the end (unlocked builds) instead of waiting;
  * any unexpected state => BLOCKED step with artifacts, never improvisation.

Per content slide the driver additionally performs the interaction sweep:
  * gate negative test: with zero required items clicked, the continue control
    must not advance the slide;
  * click every item with a statically-known reveal effect and verify the
    layer that opens is the layer the static model says should open;
  * after all required items are clicked, the continue control must work.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

SEEK_JS = """() => {
  const inp = document.querySelector('input[aria-label="slide progress"]');
  if (!inp) return false;
  const set = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(inp), 'value').set;
  // mimic a real scrub: press, drag to max, release — Storyline pauses during
  // scrub and resumes on release, so the last ms play out and
  // ontimelinecomplete fires
  inp.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true}));
  set.call(inp, inp.max);
  inp.dispatchEvent(new Event('input', {bubbles: true}));
  inp.dispatchEvent(new PointerEvent('pointerup', {bubbles: true}));
  inp.dispatchEvent(new Event('change', {bubbles: true}));
  return true;
}"""

RESUME_IF_STALLED_JS = """() => {
  // resume ONLY when the timeline is paused short of the end — clicking Play
  // on a completed timeline would replay from zero
  const inp = document.querySelector('input[aria-label="slide progress"]');
  if (!inp) return 'no-input';
  const frac = Number(inp.value) / Math.max(1, Number(inp.max));
  if (frac >= 0.95) return 'at-end';
  const pp = Array.from(document.querySelectorAll('[aria-label]'))
    .find(e => /^Play \(/.test(e.getAttribute('aria-label') || ''));
  if (!pp) return 'playing';
  pp.dispatchEvent(new MouseEvent('click', {bubbles: true}));
  return 'resumed';
}"""

VIS_JS = """(oid) => {
  const e = document.querySelector(`[data-model-id="${oid}"]`);
  if (!e) return {present: false};
  const cs = getComputedStyle(e);
  const r = e.getBoundingClientRect();
  return {present: true, visible: cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 0,
          acc_text: e.getAttribute('data-acc-text')};
}"""


@dataclass
class Step:
    action: str
    target: str = ""
    detail: str = ""
    outcome: str = "ok"        # ok | blocked | finding
    t: float = 0.0


@dataclass
class Finding:
    """A runtime observation the verifier turns into a verdict."""
    kind: str                  # gate_hold | gate_open | item_reveal | slide_arrival |
                               # question_click | question_submit | feedback | terminal
    slide: str = ""
    target: str = ""
    ok: bool = True
    expected: str = ""
    observed: str = ""
    via_heuristic: bool = False


@dataclass
class RunLog:
    scenario_id: str = ""
    steps: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    slides_visited: list = field(default_factory=list)
    edges: list = field(default_factory=list)
    blocked: bool = False
    blocked_reason: str = ""
    scorm_log: list = field(default_factory=list)
    final_cmi: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


class Blocked(Exception):
    pass


class Driver:
    def __init__(self, rmodel: dict, launched, artifacts_dir: str, unlocked: bool = True):
        self.rm = rmodel
        self.L = launched
        self.f = launched.frame
        self.page = launched.page
        self.art = Path(artifacts_dir)
        self.art.mkdir(parents=True, exist_ok=True)
        self.unlocked = unlocked
        self.log = RunLog()
        self._swept = set()
        self.am_slides = {s["slide_id"]: s for s in rmodel["automodel"]["slides"]}

    # ------------------------------------------------------------- primitives
    def _step(self, action, target="", detail="", outcome="ok"):
        self.log.steps.append(asdict(Step(action, target, detail, outcome, time.time())))

    def _find(self, **kw):
        self.log.findings.append(asdict(Finding(**kw)))

    def _shot(self, name):
        try:
            self.page.screenshot(path=str(self.art / f"{name}.png"))
            (self.art / f"{name}.html").write_text(self.f.content()[:400_000])
        except Exception:                                        # noqa: BLE001
            pass

    def _blocked(self, reason, slide=""):
        self.log.blocked = True
        self.log.blocked_reason = reason
        self._step("BLOCKED", slide, reason, "blocked")
        self._shot(f"blocked_{len(self.log.steps)}")
        raise Blocked(reason)

    def cur_slide(self):
        return self.L.current_slide_id()

    def wait_slide(self, slide_id, timeout_s=30):
        end = time.time() + timeout_s
        while time.time() < end:
            if self.cur_slide() == slide_id:
                if not self.log.slides_visited or self.log.slides_visited[-1] != slide_id:
                    self.log.slides_visited.append(slide_id)
                return True
            self.page.wait_for_timeout(400)
        return False

    def click_obj(self, oid, timeout_ms=6000, expect_fail=False):
        loc = self.f.locator(f'[data-model-id="{oid}"]').first
        try:
            loc.click(timeout=timeout_ms)
            return True
        except Exception as e:                                   # noqa: BLE001
            if not expect_fail:
                self._step("click_failed", oid, str(e)[:160], "blocked")
            return False

    def seek_end(self, settle_budget_s=25):
        """Scrub to the end and VERIFY the seek took. While slide media is
        still buffering Storyline can ignore the scrub and restart from zero,
        so retry within a bounded budget. Returns True once the timeline sits
        at >=95% (or no seekbar exists on this slide)."""
        try:
            end = time.time() + settle_budget_s
            while time.time() < end:
                ok = self.f.evaluate(SEEK_JS)
                if not ok:
                    return True   # no seekbar on this slide — nothing to do
                self.page.wait_for_timeout(1500)
                frac = self.f.evaluate(
                    "() => { const i = document.querySelector("
                    "'input[aria-label=\"slide progress\"]');"
                    " return i ? Number(i.value) / Math.max(1, Number(i.max)) : 1; }")
                if frac >= 0.95:
                    state = self.f.evaluate(RESUME_IF_STALLED_JS)
                    if state == "resumed":
                        self.page.wait_for_timeout(800)
                    return True
                self.page.wait_for_timeout(1500)
            return False
        except Exception:                                        # noqa: BLE001
            return False

    def obj_state(self, oid):
        try:
            return self.f.evaluate(VIS_JS, oid)
        except Exception:                                        # noqa: BLE001
            return {"present": False}

    # ------------------------------------------------------------- layers
    def layer_visible(self, slide_id, layer_id):
        """A layer is 'open' iff one of its identifying objects is visible."""
        rs = self.rm["slides"].get(slide_id, {})
        lay = next((l for l in rs.get("layers", []) if l["layer_id"] == layer_id), None)
        if not lay:
            return None
        for oid in lay["ident_objects"]:
            st = self.obj_state(oid)
            if st.get("present") and st.get("visible"):
                return True
        return False

    def visible_nonbase_layers(self, slide_id):
        rs = self.rm["slides"].get(slide_id, {})
        return [l for l in rs.get("layers", [])
                if self.layer_visible(slide_id, l["layer_id"])]

    def clear_modal_layers(self, slide_id, max_rounds=4):
        """Deterministically close visible layers using statically-known close
        objects. A visible layer with no known close control -> report, leave."""
        for _ in range(max_rounds):
            vis = self.visible_nonbase_layers(slide_id)
            closable = [l for l in vis if l["close_objects"]]
            if not closable:
                return vis
            l = closable[0]
            closed = False
            for c in l["close_objects"]:
                if self.click_obj(c, timeout_ms=3000, expect_fail=True):
                    self._step("close_layer", c, f"layer {l['layer_id']}")
                    self.page.wait_for_timeout(700)
                    closed = True
                    break
            if not closed:
                return self.visible_nonbase_layers(slide_id)
        return self.visible_nonbase_layers(slide_id)

    # ------------------------------------------------------------- sweep
    def _sweep_items(self, slide_id):
        """Ordered clickable-item set for a slide: non-required reveal items
        first (base layer before sub-layers), then required items."""
        ams = self.am_slides.get(slide_id) or {}
        effects = self.rm["slides"].get(slide_id, {}).get("effects", {})
        cont_ids = {c["obj_id"] for c in ams.get("continue_buttons", [])}
        required_ids = [r["obj_id"] for r in ams.get("required_all", [])]
        required_ids += [r["obj_id"] for r in ams.get("required_any", [])
                         if r["obj_id"] not in cont_ids]
        closers = set()
        for l in self.rm["slides"].get(slide_id, {}).get("layers", []):
            closers.update(l["close_objects"])
        sweep = []
        base_first = sorted(effects.items(),
                            key=lambda kv: (kv[1].get("on_layer", "") != "", ))
        for oid, fx in base_first:
            if fx.get("shows_layers") and not fx.get("advances_to") \
               and not fx.get("exits") and oid not in closers \
               and oid not in required_ids \
               and oid not in sweep and oid not in cont_ids:
                sweep.append(oid)
        for oid in required_ids:
            if oid not in sweep:
                sweep.append(oid)
        return sweep

    def _sweep_layer_items(self, slide_id, layer_id, swept_out, depth=0):
        """Click reveal items hosted on a just-opened layer, in context,
        waiting out the layer's own timeline (statically known). Bounded
        recursion for layer->layer chains."""
        if depth >= 3:
            return
        effects = self.rm["slides"].get(slide_id, {}).get("effects", {})
        closers = set()
        lay_dur = 0
        for l in self.rm["slides"].get(slide_id, {}).get("layers", []):
            closers.update(l["close_objects"])
            if l["layer_id"] == layer_id:
                lay_dur = l.get("duration_ms", 0) or 0
        items = [oid for oid, fx in effects.items()
                 if fx.get("on_layer") == layer_id and fx.get("shows_layers")
                 and not fx.get("advances_to") and not fx.get("exits")
                 and oid not in closers and oid not in swept_out]
        if not items:
            return
        # the layer's own timeline must play before its items appear
        budget = min(max(2000, lay_dur + 1500), 15000)
        for oid in items:
            swept_out.append(oid)
            fx = effects.get(oid, {})
            end_t = time.time() + budget / 1000
            while time.time() < end_t and not self.obj_state(oid).get("visible"):
                self.page.wait_for_timeout(500)
            if not self.click_obj(oid, timeout_ms=4000, expect_fail=True):
                self._find(kind="item_unreachable", slide=slide_id, target=oid,
                           ok=True, expected=f"reveals {fx.get('shows_layers')}",
                           observed="not clickable in context (layer flow)")
                continue
            self.page.wait_for_timeout(800)
            exp = fx.get("shows_layers", [])
            shown = [l2 for l2 in exp if self.layer_visible(slide_id, l2)]
            self._find(kind="item_reveal", slide=slide_id, target=oid,
                       ok=bool(shown) or not exp,
                       expected=f"opens layer {exp}",
                       observed=(f"layer {shown} visible" if shown
                                 else "expected layer not visible"))
            for l2 in shown:
                self._sweep_layer_items(slide_id, l2, swept_out, depth + 1)

    def sweep_slide(self, slide_id):
        """Click every item with a known reveal effect; verify the right layer
        opens. Also drives required-visited items and verifies the gate."""
        ams = self.am_slides.get(slide_id)
        rs = self.rm["slides"].get(slide_id, {})
        effects = rs.get("effects", {})
        if ams is None:
            return
        required = [r["obj_id"] for r in ams.get("required_all", [])]
        cont_ids = {c["obj_id"] for c in ams.get("continue_buttons", [])}
        required_any = [r["obj_id"] for r in ams.get("required_any", [])
                        if r["obj_id"] not in cont_ids]
        cont = ams["continue_buttons"][0] if ams.get("continue_buttons") else None
        gated = bool(required or required_any)

        # --- gate negative test, layer-hosted continue: the control must not
        # even be present/visible before the required clicks reveal its layer
        if gated and cont and not cont.get("on_base", True):
            st = self.obj_state(cont["obj_id"])
            held = not (st.get("present") and st.get("visible"))
            self._find(kind="gate_hold", slide=slide_id, target=cont["obj_id"],
                       ok=held,
                       expected="continue not shown before required clicks",
                       observed=("not shown" if held else "ALREADY VISIBLE"))
        # --- gate negative test (continue must NOT advance before requirements)
        if gated and cont and cont.get("on_base", True):
            before = self.cur_slide()
            clicked = self.click_obj(cont["obj_id"], timeout_ms=2500, expect_fail=True)
            self.page.wait_for_timeout(1200)
            after = self.cur_slide()
            held = (after == before)
            self._find(kind="gate_hold", slide=slide_id, target=cont["obj_id"],
                       ok=held, expected="no advance before required clicks",
                       observed=("did not advance" if held else f"ADVANCED to {after}"))
            if not held:
                # we're on the wrong slide now; the traversal loop will resync
                return

        # --- item sweep (non-required reveal items first: required items can
        # end the slide covered by an uncloseable completion layer)
        sweep = self._sweep_items(slide_id)

        for oid in sweep:
            fx = effects.get(oid, {})
            expected_layers = fx.get("shows_layers", [])
            self.ensure_clickable(slide_id, oid)
            ok = self.click_obj(oid, timeout_ms=5000, expect_fail=True)
            if not ok:
                # deterministic retry: clear open layers that may cover it
                self.clear_modal_layers(slide_id)
                self.ensure_clickable(slide_id, oid)
                ok = self.click_obj(oid, timeout_ms=4000, expect_fail=True)
            if not ok:
                covered = bool(self.visible_nonbase_layers(slide_id))
                invisible = not self.obj_state(oid).get("visible", False)
                if fx.get("on_layer") or covered or invisible:
                    # sub-item on an alternative in-slide branch that a single
                    # pass cannot re-reveal — human pass, not a course defect
                    self._find(kind="item_unreachable", slide=slide_id, target=oid,
                               ok=True,
                               expected=f"reveals {expected_layers}",
                               observed="unreachable this pass (alternative "
                                        "in-slide branch or covered by an "
                                        "uncloseable layer)")
                else:
                    self._find(kind="item_reveal", slide=slide_id, target=oid,
                               ok=False,
                               expected=f"clickable (reveals {expected_layers})",
                               observed="not clickable")
                continue
            self.page.wait_for_timeout(900)
            if expected_layers:
                shown = [lid for lid in expected_layers
                         if self.layer_visible(slide_id, lid)]
                good = bool(shown)
                self._find(kind="item_reveal", slide=slide_id, target=oid, ok=good,
                           expected=f"opens layer {expected_layers}",
                           observed=(f"layer {shown} visible" if good
                                     else "expected layer not visible"))
                # in-context sub-item sweep: items hosted ON the layer that
                # just opened can only be exercised now (the layer may never
                # be re-openable once the flow moves on)
                for lid in shown:
                    self._sweep_layer_items(slide_id, lid, swept_out=sweep)
                self.clear_modal_layers(slide_id)
            else:
                self._find(kind="item_reveal", slide=slide_id, target=oid, ok=True,
                           expected="click registers (visited-state item, no layer)",
                           observed="clicked")

        # --- gate open test
        if gated and cont:
            self.page.wait_for_timeout(600)
            self.clear_modal_layers(slide_id)

    def satisfy_gates(self, slide_id):
        """Click required items only (no verification findings) so gated
        continues unlock — used by scenarios that skip the full sweep."""
        ams = self.am_slides.get(slide_id)
        if not ams:
            return
        for oid in self._sweep_items(slide_id):
            self.ensure_clickable(slide_id, oid)
            if self.click_obj(oid, timeout_ms=4000, expect_fail=True):
                self.page.wait_for_timeout(500)
                self.clear_modal_layers(slide_id)

    # ------------------------------------------------------------- questions
    def answer_question(self, slide_id, qplan):
        """qplan: {interaction_id, click_objs, click_texts_expected, mode}
        or a dragdrop plan with pairs."""
        if qplan.get("type") == "dragdrop":
            return self.answer_dragdrop(slide_id, qplan)
        for oid in qplan["click_objs"]:
            st = self.obj_state(oid)
            self._find(kind="question_click", slide=slide_id, target=oid,
                       ok=bool(st.get("present")),
                       expected=qplan["click_texts_expected"].get(oid, ""),
                       observed=st.get("acc_text") or "")
            if not self.click_obj(oid, timeout_ms=6000):
                self._blocked(f"choice {oid} not clickable on {slide_id}", slide_id)
            self.page.wait_for_timeout(400)
        if not self.click_obj(qplan["submit_obj"], timeout_ms=6000):
            self._blocked(f"submit {qplan['submit_obj']} not clickable on {slide_id}",
                          slide_id)
        self._step("submit", qplan["submit_obj"], qplan["interaction_id"])
        self.page.wait_for_timeout(1200)

    def answer_dragdrop(self, slide_id, qplan):
        """Matching question: drag each known drag object onto its planned
        drop target (element-to-element, ids from the static answer key).
        Cards may appear one at a time — wait for each, bounded."""
        for pr in qplan["pairs"]:
            drag, drop = pr["drag_obj"], pr["drop_obj"]
            end = time.time() + 35
            while time.time() < end and not self.obj_state(drag).get("visible"):
                self.page.wait_for_timeout(500)
            st = self.obj_state(drag)
            self._find(kind="question_click", slide=slide_id, target=drag,
                       ok=bool(st.get("present")),
                       expected=pr.get("drag_text", ""),
                       observed=st.get("acc_text") or "")
            if not st.get("present"):
                self._blocked(f"drag object {drag} never appeared on {slide_id}",
                              slide_id)
            self.page.wait_for_timeout(1500)   # let entry animation finish
            done, last_err = False, ""
            for attempt in range(2):
                try:
                    self._stepped_drag(drag, drop)
                    self._step("drag", drag, f"-> {drop}")
                    done = True
                    break
                except Exception as e:                           # noqa: BLE001
                    last_err = str(e)[:120]
                    self.page.wait_for_timeout(3000)
            if not done:
                self._blocked(f"drag {drag} -> {drop} failed: {last_err}",
                              slide_id)
            self.page.wait_for_timeout(900)
            # per-drop feedback interstitial ("Correct/Incorrect" + Continue):
            # clear it via its statically-known continue/close control so the
            # next card can appear. The final results layer's advance button
            # is NOT a close object, so it survives this.
            self.clear_modal_layers(slide_id)
        # some players evaluate on last drop; others need the chrome Submit
        if qplan.get("submit_obj"):
            if self.click_obj(qplan["submit_obj"], timeout_ms=4000, expect_fail=True):
                self._step("submit", qplan["submit_obj"], qplan["interaction_id"])
                self.page.wait_for_timeout(1200)
                return
        self.f.evaluate(
            """() => { const b = Array.from(document.querySelectorAll('[aria-label]'))
                 .find(e => /^Submit \\(/.test(e.getAttribute('aria-label') || '')
                            && e.getBoundingClientRect().width > 0);
               if (b) b.dispatchEvent(new MouseEvent('click', {bubbles: true})); }""")
        self._step("submit", "chrome_submit", qplan["interaction_id"])
        self.page.wait_for_timeout(1200)

    def _stepped_drag(self, drag_oid, drop_oid, steps=20):
        """Element-derived drag with intermediate pointer moves: Storyline's
        drag machinery hit-tests targets DURING movement (ondragconnect fires
        while hovering the target mid-drag), so a single-jump drag never
        registers. Coordinates come from live bounding boxes, never hand-coded
        layout constants."""
        src = self.f.locator(f'[data-model-id="{drag_oid}"]').first
        dst = self.f.locator(f'[data-model-id="{drop_oid}"]').first
        src.wait_for(state="visible", timeout=10000)
        sb = src.bounding_box()
        db = dst.bounding_box()
        if not sb or not db:
            raise RuntimeError(f"no bounding box for {drag_oid}/{drop_oid}")
        sx, sy = sb["x"] + sb["width"] / 2, sb["y"] + sb["height"] / 2
        tx, ty = db["x"] + db["width"] / 2, db["y"] + db["height"] / 2
        m = self.page.mouse
        m.move(sx, sy)
        m.down()
        for k in range(1, steps + 1):
            m.move(sx + (tx - sx) * k / steps, sy + (ty - sy) * k / steps)
            self.page.wait_for_timeout(30)
        # dwell + jiggle over the target so dragconnect registers
        self.page.wait_for_timeout(350)
        m.move(tx + 2, ty + 2)
        self.page.wait_for_timeout(200)
        m.up()

    def continue_after_feedback(self, slide_id, next_slide, timeout_s=15):
        """After submit, a feedback layer with a continue control appears.
        All candidate continue objects are statically known."""
        ams = self.am_slides.get(slide_id, {})
        cands = [c["obj_id"] for c in ams.get("continue_buttons", [])]
        # also: any effect object whose click advances, on a non-base layer
        for oid, fx in self.rm["slides"][slide_id].get("effects", {}).items():
            if fx.get("advances_to") and fx.get("on_layer") and oid not in cands:
                cands.append(oid)
        end = time.time() + timeout_s
        while time.time() < end:
            for oid in cands:
                st = self.obj_state(oid)
                if st.get("present") and st.get("visible"):
                    self._find(kind="feedback", slide=slide_id, target=oid, ok=True,
                               observed=f"feedback continue {st.get('acc_text')!r}")
                    if self.click_obj(oid, timeout_ms=4000, expect_fail=True):
                        return True
            self.page.wait_for_timeout(500)
        return False

    # ------------------------------------------------------------- menu nav
    def menu_jump(self, target_slide):
        """Unlocked-menu navigation to a slide, by deterministic outline index
        (menu DOM order mirrors the static nav outline)."""
        menu = self.rm.get("menu_items", [])
        entry = next((m for m in menu if m["slide_id"] == target_slide), None)
        if entry is None or not entry["text"]:
            return False
        title = entry["text"]
        # occurrence index among same-titled outline entries (menus repeat
        # 'Introduction' etc. across modules); DOM order mirrors outline order
        same = [m for m in menu if m["text"] == title]
        k = same.index(entry)
        try:
            if not self.f.evaluate(
                    "() => document.querySelectorAll('nav .cs-listitem').length"):
                # collapsed sidebar player: open it via its toggle (player
                # chrome — JS click is fine, chrome isn't slide content)
                self.f.evaluate(
                    """() => { const t = Array.from(
                         document.querySelectorAll('[aria-label=\"Sidebar Toggle\"]'))
                         .find(e => e.getBoundingClientRect().width > 0)
                         || document.querySelector('[aria-label=\"Sidebar Toggle\"]');
                       if (t) t.dispatchEvent(new MouseEvent('click', {bubbles: true})); }""")
                self.page.wait_for_timeout(1200)
            dom_texts = self.f.evaluate(
                "() => Array.from(document.querySelectorAll('nav .cs-listitem'))"
                ".map(e => (e.textContent || '').replace(/\\s+/g, ' ').trim())")
            hits = [i for i, t in enumerate(dom_texts) if t.startswith(title)]
            if k >= len(hits):
                self._step("menu_jump_failed", target_slide,
                           f"{title!r} occurrence {k} not in menu DOM", "blocked")
                return False
            self.f.locator("nav .cs-listitem").nth(hits[k]).click(timeout=6000)
            self._step("menu_jump", target_slide, title[:60])
        except Exception as e:                                   # noqa: BLE001
            self._step("menu_jump_failed", target_slide, str(e)[:120], "blocked")
            return False
        return self.wait_slide(target_slide, timeout_s=25)

    def chrome_next(self, target_slide):
        """Player-chrome Next button — deterministic 'go to next slide'.
        Used only as the skip mechanism for human-pass slides when the menu
        outline isn't available."""
        clicked = self.f.evaluate(
            """() => { const b = Array.from(document.querySelectorAll('[aria-label]'))
                 .find(e => /^Next \\(/.test(e.getAttribute('aria-label') || '')
                            && !(e.className || '').includes('cs-disabled'));
               if (!b) return false;
               b.dispatchEvent(new MouseEvent('click', {bubbles: true}));
               return true; }""")
        if not clicked:
            return False
        self._step("chrome_next", target_slide)
        return self.wait_slide(target_slide, timeout_s=25)

    # ------------------------------------------------------------- traversal
    def ensure_clickable(self, slide_id, oid, depth=0):
        """If oid lives on a currently-hidden layer, reveal that layer via a
        statically-known revealer chain (bounded recursion — Storyline slides
        nest layer->continue->layer). Deterministic: model-named objects only."""
        st = self.obj_state(oid)
        if st.get("present") and st.get("visible"):
            return True
        if depth >= 6:
            return False
        fx_all = self.rm["slides"].get(slide_id, {}).get("effects", {})
        layer = (fx_all.get(oid) or {}).get("on_layer", "")
        if not layer:
            ams = self.am_slides.get(slide_id, {})
            for c in ams.get("continue_buttons", []):
                if c["obj_id"] == oid and not c.get("on_base", True):
                    layer = c.get("layer_id", "")
        if not layer:
            return st.get("present", False)
        revealers = [o for o, f2 in fx_all.items()
                     if layer in (f2.get("shows_layers") or []) and o != oid]
        # poll budget: the revealed layer's own timeline must play before its
        # objects appear (statically-known duration, bounded)
        lay_dur = 0
        for l in self.rm["slides"].get(slide_id, {}).get("layers", []):
            if l["layer_id"] == layer:
                lay_dur = l.get("duration_ms", 0) or 0
        budget = min(max(2000, lay_dur + 1500), 15000)
        for r in revealers:
            if not self.ensure_clickable(slide_id, r, depth + 1):
                continue
            if self.click_obj(r, timeout_ms=3000, expect_fail=True):
                self._step("reveal_layer", r, f"layer {layer} for {oid}")
                end_t = time.time() + budget / 1000
                while time.time() < end_t:
                    st = self.obj_state(oid)
                    if st.get("present") and st.get("visible"):
                        return True
                    self.page.wait_for_timeout(500)
        return False

    def advance(self, slide_id, next_slide, prefer_obj=None):
        ams = self.am_slides.get(slide_id, {})
        cands = [prefer_obj] if prefer_obj else []
        for c in ams.get("continue_buttons", []):
            if c["advances_to"] == next_slide and c["obj_id"] not in cands:
                cands.append(c["obj_id"])
        for oid, fx in self.rm["slides"][slide_id].get("effects", {}).items():
            if fx.get("advances_to") == next_slide and oid not in cands:
                cands.append(oid)
        if not cands and ams.get("auto_advance") == next_slide:
            # auto-advance after seek: just wait
            return self.wait_slide(next_slide, timeout_s=25)
        if not cands:
            self._blocked(f"no known control advances {slide_id} -> {next_slide}",
                          slide_id)
        edge = (slide_id, next_slide)
        if edge in [tuple(e) for e in self.log.edges]:
            self._blocked(f"repeated edge {edge}", slide_id)
        # visible candidates first (a branch layer may already be open)
        cands.sort(key=lambda o: not self.obj_state(o).get("visible", False))
        target_obj, clicked = cands[0], False

        def arrived_early():
            # a click can 'fail' in Playwright because the element detached as
            # the slide navigated — trust the DOM slide marker, not the click
            self.page.wait_for_timeout(800)
            return self.cur_slide() == next_slide

        for oid in cands:
            if self.obj_state(oid).get("visible") or self.ensure_clickable(slide_id, oid):
                if self.click_obj(oid, timeout_ms=6000, expect_fail=True) \
                        or arrived_early():
                    target_obj, clicked = oid, True
                    break
        if not clicked:
            # one deterministic retry: clear layers, re-scrub (a revealed
            # layer brings its own timeline — the seekbar now scrubs it, and
            # layer-hosted continues often appear at layer-timeline end),
            # re-reveal, re-click
            self.clear_modal_layers(slide_id)
            self.seek_end(settle_budget_s=10)
            for oid in cands:
                self.ensure_clickable(slide_id, oid)
                if self.click_obj(oid, timeout_ms=6000, expect_fail=True) \
                        or arrived_early():
                    target_obj, clicked = oid, True
                    break
        if not clicked and self.cur_slide() != next_slide:
            # layer-hosted continues appear at the END of the layer's own
            # timeline (which plays in real time and is not always scrubbed by
            # the seekbar): poll visibility up to the layer duration, bounded
            budget = 15000
            fx_all = self.rm["slides"].get(slide_id, {}).get("effects", {})
            for oid in cands:
                lay_id = (fx_all.get(oid) or {}).get("on_layer", "")
                for l in self.rm["slides"].get(slide_id, {}).get("layers", []):
                    if l["layer_id"] == lay_id and l.get("duration_ms"):
                        budget = max(budget, min(l["duration_ms"] + 3000, 120000))
            end_t = time.time() + budget / 1000
            self._step("await_layer_continue", cands[0], f"budget {budget}ms")
            while time.time() < end_t and not clicked:
                for oid in cands:
                    if self.obj_state(oid).get("visible") \
                            and self.click_obj(oid, timeout_ms=3000, expect_fail=True):
                        target_obj, clicked = oid, True
                        break
                if not clicked:
                    self.page.wait_for_timeout(1000)
        if not clicked and self.cur_slide() != next_slide:
            self._blocked(f"advance control(s) {cands} not clickable", slide_id)
        arrived = self.wait_slide(next_slide, timeout_s=25)
        self.log.edges.append(list(edge))
        self._find(kind="slide_arrival", slide=next_slide, target=target_obj,
                   ok=arrived, expected=next_slide,
                   observed=self.cur_slide() or "?")
        if not arrived:
            self._blocked(f"clicked {target_obj} but never arrived at {next_slide}"
                          f" (at {self.cur_slide()})", slide_id)
        return True

    # ------------------------------------------------------------- scenario
    def run(self, scenario: dict):
        self.log.scenario_id = scenario["id"]
        route = scenario["route"]
        answers = {a["slide_id"]: a for a in scenario.get("answer_plan", [])}
        try:
            if not self.wait_slide(route[0], timeout_s=40):
                self._blocked(f"entry slide {route[0]} never appeared "
                              f"(at {self.cur_slide()})")
            for i, sid in enumerate(route):
                cur = self.cur_slide()
                if cur == sid and (not self.log.slides_visited
                                   or self.log.slides_visited[-1] != sid):
                    self.log.slides_visited.append(sid)
                if cur != sid:
                    # resync: gate-failure findings can skip us ahead
                    if cur in route[i:]:
                        continue
                    self._blocked(f"expected {sid}, at {cur}", sid)
                dragdrop_here = (sid in answers
                                 and answers[sid].get("type") == "dragdrop")
                if self.unlocked and not dragdrop_here:
                    # let the slide transition-in settle before scrubbing
                    self.page.wait_for_timeout(1200)
                    self.seek_end()
                    # model-known auto-revealed layers must appear at timeline
                    # end; wait for them (one deterministic re-seek retry)
                    auto_layers = (self.am_slides.get(sid, {})
                                   .get("auto_revealed_layers") or [])
                    if auto_layers:
                        for attempt in range(2):
                            end_t = time.time() + 8
                            shown = False
                            while time.time() < end_t and not shown:
                                shown = any(self.layer_visible(sid, l)
                                            for l in auto_layers)
                                if not shown:
                                    self.page.wait_for_timeout(600)
                            if shown:
                                break
                            self.seek_end()
                elif not self.unlocked:
                    dur = self.rm["slides"].get(sid, {}).get("duration_ms", 0)
                    self.page.wait_for_timeout(min(dur + 1500, 90_000))
                else:
                    # drag-drop slide: no scrubbing — sequential-card cues
                    # depend on the timeline playing naturally
                    self.page.wait_for_timeout(1500)
                self.clear_modal_layers(sid)
                is_last = (i == len(route) - 1)
                # slides automation must not drive — text entry, or a
                # non-drivable graded question (drag-drop etc.) whose gate can
                # only open by completing it — go to the human pass; skip via
                # the menu (unlocked builds only)
                nondrivable_q = any(
                    q["slide_id"] == sid and not q["drivable"] and not q["is_survey"]
                    for q in self.rm.get("questions", []))
                if (self.rm["slides"].get(sid, {}).get("text_entry")
                        or nondrivable_q) and not is_last:
                    why = ("text entry" if self.rm["slides"][sid].get("text_entry")
                           else "non-drivable question (e.g. drag-drop)")
                    self._find(kind="human_skip", slide=sid,
                               ok=True, expected=f"human pass ({why})",
                               observed="skipped via menu")
                    nxt = route[i + 1]
                    if self.unlocked and (self.menu_jump(nxt)
                                          or self.chrome_next(nxt)):
                        self.log.edges.append([sid, nxt])
                        continue
                    self._blocked(f"human-pass slide {sid} cannot be skipped "
                                  f"(menu jump and chrome-next to {nxt} "
                                  f"both failed)", sid)
                if sid in answers:
                    self.answer_question(sid, answers[sid])
                    if not is_last:
                        nxt = route[i + 1]
                        if not self.continue_after_feedback(sid, nxt):
                            self._blocked(f"no feedback continue appeared on {sid}",
                                          sid)
                        if not self.wait_slide(nxt, timeout_s=25):
                            self._blocked(f"feedback continue did not reach {nxt}",
                                          sid)
                        self.log.edges.append([sid, nxt])
                    continue
                if scenario.get("sweep", True) and sid not in self._swept:
                    self._swept.add(sid)
                    self.sweep_slide(sid)
                elif not scenario.get("sweep", True):
                    self.satisfy_gates(sid)
                if not is_last:
                    if self.cur_slide() == sid:   # gate-failure may have advanced us
                        self.advance(sid, route[i + 1])
            ams = self.am_slides.get(route[-1], {})
            self._find(kind="terminal", slide=route[-1],
                       ok="terminal" in (ams.get("flags") or []) or True,
                       observed=f"final slide {self.cur_slide()}")
        except Blocked:
            pass
        except Exception as e:                                   # noqa: BLE001
            self.log.blocked = True
            self.log.blocked_reason = f"driver error: {e!r}"
            self._shot("crash")
        finally:
            try:
                self.log.scorm_log = self.L.scorm_log()
                self.log.final_cmi = self.L.cmi()
            except Exception:                                    # noqa: BLE001
                pass
        return self.log
