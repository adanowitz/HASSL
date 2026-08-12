from pathlib import Path

import yaml

from .util_compile import run_compile


def _automations(outdir: Path):
    data = yaml.safe_load((outdir / "rules_bundled_out.yaml").read_text())
    return data["automation"]


def test_bare_button_condition_handles_each_press(tmp_path: Path):
    src = """
    alias doorbell = button.front_door
    alias light = light.porch

    rule ring:
      if (doorbell) then light = on
    """
    outdir = tmp_path / "out"
    run_compile(src, outdir)

    automation = _automations(outdir)[0]
    assert automation["trigger"] == [
        {"platform": "state", "entity_id": "button.front_door"}
    ]

    press_condition = automation["condition"][-1]
    assert press_condition["condition"] == "template"
    template = press_condition["value_template"]
    assert "trigger.entity_id == 'button.front_door'" in template
    assert "trigger.from_state.state != trigger.to_state.state" in template


def test_button_event_composes_with_persistent_state(tmp_path: Path):
    src = """
    alias scene_button = input_button.movie_scene
    alias occupied = binary_sensor.living_room_occupied
    alias light = light.living_room

    rule movie_scene:
      if (scene_button && occupied) then light = off
    """
    outdir = tmp_path / "out"
    run_compile(src, outdir)

    automation = _automations(outdir)[0]
    assert automation["trigger"] == [
        {"platform": "state", "entity_id": "binary_sensor.living_room_occupied"},
        {"platform": "state", "entity_id": "input_button.movie_scene"},
    ]

    event_and_state = automation["condition"][-1]
    assert event_and_state["condition"] == "and"
    button_condition, occupied_condition = event_and_state["conditions"]
    assert "trigger.entity_id == 'input_button.movie_scene'" in button_condition["value_template"]
    assert occupied_condition == {
        "condition": "state",
        "entity_id": "binary_sensor.living_room_occupied",
        "state": "on",
    }


def test_explicit_button_state_comparison_remains_a_state_condition(tmp_path: Path):
    src = """
    alias scene_button = input_button.movie_scene
    alias light = light.living_room

    rule explicit_state:
      if (scene_button == on) then light = on
    """
    outdir = tmp_path / "out"
    run_compile(src, outdir)

    condition = _automations(outdir)[0]["condition"][-1]
    assert condition == {
        "condition": "state",
        "entity_id": "input_button.movie_scene",
        "state": "on",
    }
