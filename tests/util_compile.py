from pathlib import Path
from hassl.cli import parse_hassl
from hassl.semantics.analyzer import analyze
from hassl.codegen.package import emit_package
from hassl.codegen.rules_min import generate_rules

def run_compile(src_text: str, outdir: Path):
    program = parse_hassl(src_text)
    ir = analyze(program)
    outdir.mkdir(parents=True, exist_ok=True)
    emit_package(ir, str(outdir))
    # Emit rules to mirror full compile pipeline for tests that assert on rule output
    generate_rules(ir.to_dict(), str(outdir))
    return ir
