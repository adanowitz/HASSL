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
