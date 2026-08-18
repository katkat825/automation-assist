"""Launch harness: local server + recording SCORM stub + Playwright page.

Deterministic core. The stub lives on the launcher (parent) window; the course
discovers it via standard SCORM window-walking. No course files are modified.
"""
from __future__ import annotations
import functools, http.server, json, threading
from pathlib import Path

STUB_JS = (Path(__file__).parent / "scorm_stub.js").read_text()

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
    def __init__(self, course_dir: str, port: int = 0, seed: dict | None = None):
        seed_js = f"window.__scormSeed = {json.dumps(seed or {})};"
        handler = type("H", (_Handler,), {
            "launcher_html": (
                "<!doctype html><html><head><script>" + seed_js + STUB_JS +
                "</script></head><body style='margin:0'>"
                "<iframe id='course' src='/index_lms.html' "
                "style='border:0;width:100vw;height:100vh'></iframe></body></html>"
            )
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

def launch(playwright, server: CourseServer, headless: bool = True, timeout_s: int = 45):
    """Open the course, dismiss the launch/play gate, wait for slide DOM."""
    browser = playwright.chromium.launch(headless=headless)
    page = browser.new_context(viewport={"width": 1280, "height": 800}).new_page()
    page.goto(server.launch_url)
    course = None
    # wait for the course frame and the play gate
    for _ in range(timeout_s * 2):
        page.wait_for_timeout(500)
        course = next((f for f in page.frames if f.url.endswith("index_lms.html")), None)
        if course is None:
            continue
        gate = course.locator("#mobile-start-button, .mobile-start-overlay.shown button")
        if gate.count():
            gate.first.click()
            break
        if course.locator(".slide[class*=cs-]").count():
            break  # no gate on this build
    else:
        raise TimeoutError("BLOCKED: launch gate / course frame never appeared")
    # wait for first slide
    course.wait_for_selector(".slide[class*=cs-]", timeout=timeout_s * 1000)
    return browser, Launched(page, course)
