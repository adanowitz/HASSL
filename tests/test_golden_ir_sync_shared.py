import yaml
from pathlib import Path
from .util_compile import run_compile

def test_golden_ir_sync_shared(tmp_path: Path):
    src = '''
    alias a = light.kitchen
    alias b = switch.kitchen_circuit
    sync shared [a, b] as ksync
    '''
    outdir = tmp_path / "out"; ir = run_compile(src, outdir)
    helpers = (outdir / "helpers_out.yaml").read_text()
    data = yaml.safe_load(helpers)

    # Check the helper proxy exists and is properly named
    assert "input_boolean" in data
    assert "hassl_ksync_onoff" in data["input_boolean"]
    assert data["input_boolean"]["hassl_ksync_onoff"]["name"].startswith("HASSL Proxy ksync")
