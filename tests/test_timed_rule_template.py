from pathlib import Path

import pytest
import yaml

from .util_compile import run_compile


def _automations(outdir: Path):
    data = yaml.safe_load((outdir / "rules_bundled_out.yaml").read_text())
    return data["automation"]


def test_timed_light_template_supports_sunset_offset_and_clock(tmp_path: Path):
    src = """
    schedule anytime:
      enable from 00:00 to 23:59;

    template rule timed_light(name, light, turn_on, turn_off, sched=anytime):
      schedule use sched
      at turn_on then light = on
      at turn_off then light = off

    use template timed_light(
      name=porch_timed,
      light=light.porch,
      turn_on=sunset-30m,
      turn_off=23:15,
      sched=anytime
    )
    """
    outdir = tmp_path / "out"
    run_compile(src, outdir)

    automations = _automations(outdir)
    assert len(automations) == 2
    assert automations[0]["trigger"] == [
        {"platform": "sun", "event": "sunset", "offset": "-00:30:00"}
    ]
    assert automations[1]["trigger"] == [
        {"platform": "time", "at": "23:15:00"}
    ]
    for automation in automations:
        schedule_condition = automation["condition"][1]
        assert schedule_condition["condition"] == "or"
        assert any(
            condition.get("entity_id", "").startswith("binary_sensor.hassl_schedule_")
            and condition["entity_id"].endswith("anytime_active")
            and condition.get("state") == "on"
            for condition in schedule_condition["conditions"]
        )
    assert automations[0]["action"][-1] == {
        "service": "homeassistant.turn_on",
        "target": {"entity_id": "light.porch"},
    }
    assert automations[1]["action"][-1] == {
        "service": "homeassistant.turn_off",
        "target": {"entity_id": "light.porch"},
    }


def test_timed_rule_supports_sunrise_with_offset(tmp_path: Path):
    src = """
    alias porch = light.porch

    rule sunrise_light:
      at sunrise+20m then porch = on
    """
    outdir = tmp_path / "out"
    run_compile(src, outdir)

    automation = _automations(outdir)[0]
    assert automation["trigger"] == [
        {"platform": "sun", "event": "sunrise", "offset": "+00:20:00"}
    ]


def test_timed_rule_rejects_invalid_clock_value(tmp_path: Path):
    src = """
    alias porch = light.porch

    template rule timed_light(name, light, turn_on):
      at turn_on then light = on

    use template timed_light(
      name=porch_timed,
      light=porch,
      turn_on="25:00"
    )
    """
    with pytest.raises(ValueError, match="invalid timed rule value '25:00'"):
        run_compile(src, tmp_path / "out")


def test_schedule_start_and_stop_emit_transition_and_startup_triggers(tmp_path: Path):
    src = """
    alias porch = light.porch

    schedule porch_hours:
      on weekdays 17:00-23:00;
      on weekends 17:00-00:30;

    rule porch_lighting:
      schedule use porch_hours;
      at schedule start then porch = on
      at schedule stop then porch = off
    """
    outdir = tmp_path / "out"
    ir = run_compile(src, outdir)

    clauses = ir.to_dict()["rules"][0]["clauses"]
    assert [clause["time"] for clause in clauses] == [
        {"kind": "schedule", "event": "start"},
        {"kind": "schedule", "event": "stop"},
    ]

    start, stop = _automations(outdir)
    assert start["trigger"][0]["platform"] == "template"
    assert "not (" not in start["trigger"][0]["value_template"]
    assert start["trigger"][1] == {"platform": "homeassistant", "event": "start"}
    assert start["trigger"][2] == {
        "platform": "state",
        "entity_id": "input_boolean.hassl_gate_porch_lighting",
        "to": "on",
    }
    assert start["condition"][1] == {
        "condition": "template",
        "value_template": start["trigger"][0]["value_template"],
    }
    assert start["action"][-1] == {
        "service": "homeassistant.turn_on",
        "target": {"entity_id": "light.porch"},
    }

    assert stop["trigger"][0]["platform"] == "template"
    assert "not (" in stop["trigger"][0]["value_template"]
    assert stop["trigger"][1] == {"platform": "homeassistant", "event": "start"}
    assert stop["trigger"][2] == {
        "platform": "state",
        "entity_id": "input_boolean.hassl_gate_porch_lighting",
        "to": "on",
    }
    assert stop["condition"][1] == {
        "condition": "template",
        "value_template": stop["trigger"][0]["value_template"],
    }
    assert stop["action"][-1] == {
        "service": "homeassistant.turn_off",
        "target": {"entity_id": "light.porch"},
    }


def test_schedule_transition_requires_named_schedule_use(tmp_path: Path):
    src = """
    alias porch = light.porch

    rule porch_lighting:
      at schedule start then porch = on
    """
    with pytest.raises(
        ValueError,
        match="'at schedule start/stop' requires at least one named 'schedule use'",
    ):
        run_compile(src, tmp_path / "out")


def test_schedule_transition_uses_combined_schedule_state(tmp_path: Path):
    src = """
    alias porch = light.porch

    schedule evening:
      enable from 17:00 to 23:00;

    schedule winter:
      enable from 00:00 to 23:59;

    rule porch_lighting:
      schedule use evening, winter;
      at schedule start then porch = on
      at schedule stop then porch = off
    """
    outdir = tmp_path / "out"
    run_compile(src, outdir)

    start, stop = _automations(outdir)
    start_template = start["trigger"][0]["value_template"]
    stop_template = stop["trigger"][0]["value_template"]
    assert " and " in start_template
    assert "evening_active" in start_template
    assert "winter_active" in start_template
    assert stop_template == "{{ not (" + start_template[3:-3] + ") }}"
