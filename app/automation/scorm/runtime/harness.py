"""Launch harness: local server + recording SCORM stub + Playwright page.

Deterministic core. The stub lives on the launcher (parent) window; the course
discovers it via standard SCORM window-walking. No course files are modified.
"""
from __future__ import annotations
import functools, http.server, json, threading
from pathlib import Path

from .cancel import Cancelled, check, is_cancelled

STUB_JS = (Path(__file__).parent / "scorm_stub.js").read_text()

# --- companion side-panel assets (used only when companion data is passed) ---

_COMPANION_CSS = """
#cmp{width:400px;min-width:400px;height:100vh;overflow-y:auto;box-sizing:border-box;
 background:#f7f7f8;color:#1a1a1a;font:13px/1.45 system-ui,Segoe UI,Arial,sans-serif;
 border-left:2px solid #d0d0d5;padding:0}
#cmp-head{position:sticky;top:0;background:#26303b;color:#fff;padding:10px 12px;z-index:2}
.cmp-scr{font-size:18px;font-weight:700}
.cmp-sid{background:#3d556b;padding:1px 7px;border-radius:4px;font-size:14px;font-weight:600}
.cmp-idline{font-size:11px;color:#b7c4d1;margin-top:3px;font-family:ui-monospace,Consolas,monospace}
.cmp-title{font-size:13px;color:#e6ecf1;margin-top:4px}
#cmp-body{padding:8px 12px 40px}
.cmp-sec{font-weight:700;text-transform:uppercase;font-size:11px;letter-spacing:.04em;
 color:#555;margin:14px 0 6px;border-bottom:1px solid #ddd;padding-bottom:3px}
.cmp-none{color:#7a7a7a;font-style:italic;padding:2px 0}
.cmp-chk{padding:6px 8px;margin:5px 0;border-radius:4px;border-left:4px solid #bbb;background:#fff}
.cmp-chk.lvl-fail{border-left-color:#c0392b;background:#fdecea}
.cmp-chk.lvl-warn{border-left-color:#d68910;background:#fef5e7}
.cmp-chk.lvl-info{border-left-color:#5499c7;background:#eaf2f8}
.cmp-lvl{font-size:10px;font-weight:700;text-transform:uppercase;padding:0 4px;border-radius:3px;
 background:#eee;color:#333}
.cmp-secname{font-size:11px;color:#666}
.cmp-q{margin:8px 0;padding:7px 8px;background:#fff;border:1px solid #e3e3e6;border-radius:4px}
.cmp-qt{font-weight:600;margin-bottom:4px}
.cmp-qtype{font-weight:400;color:#888;font-size:11px}
.cmp-unv{color:#b9770e;font-weight:700;font-size:11px}
.cmp-ch{padding:2px 0 2px 4px;color:#333}
.cmp-ch.correct{color:#1e8449;font-weight:700}
.cmp-wait{color:#b7c4d1}
"""

