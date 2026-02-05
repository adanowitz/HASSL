import yaml
from tests.util_compile import run_compile

def test_template_rule_instantiates_and_gates(tmp_path):
    src = """
    package demo.test

    schedule anytime:
      enable from 00:00 to 23:59;

    template rule motion_light(name, motion, lux, light, sched=anytime):
      schedule use sched
      if (motion && lux < 50) then light = on

    use template motion_light(
      name="kitchen_motion",
      motion=motion.kitchen,
      lux=sensor.kitchen_lux,
      light=light.kitchen_main
    )
    """
    outdir = tmp_path / "out"
    ir = run_compile(src, outdir)

    pkg_slug = "demo_test"
    # rules file naming is based on outdir slug, not package id
    from hassl.codegen.rules_min import _pkg_slug as _out_slug
    rule_file = outdir / f"rules_bundled_{_out_slug(str(outdir))}.yaml"
    assert rule_file.exists(), "Expected rules_bundled YAML to be generated"

    yaml_doc = yaml.safe_load(rule_file.read_text())
    rules = yaml_doc.get("automation", [])

    rule = next((r for r in rules if r.get("alias") == "HASSL kitchen_motion #1"), None)
    assert rule is not None, "Rule 'kitchen_motion' not found in emitted YAML"

    #  Confirm schedule gate is present (either direct or inside OR condition)
    def _all_conditions(conds):
        for c in conds or []:
            yield c
            if isinstance(c, dict) and c.get("condition") in ("or", "and"):
                yield from _all_conditions(c.get("conditions") or [])

    conds = list(_all_conditions(rule.get("condition", [])))
    assert any(
        c.get("entity_id") == "binary_sensor.hassl_schedule_demo_test_anytime_active"
        for c in conds
    ), "Expected condition on 'anytime' schedule gate"

    #  Confirm basic action is present
    actions = rule.get("action", [])
    assert any(
        a.get("service") == "homeassistant.turn_on"
        and a.get("target", {}).get("entity_id") == "light.kitchen_main"
        for a in actions
    ), "Expected action to turn on 'light.kitchen_main'"
