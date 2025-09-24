from pathlib import Path, PurePath
import json
from .util_compile import run_compile
def test_golden_ir_sync_shared(tmp_path: Path):
    src = '''
    alias a = light.kitchen
    alias b = switch.kitchen_circuit
    sync shared [a, b] as ksync
    '''
    outdir = tmp_path / "out"; ir = run_compile(src, outdir)
    got = ir.to_dict()
    want = json.loads((Path(__file__).parent / "golden" / "ir_ksync.json").read_text())
    assert got == want
