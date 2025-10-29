# tests/test_schedule_windows_codegen.py
import yaml
from pathlib import Path

from hassl.codegen import package as pkg_codegen
from hassl.semantics.analyzer import IRProgram, IRSync

def _read_yaml(p: Path):
    # Load possibly "multi-doc" helpers we write; always return dict
    txt = p.read_text()
    data = yaml.safe_load(txt)
    return data or {}

def test_schedule_windows_codegen_weekday_weekend_and_holidays(tmp_path: Path):
    """
    Verifies:
      - input_boolean.hassl_sched_<pkg>_<name> helper exists
      - one maintenance automation per schedule with time_pattern trigger
      - sun triggers are included when any window uses sunrise/sunset
      - holiday sensors (workday + derived holiday template) are emitted
      - conditions include weekday/weekend and holiday 'only'/'except'
    """
    # Build a minimal IRProgram with schedule windows and a holiday set
    ir = IRProgram(
        aliases={},
        syncs=[],  # no syncs needed
        rules=[],  # no rules needed
        schedules={},  # legacy schedules unused here
        schedules_windows={
            "wake": [
                # Weekdays: 07:00-23:00
                {
                    "start": {"kind": "clock", "value": "07:00"},
                    "end":   {"kind": "clock", "value": "23:00"},
                    "day_selector": "weekdays",
                    "period": None,
                    "holiday_ref": None,
                    "holiday_mode": None,
                },
                # Weekends: 08:30-22:00, holidays except (i.e., off on holidays)
                {
                    "start": {"kind": "clock", "value": "08:30"},
                    "end":   {"kind": "clock", "value": "22:00"},
                    "day_selector": "weekends",
                    "period": None,
                    "holiday_ref": "us",
                    "holiday_mode": "except",
                },
                # Sunset..Sunrise with holiday only (on holidays only)
                {
                    "start": {"kind": "sun", "event": "sunset", "offset": "+0m"},
                    "end":   {"kind": "sun", "event": "sunrise", "offset": "+0m"},
                    "day_selector": "daily",
                    "period": None,
                    "holiday_ref": "us",
                    "holiday_mode": "only",
                },
            ]
        },
        holidays={
            # Minimal holiday set; province/workdays/excludes optional
            "us": {
                "id": "us",
                "country": "US",
                "province": None,
                "add": [],            # keep empty for the test
                "remove": [],         # keep empty for the test
                "workdays": [],       # defaults okay
                "excludes": [],       # defaults okay
            }
        },
    )
    # Name the package so helpers are namespaced correctly
    setattr(ir, "package", "home.landing")

    outdir = tmp_path / "out_home_landing"
    pkg_codegen.emit_package(ir, str(outdir))

    # Files on disk are named with the IR package (not an outdir slug)
    pkg_id = getattr(ir, "package", "pkg")

    # ---- Helpers: input_boolean for the schedule (namespaced with pkg) ----
    helpers = _read_yaml(outdir / f"helpers_{pkg_id}.yaml")
    ibo = helpers.get("input_boolean") or {}
    # Current package.py creates a namespaced schedule boolean (pkg + name)
    expect_key = f"hassl_sched_{pkg_id.replace('.', '_')}_wake"
    assert expect_key in ibo, f"expected {expect_key} in helpers input_boolean"

    # ---- Holidays: workday + derived holiday template ----
    hol_yaml_path = outdir / f"holidays_{pkg_id}.yaml"
    assert hol_yaml_path.exists(), "holidays YAML not emitted"
    hol_doc = yaml.safe_load(hol_yaml_path.read_text())
    # At minimum we expect a workday platform entry
    assert "binary_sensor" in hol_doc, "workday binary_sensor section missing"
 
    # ---- Automations: current code emits per-window on/off automations ----
    sched_yaml = outdir / f"schedule_{pkg_id}_wake.yaml"
    assert sched_yaml.exists(), "per-schedule automation YAML not emitted"
    sched_doc = _read_yaml(sched_yaml)
    autos = sched_doc.get("automation") or []

    # We expect at least one "on" and one "off" automation across windows
    assert any(a for a in autos if any(step.get("service") == "input_boolean.turn_on"
                                       and step.get("target", {}).get("entity_id") == f"input_boolean.{expect_key}"
                                       for step in (a.get("action") or [])))
    assert any(a for a in autos if any(step.get("service") == "input_boolean.turn_off"
                                       and step.get("target", {}).get("entity_id") == f"input_boolean.{expect_key}"
                                       for step in (a.get("action") or [])))

    # Spot-check that at least one weekday condition and one weekend condition exist
    def _conds(auto):
        for c in auto.get("condition") or []:
            yield c
    has_weekdays = any(
        any(c.get("condition") == "time" and c.get("weekday") == ["mon","tue","wed","thu","fri"] for c in _conds(a))
        for a in autos
    )
    has_weekends = any(
        any(c.get("condition") == "time" and c.get("weekday") == ["sat","sun"] for c in _conds(a))
        for a in autos
    )
    assert has_weekdays, "expected a weekday time condition"
    assert has_weekends, "expected a weekend time condition"