_COMPANION_JS = r"""
(function(){
  var DATA = window.__companion || {slides:{}};
  var head = document.getElementById('cmp-head');
  var body = document.getElementById('cmp-body');
  var lastSid = null;
  function curSlide(){
    try{
      var fr = document.getElementById('course');
      var doc = fr && fr.contentDocument; if(!doc) return null;
      var all = Array.prototype.slice.call(doc.querySelectorAll('.slide[class*="cs-"]'));
      var vis = all.filter(function(s){
        var cs = doc.defaultView.getComputedStyle(s), r = s.getBoundingClientRect();
        return cs.display!=='none' && cs.visibility!=='hidden' && r.width>0;
      });
      if(!vis.length) return null;
      var m = vis[vis.length-1].className.match(/cs-([A-Za-z0-9_$]+)/);
      return m ? m[1] : null;
    }catch(e){ return null; }
  }
  function esc(t){ var d=document.createElement('div'); d.textContent = (t==null?'':String(t)); return d.innerHTML; }
  function render(sid){
    var s = DATA.slides[sid];
    if(!s){ head.innerHTML = '<div class="cmp-wait">Waiting for a screen&hellip;</div>'; body.innerHTML=''; return; }
    head.innerHTML =
      '<div class="cmp-scr">Screen '+esc(s.screen_number||'—')+'</div>'
      + '<div class="cmp-idline">'+esc(s.slide_id)
        + (s.path ? ' • '+esc(s.path)+' path' : '')
        + (s.acc_screen_number ? ' • acc '+esc(s.acc_screen_number) : '') + '</div>'
      + '<div class="cmp-title">'+esc(s.slide_title||'')+'</div>';
    var h = '<div class="cmp-sec">To check on this screen</div>';
    if(!s.checks || !s.checks.length){
      h += '<div class="cmp-none">No static-check findings for this screen.</div>';
    } else {
      s.checks.forEach(function(c){
        h += '<div class="cmp-chk lvl-'+esc(c.level)+'"><span class="cmp-lvl">'+esc(c.level)+'</span> '
           + '<span class="cmp-secname">'+esc(c.section)+'</span><br>'+esc(c.message)+'</div>';
      });
    }
    if(s.questions && s.questions.length){
      h += '<div class="cmp-sec">Questions &amp; answers</div>';
      s.questions.forEach(function(q){
        h += '<div class="cmp-q"><div class="cmp-qt">'+esc(q.question||'')
           + ' <span class="cmp-qtype">('+esc(q.type)+(q.is_survey?', survey':'')+')</span>'
           + (q.order_verified ? '' : ' <span class="cmp-unv">order unverified</span>') + '</div>';
        (q.choices||[]).forEach(function(ch){
          h += '<div class="cmp-ch'+(ch.correct?' correct':'')+'">'+(ch.correct?'✓':'○')+' '+esc(ch.text)+'</div>';
        });
        h += '</div>';
      });
    }
    body.innerHTML = h;
  }
  function tick(){ var sid = curSlide(); if(sid && sid!==lastSid){ lastSid = sid; render(sid); } }
  setInterval(tick, 400); tick();
})();
"""


# --- opt-in screenshot capture (course print) --------------------------------
# A floating button + a queue the observe loop drains. Deliberately layered on
# top of the existing DOM (position:fixed) so it never disturbs the companion
# panel. Auto per-screen capture is driven from Python; this button lets the
# reviewer also grab each interactive layer state they open during QA.

_CAPTURE_UI = r"""
<div id="cap-ui" style="position:fixed;right:16px;bottom:16px;z-index:2147483647;
 font:13px system-ui,Segoe UI,Arial,sans-serif;text-align:right">
  <div id="cap-flash" style="display:none;margin-bottom:6px;background:#1e8449;color:#fff;
   padding:4px 9px;border-radius:4px;box-shadow:0 1px 4px rgba(0,0,0,.3)"></div>
  <div id="cap-state" style="margin-bottom:6px;background:#26303b;color:#e6ecf1;
   padding:4px 9px;border-radius:4px;box-shadow:0 1px 4px rgba(0,0,0,.3);min-width:150px">
   capture ready</div>
  <button id="cap-btn" style="background:#c0392b;color:#fff;border:0;border-radius:6px;
   padding:9px 14px;font-weight:700;cursor:pointer;box-shadow:0 2px 6px rgba(0,0,0,.35)">
   &#128247; Capture state</button>
</div>
<script>
(function(){
  window.__captureQueue = window.__captureQueue || [];
  window.__captureCount = 0;
  var btn = document.getElementById('cap-btn');
  var flash = document.getElementById('cap-flash');
  var state = document.getElementById('cap-state');
  window.__capture = function(){
    window.__captureQueue.push({t: Date.now(), kind: 'manual'});
    window.__captureCount++;
    flash.textContent = 'Queued — ' + window.__captureCount + ' captured';
    flash.style.display = 'block';
    clearTimeout(window.__capFlashT);
    window.__capFlashT = setTimeout(function(){ flash.style.display='none'; }, 1400);
  };
  btn.addEventListener('click', window.__capture);
  // keyboard shortcut: Ctrl+Shift+S (avoids the browser's own save dialog focus)
  window.addEventListener('keydown', function(e){
    if(e.ctrlKey && e.shiftKey && (e.key==='S'||e.key==='s')){ e.preventDefault(); window.__capture(); }
  });
  // reflect the Python-side capture status (waiting / captured / manual-needed)
  setInterval(function(){
    if(typeof window.__capStatus === 'string') state.textContent = window.__capStatus;
  }, 250);
})();
</script>
"""


