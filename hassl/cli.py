import argparse
import os, json
from .parser.loader import load_grammar_text
from .parser.transform import HasslTransformer
from .ast.nodes import Program
from lark import Lark
from .semantics.analyzer import analyze
from .codegen.package import emit_package
from .codegen import generate as codegen_generate

#GRAMMAR_PATH = os.path.join(os.path.dirname(__file__), "parser", "hassl.lark")

def parse_hassl(text: str) -> Program:
    grammar = load_grammar_text()
    parser = Lark(grammar, start="start", parser="lalr", maybe_placeholders=False)
    tree = parser.parse(text)
    program = HasslTransformer().transform(tree)
    return program

def main():
    ap = argparse.ArgumentParser(prog="hasslc", description="HASSL Compiler")
    ap.add_argument("input", help="Input .hassl file")
    ap.add_argument("-o", "--out", default="./packages/out", help="Output directory for HA package")
    args = ap.parse_args()

    with open(args.input) as f:
        src = f.read()

    program = parse_hassl(src)
    print("[hasslc] AST:", program.to_dict())
    ir = analyze(program)
    print("[hasslc] IR:", ir.to_dict())

    ir_dict = ir.to_dict() if hasattr(ir, "to_dict") else ir
    codegen_generate(ir_dict, args.out)
    print(f"[hasslc] Package written to {args.out}")

    os.makedirs(args.out, exist_ok=True)
    emit_package(ir, args.out)
    with open(os.path.join(args.out, "DEBUG_ir.json"), "w") as dbg:
        dbg.write(json.dumps(ir.to_dict(), indent=2))
    print(f"[hasslc] Package written to {args.out}")
