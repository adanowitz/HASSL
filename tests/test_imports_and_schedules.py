# tests/test_imports_and_schedules.py
import json
from pathlib import Path

import pytest

from hassl.cli import parse_hassl  # parser + transformer
from hassl.semantics import analyzer as sem_analyzer  # to set GLOBAL_EXPORTS
from hassl.semantics.analyzer import analyze         # analyzer -> IR
from hassl.ast.nodes import Alias, Schedule          # node types for exports
from hassl.codegen import rules_min                  # codegen entry we’re using (generate_rules)


# --------- helpers (mirrors cli.py logic lightly) ---------

def _collect_public_exports(prog, pkg: str):
    out = {}
    for s in prog.statements:
        if isinstance(s, Alias) and not getattr(s, "private", False):
            out[(pkg, "alias", s.name)] = s
    for s in prog.statements:
        if isinstance(s, Schedule) and not getattr(s, "private", False):
            out[(pkg, "schedule", s.name)] = s
        elif isinstance(s, dict) and s.get("type") == "schedule_decl" and not s.get("private", False):
            out[(pkg, "schedule", s["name"])] = Schedule(
                name=s["name"], clauses=s.get("clauses", []) or [], private=False
            )
    return out


def _write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


# =========== tests ===========

def test_import_glob_alias_and_schedule(tmp_path: Path):
    """home imports std.*; landing_light + wake_hours usable; schedule name normalized to pkg.name."""
    shared = tmp_path / "std" / "shared.hassl"
    landing = tmp_path / "home" / "landing.hassl"

    _write(shared, """
package std.shared

alias landing_light = light.landing_main
private alias _debug = light.dev_fixture

schedule wake_hours:
  enable from 07:00 to 23:00;
""")

    _write(landing, """
package home.landing
import std.shared.*

rule motion_light:
  schedule use wake_hours;
  if (motion && lux < 50)
  then landing_light = on; wait (!motion for 10m) landing_light = off
""")

    # parse both
    p_shared = parse_hassl(shared.read_text()); p_shared.package = "std.shared"
    p_landing = parse_hassl(landing.read_text()); p_landing.package = "home.landing"

    # build exports (like cli pass 1)
    sem_analyzer.GLOBAL_EXPORTS = _collect_public_exports(p_shared, "std.shared")

    # analyze home
    ir = analyze(p_landing).to_dict()

    # alias injected, resolved in actions
    assert ir["aliases"]["landing_light"] == "light.landing_main"

    # schedule use normalized to fully-qualified form
    (rule,) = ir["rules"]
    assert rule["schedule_uses"] == ["std.shared.wake_hours"]

    # actions targets resolved to entity id
    assigns = [a for a in rule["clauses"][0]["actions"] if a["type"] == "assign"]
    assert {a["target"] for a in assigns} == {"light.landing_main"}


def test_private_alias_not_imported(tmp_path: Path):
    """private alias is not visible to importer."""
    shared = tmp_path / "std" / "shared.hassl"
    landing = tmp_path / "home" / "landing.hassl"

    _write(shared, """
package std.shared
private alias hidden = light.foo
alias visible = light.bar
""")

    _write(landing, """
package home.landing
import std.shared.*

rule r:
  if (visible) then visible = on
""")

    p_shared = parse_hassl(shared.read_text()); p_shared.package = "std.shared"
    p_landing = parse_hassl(landing.read_text()); p_landing.package = "home.landing"

    sem_analyzer.GLOBAL_EXPORTS = _collect_public_exports(p_shared, "std.shared")

    ir = analyze(p_landing).to_dict()

    # Only the public alias should be injected; private not present
    assert "visible" in ir["aliases"]
    assert "hidden" not in ir["aliases"]


