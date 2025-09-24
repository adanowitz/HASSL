from pathlib import Path
from .util_compile import run_compile
def test_sync_shared_onoff(tmp_path: Path):
    src = '''
    alias a = light.kitchen
    alias b = switch.kitchen_circuit
    sync shared [a, b] as ksync
    '''
    outdir = tmp_path / "out"; ir = run_compile(src, outdir)
    helpers = (outdir / "helpers.yaml").read_text()
    syncfile = (outdir / "sync__ksync.yaml").read_text()
    assert "input_boolean.hassl__ksync__onoff" in helpers
    assert "HASSL sync ksync upstream onoff" in syncfile
