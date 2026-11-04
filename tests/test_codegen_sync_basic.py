import yaml
from pathlib import Path
from .util_compile import run_compile

def test_sync_shared_onoff(tmp_path: Path):
    src = '''
    alias a = light.kitchen
    alias b = switch.kitchen_circuit
    sync shared [a, b] as ksync
    '''
    outdir = tmp_path / "out"; ir = run_compile(src, outdir)
    helpers = (outdir / "helpers_out.yaml").read_text()
    syncfile = (outdir / "sync_out_ksync.yaml").read_text()

    data = yaml.safe_load(helpers)
    # Assert helpers contain the expected structures Home Assistant will read
    assert "input_boolean" in data
    assert "hassl_ksync_onoff" in data["input_boolean"]
    # Also sanity check input_text contexts were emitted
    assert "input_text" in data
    assert any(k.startswith("hassl_ctx_") for k in data["input_text"].keys())


def test_sync_onoff_ignores_unknown_and_unavailable_states(tmp_path: Path):
    src = '''
    alias a = light.front_left
    alias b = light.front_right
    sync onoff [a, b] as front_lights
    '''
    outdir = tmp_path / "out"
    run_compile(src, outdir)

    sync_data = yaml.safe_load((outdir / "sync_out_front_lights.yaml").read_text())
    upstream = next(
        automation
        for automation in sync_data["automation"]
        if automation["alias"] == "HASSL sync front_lights upstream onoff"
    )

    assert upstream["trigger"] == [
        {"platform": "state", "entity_id": "light.front_left", "to": "on"},
        {"platform": "state", "entity_id": "light.front_left", "to": "off"},
        {"platform": "state", "entity_id": "light.front_right", "to": "on"},
        {"platform": "state", "entity_id": "light.front_right", "to": "off"},
    ]

    choices = upstream["action"][0]["choose"]
    assert choices[0]["conditions"][0]["value_template"] == (
        "{{ trigger.to_state.state == 'on' }}"
    )
    assert choices[1]["conditions"][0]["value_template"] == (
        "{{ trigger.to_state.state == 'off' }}"
    )
