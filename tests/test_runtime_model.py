"""Static runtime-model extraction: layer reveal edges that aren't clicks."""
from app.automation.scorm.runtime.runtime_model import _extract_slide_effects


def _show(layer_id):
    return {"kind": "show_slidelayer", "hideOthers": "never",
            "objRef": {"type": "string", "value": f"_parent.{layer_id}"}}


def _slide():
    # base timeline end -> L1; L1 click -> L2; L2 timeline end -> L3;
    # L3 start -> L4; L4 start un-hides base object BTN
    return {
        "events": [{"kind": "ontimelinecomplete", "actions": [_show("L1")]}],
        "slideLayers": [
            {"isBaseLayer": True, "objects": [
                {"id": "BTN", "accType": "button", "events": []}]},
            {"id": "L1", "isBaseLayer": False, "objects": [
                {"id": "GO", "accType": "button", "events": [
                    {"kind": "onrelease", "actions": [_show("L2")]}]}]},
            {"id": "L2", "isBaseLayer": False, "timeline": {"duration": 2000},
             "events": [{"kind": "ontimelinecomplete", "actions": [_show("L3")]}]},
            {"id": "L3", "isBaseLayer": False,
             "events": [{"kind": "onslidestart", "actions": [_show("L4")]}]},
            {"id": "L4", "isBaseLayer": False,
             "events": [{"kind": "onslidestart", "actions": [
                 {"kind": "show", "objRef": {"type": "string", "value": "_parent.BTN"}}]}]},
        ],
    }


def test_base_timeline_end_reveals_layer():
    _, layers = _extract_slide_effects(_slide())
    assert layers["L1"].revealed_by_timeline_of == [""]


def test_layer_timeline_end_reveals_layer():
    effects, layers = _extract_slide_effects(_slide())
    assert effects["GO"].shows_layers == ["L2"]
    assert layers["L3"].revealed_by_timeline_of == ["L2"]
    assert layers["L2"].duration_ms == 2000


def test_layer_start_reveals_layer_and_object():
    _, layers = _extract_slide_effects(_slide())
    assert layers["L4"].revealed_on_start_of == ["L3"]
    assert layers["L4"].shows_objects == ["BTN"]
    assert layers["L3"].revealed_by_timeline_of == ["L2"]   # not double-counted