def test_declared_schedule_emits_helpers_and_automations(tmp_path: Path):
    """Top-level schedule creates helpers + start/end/maintain automations in rules_min."""
    shared = tmp_path / "std" / "shared.hassl"
    _write(shared, """
package std.shared
alias lamp = light.fake_lamp    
schedule wake_hours:
  enable from 07:00 to 23:00;

rule ping:
    schedule use wake_hours;
    if (lamp) then lamp = on
""")
    p_shared = parse_hassl(shared.read_text()); p_shared.package = "std.shared"
    sem_analyzer.GLOBAL_EXPORTS = {}  # standalone compile is fine

    ir_shared = analyze(p_shared)  # contains schedule + a rule that uses it
    outdir = tmp_path / "out_std"
    out_yaml = rules_min.generate_rules(ir_shared.to_dict(), str(outdir))

    # Helpers YAML should contain the schedule boolean (base token name)
    helpers = (outdir / f"helpers_{rules_min._pkg_slug(str(outdir))}.yaml").read_text()
    assert "input_boolean:" in helpers
    assert "hassl_schedule_wake_hours" in helpers

    # Bundled automations file should include 3 entries with alias containing schedule name
    bundled = (outdir / f"rules_bundled_{rules_min._pkg_slug(str(outdir))}.yaml").read_text()
    # start automation
    assert "HASSL schedule wake_hours start" in bundled
    # end automation
    assert "HASSL schedule wake_hours end" in bundled
    # maintain automation
    assert "HASSL schedule wake_hours maintain" in bundled


def test_inline_schedule_creates_per_rule_boolean(tmp_path: Path):
    """Inline schedule generates rule-specific boolean gates and automations, and the rule conditions check them."""
    landing = tmp_path / "home" / "landing.hassl"
    _write(landing, """
package home.landing

alias kitchen = light.kitchen
    
rule motion_light:
  schedule
    enable from sunrise+15m until 23:00;
  if (motion) then kitchen = on
""")
    p = parse_hassl(landing.read_text()); p.package = "home.landing"
    sem_analyzer.GLOBAL_EXPORTS = {}
    ir = analyze(p)

    outdir = tmp_path / "out_inline"
    out_yaml = rules_min.generate_rules(ir.to_dict(), str(outdir))

    helpers = (outdir / f"helpers_{rules_min._pkg_slug(str(outdir))}.yaml").read_text()
    # rule-specific schedule boolean exists
    assert "hassl_schedule_rule_motion_light" in helpers

    bundled = (outdir / f"rules_bundled_{rules_min._pkg_slug(str(outdir))}.yaml").read_text()
    # inline schedule’s automations present (start/end/maintain of the rule schedule)
    assert "HASSL schedule rule_motion_light start" in bundled
    assert "HASSL schedule rule_motion_light end" in bundled
    assert "HASSL schedule rule_motion_light maintain" in bundled

    # the rule automation should include a condition referencing the rule schedule boolean
    assert "input_boolean.hassl_schedule_rule_motion_light" in bundled


def test_cross_package_import_and_codegen_gate(tmp_path: Path):
    """home uses std.wake_hours; codegen must gate the rule with the base schedule boolean."""
    shared = tmp_path / "std" / "shared.hassl"
    homep = tmp_path / "home" / "landing.hassl"

    _write(shared, """
package std.shared
alias landing_light = light.landing_main
schedule wake_hours:
  enable from 07:00 to 23:00;
""")
    _write(homep, """
package home.landing
import std.shared.*

rule motion_light:
  schedule use wake_hours;
  if (motion) then landing_light = on
""")

    # parse + export
    p_shared = parse_hassl(shared.read_text()); p_shared.package = "std.shared"
    sem_analyzer.GLOBAL_EXPORTS = _collect_public_exports(p_shared, "std.shared")

    # analyze home
    p_home = parse_hassl(homep.read_text()); p_home.package = "home.landing"
    ir_home = analyze(p_home)

    # generate combined YAML for the home package
    outdir = tmp_path / "out_home"
    rules_min.generate_rules(ir_home.to_dict(), str(outdir))

    helpers = (outdir / f"helpers_{rules_min._pkg_slug(str(outdir))}.yaml").read_text()
    # shared schedule helper exists under base token name
    assert "hassl_schedule_wake_hours" in helpers

    bundled = (outdir / f"rules_bundled_{rules_min._pkg_slug(str(outdir))}.yaml").read_text()
    # the rule’s condition gates on that shared schedule boolean (base token)
    assert "input_boolean.hassl_schedule_wake_hours" in bundled
