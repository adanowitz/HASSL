from pathlib import Path
from hassl.cli import parse_hassl
from hassl.semantics.analyzer import analyze
from hassl.codegen.package import emit_package

def run_compile(src_text: str, outdir: Path):
    program = parse_hassl(src_text)
    ir = analyze(program)
    outdir.mkdir(parents=True, exist_ok=True)
    emit_package(ir, str(outdir))
    return ir
