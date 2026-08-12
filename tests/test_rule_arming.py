from pathlib import Path

import pytest
import yaml

from hassl.cli import parse_hassl
from hassl.codegen import package as pkg_codegen
from hassl.codegen import rules_min
from hassl.semantics import analyzer as sem_analyzer
from hassl.semantics.analyzer import analyze


SOURCE = """
package home.hallway

alias light = light.hallway
alias motion = binary_sensor.hallway_motion

schedule evening:
  enable from 18:00 to 23:59;

rule hallway_motion:
  schedule use evening;
  arm when (light == on) not_by this;
  if (motion) then light = on
"""


def _compile(tmp_path: Path):
    sem_analyzer.GLOBAL_EXPORTS = {}
    program = parse_hassl(SOURCE)
    ir = analyze(program)
    outdir = tmp_path / "out_arm"
    pkg_codegen.emit_package(ir, str(outdir))
    rules_path = Path(rules_min.generate_rules(ir.to_dict(), str(outdir)))
    helpers_path = outdir / "helpers_out_arm.yaml"
    return ir.to_dict(), yaml.safe_load(rules_path.read_text()), yaml.safe_load(helpers_path.read_text())


def test_arm_when_is_preserved_in_ir():
    sem_analyzer.GLOBAL_EXPORTS = {}
    ir = analyze(parse_hassl(SOURCE)).to_dict()
    rule = ir["rules"][0]

    assert rule["arm_when"] == {
        "expr": {"op": "==", "left": "light", "right": "on"},
        "not_by": "this",
    }


def test_arm_when_requires_named_schedule():
    source = """
alias light = light.hallway
alias motion = binary_sensor.hallway_motion

rule hallway_motion:
  arm when (light == on) not_by this;
  if (motion) then light = on
"""
    with pytest.raises(ValueError, match="requires at least one named 'schedule use'"):
        analyze(parse_hassl(source))


def test_arm_when_emits_latch_arm_reset_and_rule_gate(tmp_path: Path):
    _, rules_doc, helpers_doc = _compile(tmp_path)
    automations = rules_doc["automation"]

    arm = next(a for a in automations if a["id"] == "hallway_motion__arm")
    assert arm["trigger"] == [{
        "platform": "state",
        "entity_id": "light.hallway",
        "to": "on",
    }]
    assert arm["action"] == [{
        "service": "input_boolean.turn_on",
        "target": {"entity_id": "input_boolean.hassl_armed_hallway_motion"},
    }]
    assert any(
        c.get("entity_id") == "binary_sensor.hassl_schedule_home_hallway_evening_active"
        for group in arm["condition"]
        for c in (group.get("conditions") or [group])
    )
    assert any(
        "hassl_ctx_rule_hallway_motion_" in c.get("value_template", "")
        for c in arm["condition"]
    )

    disarm = next(a for a in automations if a["id"] == "hallway_motion__disarm")
    assert disarm["trigger"][0]["platform"] == "template"
    assert "not (" in disarm["trigger"][0]["value_template"]
    assert disarm["action"] == [{
        "service": "input_boolean.turn_off",
        "target": {"entity_id": "input_boolean.hassl_armed_hallway_motion"},
    }]

    rule = next(a for a in automations if a["id"] == "hallway_motion__1")
    assert {
        "condition": "state",
        "entity_id": "input_boolean.hassl_armed_hallway_motion",
        "state": "on",
    } in rule["condition"]

    armed = helpers_doc["input_boolean"]["hassl_armed_hallway_motion"]
    assert armed == {"name": "HASSL Armed hallway_motion"}


def test_not_by_this_stamps_current_rule_context(tmp_path: Path):
    _, rules_doc, helpers_doc = _compile(tmp_path)
    rule = next(a for a in rules_doc["automation"] if a["id"] == "hallway_motion__1")

    assert any(
        step.get("data", {}).get("entity_id")
        == "input_text.hassl_ctx_rule_hallway_motion_light_hallway"
        for step in rule["action"]
    )
    assert "hassl_ctx_rule_hallway_motion_light_hallway" in helpers_doc["input_text"]


def test_arm_when_expands_inside_rule_template():
    source = """
package home.hallway

schedule evening:
  enable from 18:00 to 23:59;

template rule armed_motion(name, light, motion, sched=evening):
  schedule use sched
  arm when (light == on) not_by this
  if (motion) then light = on

use template armed_motion(
  name="hallway_motion",
  light=light.hallway,
  motion=binary_sensor.hallway_motion
)
"""
    sem_analyzer.GLOBAL_EXPORTS = {}
    rule = analyze(parse_hassl(source)).to_dict()["rules"][0]

    assert rule["name"] == "hallway_motion"
    assert rule["schedule_uses"] == ["evening"]
    assert rule["arm_when"]["expr"] == {
        "op": "==",
        "left": "light.hallway",
        "right": "on",
    }