def _build_launcher_html(seed_js: str, companion: dict | None, capture: bool = False) -> str:
    head = "<!doctype html><html><head><script>" + seed_js + STUB_JS + "</script>"
    cap_ui = _CAPTURE_UI if capture else ""
    if companion is None:
        return (head + "</head><body style='margin:0'>"
                "<iframe id='course' src='/index_lms.html' "
                "style='border:0;width:100vw;height:100vh'></iframe>"
                + cap_ui + "</body></html>")
    # embed the companion data safely (avoid closing the script tag early)
    data_js = json.dumps(companion).replace("</", "<\\/")
    return (
        head + "<style>" + _COMPANION_CSS + "</style></head>"
        "<body style='margin:0'>"
        "<div style='display:flex;height:100vh'>"
        "<iframe id='course' src='/index_lms.html' style='border:0;flex:1;height:100vh'></iframe>"
        "<div id='cmp'><div id='cmp-head'></div><div id='cmp-body'></div></div>"
        "</div>"
        "<script>window.__companion=" + data_js + ";</script>"
        "<script>" + _COMPANION_JS + "</script>"
        + cap_ui +
        "</body></html>"
    )

class _Handler(http.server.SimpleHTTPRequestHandler):
    launcher_html = ""
    def do_GET(self):
        if self.path == "/__launch":
            b = self.launcher_html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
        try:
            super().do_GET()
        except (BrokenPipeError, ConnectionResetError):
            pass
    def log_message(self, *a):
        pass

class CourseServer:
    def __init__(self, course_dir: str, port: int = 0, seed: dict | None = None,
                 companion: dict | None = None, capture: bool = False):
        seed_js = f"window.__scormSeed = {json.dumps(seed or {})};"
        handler = type("H", (_Handler,), {
            "launcher_html": _build_launcher_html(seed_js, companion, capture)
        })
        http.server.ThreadingHTTPServer.allow_reuse_address = True
        self.srv = http.server.ThreadingHTTPServer(
            ("127.0.0.1", port), functools.partial(handler, directory=course_dir))
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    @property
    def launch_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/__launch"

    def close(self):
        self.srv.shutdown()

class Launched:
    """A launched course: top-level page + the course frame."""
    def __init__(self, page, course_frame):
        self.page = page
        self.frame = course_frame

    def scorm_log(self) -> list:
        return self.page.evaluate("window.__scormLog")

    def cmi(self) -> dict:
        return self.page.evaluate("window.__cmi")

    def capture_course(self, path: str) -> bool:
        """Screenshot just the course iframe (excludes the companion panel).
        Returns True on success. Never raises — capture is best-effort and must
        not interrupt a manual QA pass."""
        try:
            self.page.locator("#course").screenshot(path=path)
            return True
        except Exception:
            return False

    def drain_capture_queue(self) -> list:
        """Return and clear any manual-capture requests queued by the panel
        button. Empty list if capture UI isn't present."""
        try:
            return self.page.evaluate(
                "() => (window.__captureQueue ? window.__captureQueue.splice(0) : [])")
        except Exception:
            return []

    def active_slide_text(self) -> str | None:
        """Normalized visible text of the ACTIVE slide. Used as a stability
        signal: on a timeline, text fades in synced to the voiceover, so this
        string grows until the timeline finishes, then holds steady. Looping
        audio and looping background animation don't change it (only the text
        does), so it's a reliable 'is the text done appearing?' signal.
        Returns None if no slide is mounted."""
        try:
            return self.frame.evaluate(
                """() => {
                  const vis = Array.from(document.querySelectorAll('.slide[class*="cs-"]'))
                    .filter(s => {
                      const cs = getComputedStyle(s), r = s.getBoundingClientRect();
                      return cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 0;
                    });
                  if (!vis.length) return null;
                  const t = vis[vis.length - 1].innerText || '';
                  return t.replace(/\s+/g, ' ').trim();
                }""")
        except Exception:
            return None

    def set_capture_status(self, text: str) -> None:
        """Push a short status string into the panel's capture readout."""
        try:
            self.page.evaluate("(s) => { window.__capStatus = s; }", text)
        except Exception:
            pass

    def current_slide_id(self) -> str | None:
        """Progress signal: slide id from the DOM (`.slide.cs-<id>` class).
        During transitions several slide elements can be mounted; the ACTIVE
        one is the last visible in DOM order. cmi.location is NOT used."""
        cls = self.frame.evaluate(
            """() => {
              const slides = Array.from(document.querySelectorAll('.slide[class*="cs-"]'))
                .filter(s => {
                  const cs = getComputedStyle(s);
                  const r = s.getBoundingClientRect();
                  return cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 0;
                });
              if (!slides.length) return null;
              const m = slides[slides.length - 1].className.match(/cs-([A-Za-z0-9_$]+)/);
              return m ? m[1] : null;
            }""")
        return cls

