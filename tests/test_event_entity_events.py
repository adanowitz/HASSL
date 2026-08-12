from pathlib import Path

import yaml

from .util_compile import run_compile


def _automations(outdir: Path):
    data = yaml.safe_load((outdir / "rules_bundled_out.yaml").read_text())
    return data["automation"]


def test_event_entity_can_filter_event_type(tmp_path: Path):
    src = """
    alias wesley_button = event.wesley_switch_button_7
    alias light = light.wesley_room

    rule wesley_button_press:
      if (wesley_button == short_release) then light = on
    """
    outdir = tmp_path / "out"
    run_compile(src, outdir)

    automation = _automations(outdir)[0]
    assert automation["trigger"] == [
        {"platform": "state", "entity_id": "event.wesley_switch_button_7"}
    ]

    event_condition = automation["condition"][-1]
    assert event_condition["condition"] == "template"
    template = event_condition["value_template"]
    assert "trigger.entity_id == 'event.wesley_switch_button_7'" in template
    assert "trigger.from_state.state != trigger.to_state.state" in template
    assert "trigger.to_state.attributes.get('event_type') == 'short_release'" in template


def test_event_entity_supports_friendly_clicked_keyword(tmp_path: Path):
    src = """
    alias wesley_button = event.wesley_switch_button_7
    alias light = light.wesley_room

    rule wesley_button_press:
      if (wesley_button is clicked) then light = on
    """
    outdir = tmp_path / "out"
    run_compile(src, outdir)

    event_condition = _automations(outdir)[0]["condition"][-1]
    template = event_condition["value_template"]
    assert (
        "trigger.to_state.attributes.get('event_type') "
        "in ['short_release', 'press_end']"
    ) in template


def test_unknown_friendly_event_keyword_remains_a_raw_event_type(tmp_path: Path):
    src = """
    alias custom_button = event.custom_button
    alias light = light.office

    rule custom_event:
      if (custom_button is vendor_tap) then light = on
    """
    outdir = tmp_path / "out"
    run_compile(src, outdir)

    template = _automations(outdir)[0]["condition"][-1]["value_template"]
    assert "attributes.get('event_type') == 'vendor_tap'" in template


def test_bare_event_entity_matches_each_real_emission(tmp_path: Path):
    src = """
    alias doorbell = event.front_door
    alias light = light.porch

    rule ring:
      if (doorbell) then light = on
    """
    outdir = tmp_path / "out"
    run_compile(src, outdir)

    event_condition = _automations(outdir)[0]["condition"][-1]
    template = event_condition["value_template"]
    assert "trigger.entity_id == 'event.front_door'" in template
    assert "trigger.from_state.state != trigger.to_state.state" in template
    assert "attributes.get('event_type')" not in template


def test_event_type_composes_with_persistent_state(tmp_path: Path):
    src = """
    alias scene_button = event.wesley_switch_button_7
    alias occupied = binary_sensor.wesley_room_occupied
    alias light = light.wesley_room

    rule occupied_button_press:
      if (scene_button == short_release && occupied) then light = off
    """
    outdir = tmp_path / "out"
    run_compile(src, outdir)

    automation = _automations(outdir)[0]
    assert automation["trigger"] == [
        {"platform": "state", "entity_id": "binary_sensor.wesley_room_occupied"},
        {"platform": "state", "entity_id": "event.wesley_switch_button_7"},
    ]

    combined = automation["condition"][-1]
    assert combined["condition"] == "and"
    event_condition, occupied_condition = combined["conditions"]
    assert "attributes.get('event_type') == 'short_release'" in event_condition["value_template"]
    assert occupied_condition == {
        "condition": "state",
        "entity_id": "binary_sensor.wesley_room_occupied",
        "state": "on",
    }
