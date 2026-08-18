"""Shared test factories.

The checks all operate on parsed ``ScormData``. Building one by hand is
verbose and every test needs a slightly different shape, so these helpers
supply sensible defaults and let each test override only the field under
test. Nothing here reads a real course package — the suite is designed to
run in a clean checkout with no course files present.
"""

import pytest

from app.automation.scorm.parser import (
    AccObject,
    Choice,
    Interaction,
    LayerInfo,
    ScormData,
    SlideInfo,
    StoryData,
)


def make_slide(
    slide_id="s1",
    slide_title="Slide One",
    *,
    scene_number=1,
    scene_id="scene1",
    scene_title="Module 1",
    slide_number=1,
    lms_id="Slide1",
    html5url="html5/data/js/s1.js",
    interactions=None,
    texts=None,
    external_links=None,
    is_in_menu=True,
    duration_ms=0.0,
    layers=None,
) -> SlideInfo:
    return SlideInfo(
        scene_number=scene_number,
        scene_id=scene_id,
        scene_title=scene_title,
        slide_number=slide_number,
        slide_id=slide_id,
        slide_title=slide_title,
        lms_id=lms_id,
        html5url=html5url,
        interactions=list(interactions or []),
        texts=list(texts or []),
        external_links=list(external_links or []),
        is_in_menu=is_in_menu,
        duration_ms=duration_ms,
        layers=list(layers or []),
    )


def make_data(
    slides=None,
    *,
    course_title="Test Course",
    scorm_version="2004 4th Edition",
    scenes_raw=None,
    quiz_configs=None,
    scoring_configs=None,
    scormdriver_js="",
    frame_data=None,
    nav_outline=None,
    zip_path="test.zip",
    parse_errors=None,
    speed_control_enabled=None,
    navigation_flow=None,
) -> ScormData:
    return ScormData(
        course_title=course_title,
        scorm_version=scorm_version,
        scenes_raw=list(scenes_raw or []),
        slides=list(slides or []),
        quiz_configs=list(quiz_configs or []),
        scoring_configs=list(scoring_configs or []),
        scormdriver_js=scormdriver_js,
        frame_data=dict(frame_data or {}),
        nav_outline=list(nav_outline or []),
        zip_path=zip_path,
        parse_errors=list(parse_errors or []),
        speed_control_enabled=speed_control_enabled,
        navigation_flow=navigation_flow,
    )


def make_obj(
    obj_id="o1",
    *,
    kind="vectorshape",
    acc_type="",
    tab_index=-1,
    tab_enabled=False,
    reference_name="",
    text="",
    has_text=False,
    alt_text="",
    has_click_event=False,
    x_pos=0.0,
    y_pos=0.0,
    width=100.0,
    height=20.0,
) -> AccObject:
    return AccObject(
        obj_id=obj_id,
        kind=kind,
        acc_type=acc_type,
        tab_index=tab_index,
        tab_enabled=tab_enabled,
        reference_name=reference_name,
        text=text,
        has_text=has_text,
        alt_text=alt_text,
        has_click_event=has_click_event,
        x_pos=x_pos,
        y_pos=y_pos,
        width=width,
        height=height,
    )


def make_layer(layer_id="base", *, is_base=True, modal=False, objects=None) -> LayerInfo:
    return LayerInfo(
        layer_id=layer_id,
        is_base=is_base,
        modal=modal,
        objects=list(objects or []),
    )


def make_interaction(
    obj_id="q1",
    *,
    lms_id="Question1",
    question_text="What is the answer?",
    question_type="multiple_choice",
    choices=None,
    correct_choice_ids=None,
    is_survey=False,
) -> Interaction:
    if choices is None:
        choices = [Choice(id="c1", text="Right"), Choice(id="c2", text="Wrong")]
    return Interaction(
        id=obj_id,
        lms_id=lms_id,
        question_text=question_text,
        question_type=question_type,
        choices=list(choices),
        correct_choice_ids=list(correct_choice_ids or ["c1"]),
        is_survey=is_survey,
    )


# A scormdriver.js containing every marker check_scorm_api looks for.
# Tests that want a failure remove the specific line they care about.
GOOD_SCORMDRIVER_JS = """
    SetValue("cmi.completion_status", "completed");
    SetValue("cmi.success_status", "passed");
    SetValue("cmi.score.scaled", scaled);
    SetValue("cmi.score.raw", raw);
    SetValue("cmi.suspend_data", data);
    function CallCommit() { }
    var DEFAULT_EXIT_TYPE = EXIT_TYPE_SUSPEND;
    var FORCED_COMMIT_TIME = "60000";
"""


@pytest.fixture
def monkeypatch_settings():
    """Replace settings.load() for the duration of one test.

    Several modules read settings on every call (deliberately, so a change
    takes effect without an app restart). Tests therefore need to control
    what load() returns rather than touching a real settings.json.
    """
    from app.config import settings as cfg

    original = cfg.load
    applied = []

    def apply(value: dict):
        cfg.load = lambda: value
        applied.append(True)

    yield apply
    cfg.load = original


@pytest.fixture
def monkeypatch_paths():
    """Point settings at a throwaway file instead of the user's real one."""
    from app.config import settings as cfg

    original = cfg.SETTINGS_FILE

    def apply(path):
        cfg.SETTINGS_FILE = path

    yield apply
    cfg.SETTINGS_FILE = original


@pytest.fixture
def temp_db():
    """Point the history store at a throwaway sqlite file."""
    import tempfile

    from app.db import history

    original = history.DB_FILE
    handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    handle.close()
    history.DB_FILE = handle.name
    history.init_db()
    yield history
    history.DB_FILE = original


@pytest.fixture
def slide():
    return make_slide()


@pytest.fixture
def data():
    return make_data([make_slide()])


@pytest.fixture
def story():
    return StoryData(parse_errors=[])