def launch(playwright, server: CourseServer, headless: bool = True, timeout_s: int = 45,
           no_viewport: bool = False, cancel=None, log=None):
    """Open the course, dismiss the launch/play gate, wait for slide DOM.

    no_viewport=True lets the page fill (and resize with) the real browser
    window instead of a fixed 1280x800 viewport — used by --observe so the
    course + companion can be maximized to full monitor. The driver keeps the
    fixed viewport for deterministic clicking.

    ``cancel`` (optional, anything with ``is_set()``) lets a caller abort the
    wait — the browser is closed and :class:`Cancelled` is raised. ``log``
    (optional) receives a heartbeat while waiting so the launch never looks
    frozen. Both default to the previous behavior when omitted.
    """
    log = log or (lambda m: print(m, flush=True))
    launch_args = ["--start-maximized"] if no_viewport else []
    browser = playwright.chromium.launch(headless=headless, args=launch_args)

    def _abort_browser():
        try:
            browser.close()
        except Exception:  # noqa: BLE001
            pass

    try:
        if no_viewport:
            context = browser.new_context(no_viewport=True)
        else:
            context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        page.goto(server.launch_url)
        course = None
        stage = "course frame"
        log(f"launch: opening course, waiting for {stage}…")
        # wait for the course frame and the play gate (heartbeat every ~5s)
        iters = timeout_s * 2
        for i in range(iters):
            if is_cancelled(cancel):
                _abort_browser()
                raise Cancelled()
            page.wait_for_timeout(500)
            if i and i % 10 == 0:  # ~every 5s
                log(f"launch: still waiting for {stage}… "
                    f"{i // 2}s / {timeout_s}s")
            course = next((f for f in page.frames
                           if f.url.endswith("index_lms.html")), None)
            if course is None:
                continue
            stage = "play gate / first slide"
            gate = course.locator(
                "#mobile-start-button, .mobile-start-overlay.shown button")
            if gate.count():
                log("launch: play gate found — starting course")
                gate.first.click()
                break
            if course.locator(".slide[class*=cs-]").count():
                break  # no gate on this build
        else:
            _abort_browser()
            raise TimeoutError(
                f"BLOCKED: {stage} never appeared within {timeout_s}s. "
                "The course may not have loaded (check media transcode / "
                "package structure).")
        # wait for first slide
        check(cancel)
        log("launch: waiting for the first slide to mount…")
        try:
            course.wait_for_selector(".slide[class*=cs-]", timeout=timeout_s * 1000)
        except Exception as e:  # noqa: BLE001 — normalize to a clear message
            _abort_browser()
            raise TimeoutError(
                f"BLOCKED: first slide never mounted within {timeout_s}s "
                f"({type(e).__name__}). The course frame loaded but no slide "
                "appeared — a video slide may be stuck loading.")
        log("launch: course ready")
        return browser, Launched(page, course)
    except Cancelled:
        _abort_browser()
        raise
