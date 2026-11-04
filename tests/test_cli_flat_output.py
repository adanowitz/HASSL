from pathlib import Path

import yaml

from hassl import cli


def test_directory_compile_writes_all_packages_at_output_top_level(
    tmp_path: Path, monkeypatch
):
    source = tmp_path / "area"
    source.mkdir()
    (source / "lights.hassl").write_text(
        """
package home.area.lights
alias porch = light.porch
"""
    )
    (source / "switches.hassl").write_text(
        """
package home.area.switches
alias decorations = switch.decorations
"""
    )
    output = tmp_path / "packages"

    monkeypatch.setattr(
        "sys.argv", ["hasslc", str(source), "-o", str(output)]
    )
    cli.main()

    assert not [path for path in output.iterdir() if path.is_dir()]
    for package in ("home_area_lights", "home_area_switches"):
        expected = {
            f"helpers_{package}.yaml",
            f"scripts_{package}.yaml",
            f"rules_bundled_{package}.yaml",
        }
        assert expected <= {path.name for path in output.iterdir()}
        for filename in expected:
            if filename.endswith(".yaml"):
                assert isinstance(yaml.safe_load((output / filename).read_text()), dict)
    assert all(path.suffix == ".yaml" for path in output.iterdir())


def test_autoloaded_common_schedule_is_referenced_but_not_embedded(
    tmp_path: Path, monkeypatch
):
    source_root = tmp_path / "src"
    common = source_root / "home" / "common" / "schedules.hassl"
    common.parent.mkdir(parents=True)
    common.write_text(
        """
package home.common.schedules
schedule night:
  enable from sunset until sunrise;
"""
    )
    porch = source_root / "home" / "porch" / "lights.hassl"
    porch.parent.mkdir(parents=True)
    porch.write_text(
        """
package home.porch.lights
import home.common.schedules.*
alias porch = light.porch
rule porch_schedule:
  schedule use night
  at schedule start then porch = on
  at schedule stop then porch = off
"""
    )
    output = tmp_path / "packages"

    monkeypatch.setattr(
        "sys.argv",
        [
            "hasslc",
            str(porch.parent),
            "-o",
            str(output),
            "--module-root",
            str(source_root),
        ],
    )
    cli.main()

    names = {path.name for path in output.iterdir()}
    assert "rules_bundled_home_porch_lights.yaml" in names
    assert not any("home_common_schedules" in name for name in names)

    rules = (output / "rules_bundled_home_porch_lights.yaml").read_text()
    assert "binary_sensor.hassl_schedule_home_common_schedules_night_active" in rules


def test_room_compile_resolves_imports_relative_to_top_level_namespace(
    tmp_path: Path, monkeypatch
):
    source_root = tmp_path / "src"
    common = source_root / "home" / "common" / "schedules.hassl"
    common.parent.mkdir(parents=True)
    common.write_text(
        """
package home.common.schedules
schedule night:
  enable from sunset until sunrise;
"""
    )

    room = source_root / "home" / "porch"
    room.mkdir(parents=True)
    (room / "aliases.hassl").write_text(
        """
package home.porch.aliases
alias light = light.porch
"""
    )
    (room / "automations.hassl").write_text(
        """
package home.porch.automations
import porch.aliases.*
import common.schedules.*

rule porch_night:
  schedule use night
  if (light == off) then light = on
"""
    )
    output = tmp_path / "packages" / "porch"

    monkeypatch.setattr(
        "sys.argv",
        [
            "hasslc",
            str(room),
            "-o",
            str(output),
            "--module-root",
            str(source_root),
        ],
    )
    cli.main()

    rules = (output / "rules_bundled_home_porch_automations.yaml").read_text()
    assert "alias: 'HASSL porch_night #1'" in rules
    assert "binary_sensor.hassl_schedule_home_common_schedules_night_active" in rules
    assert not any("home_common_schedules" in path.name for path in output.iterdir())
