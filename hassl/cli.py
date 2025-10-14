import argparse
import os, json, glob
from pathlib import Path
from typing import Dict, Tuple, List
from .parser.loader import load_grammar_text
from .parser.transform import HasslTransformer
from .ast.nodes import Program, Alias, Schedule
from lark import Lark
from .semantics import analyzer as sem_analyzer
from .semantics.analyzer import analyze
from .codegen.package import emit_package
from .codegen import generate as codegen_generate

def parse_hassl(text: str) -> Program:
    grammar = load_grammar_text()
    parser = Lark(grammar, start="start", parser="lalr", maybe_placeholders=False)
    tree = parser.parse(text)
    program = HasslTransformer().transform(tree)
    return program


def _normalize_module(importing_pkg: str, mod: str) -> str:
    """
    Resolve Python-like relative module notation to an absolute dotted id.
    Examples (importing_pkg='home.addie.automations'):
      '.shared'   -> 'home.addie.shared'
      '..shared'  -> 'home.shared'
      'std.shared' (absolute) stays 'std.shared'
    """
    if not mod:
        return mod
    if not mod.startswith("."):
        return mod  # already absolute
    # Count leading dots
    i = 0
    while i < len(mod) and mod[i] == ".":
        i += 1
    rel = mod[i:]  # tail after dots (may be '')
    base_parts = (importing_pkg or "").split(".")
    # Pop one level per dot
    up = i - 1  # '.x' means stay at same depth + replace last segment -> up=0
    if up > 0 and up <= len(base_parts):
        base_parts = base_parts[:len(base_parts) - up]
    elif up > len(base_parts):
        base_parts = []
    if rel:
        return ".".join([p for p in base_parts if p] + [rel])
    return ".".join([p for p in base_parts if p])

def _derive_package_name(prog: Program, src_path: Path, module_root: Path | None) -> str:
    """
    If the source did not declare `package`, derive one from the path:
    - If module_root is given and src_path is under it: use relative path (dots)
    - Else: use file stem
    """
    if getattr(prog, "package", None):
        return prog.package  # declared
    if module_root:
        try:
            rel = src_path.resolve().relative_to(module_root.resolve())
            parts = list(rel.with_suffix("").parts)
            if parts:
                return ".".join(parts)
        except Exception:
            pass
    return src_path.stem

def _collect_public_exports(prog: Program, pkg: str) -> Dict[Tuple[str,str,str], object]:
    """
    Build (pkg, kind, name) -> node for public alias/schedule in a single Program.
    Accepts both Schedule nodes and transformer dicts {"type":"schedule_decl",...}.
    """
    out: Dict[Tuple[str,str,str], object] = {}
    # Aliases
    for s in prog.statements:
        if isinstance(s, Alias):
            if not getattr(s, "private", False):
                out[(pkg, "alias", s.name)] = s
    # Schedules (either dicts from transformer or Schedule nodes)
    for s in prog.statements:
        if isinstance(s, Schedule):
            if not getattr(s, "private", False):
                out[(pkg, "schedule", s.name)] = s
        elif isinstance(s, dict) and s.get("type") == "schedule_decl" and not s.get("private", False):
            name = s.get("name")
            if isinstance(name, str) and name.strip():
                out[(pkg, "schedule", name)] = Schedule(name=name, clauses=s.get("clauses", []) or [], private=False)
    return out

def _scan_hassl_files(path: Path) -> List[Path]:
    if path.is_file():
        return [path]
    return [Path(p) for p in glob.glob(str(path / "**" / "*.hassl"), recursive=True)]

def _module_to_path(module_root: Path, module: str) -> Path:
    return (module_root / Path(module.replace(".", "/"))).with_suffix(".hassl")

def _ensure_imports_loaded(programs, module_root: Path):
    """If imported packages aren't parsed yet, try to load their .hassl files from module_root."""
    # programs: List[tuple[Path, Program, str]]
    known_pkgs = {pkg for _, _, pkg in programs}
    added = True
    while added:
        added = False
        for _, prog, importer_pkg in list(programs):
            for imp in getattr(prog, "imports", []) or []:
                if not isinstance(imp, dict) or imp.get("type") != "import":
                    continue
                raw_mod = imp.get("module", "")
                if not raw_mod:
                    continue
                # resolve relative notation against the importing package
                abs_mod = _normalize_module(importer_pkg, raw_mod)
                if abs_mod in known_pkgs:
                    continue
                if not module_root:
                    continue
                candidate = _module_to_path(module_root, abs_mod)
                if candidate.exists():
                    print(f"[hasslc] Autoload candidate FOUND for '{abs_mod}': {candidate}")
                    with open(candidate, "r", encoding="utf-8") as f:
                        text = f.read()
                    p = parse_hassl(text)
                    # force package to declared or derived (declared will win)
                    # If the file declared a package, keep it. Otherwise, assign the resolved module id.
                    pkg_name = p.package or abs_mod
                    p.package = pkg_name
                    programs.append((candidate, p, pkg_name))
                    known_pkgs.add(pkg_name)
                    added = True
                else:
                    print(f"[hasslc] Autoload candidate MISS for '{abs_mod}': {candidate}")

def main():
    print("[hasslc] Using CLI file:", __file__)
    ap = argparse.ArgumentParser(prog="hasslc", description="HASSL Compiler")
    ap.add_argument("input", help="Input .hassl file OR directory")
    ap.add_argument("-o", "--out", default="./packages/out", help="Output directory root for HA package(s)")
    ap.add_argument("--module-root", default=None, help="Optional root to derive package names from paths")
    args = ap.parse_args()

    in_path = Path(args.input)
    out_root = Path(args.out)
    module_root = Path(args.module_root).resolve() if args.module_root else None

    src_files = _scan_hassl_files(in_path)
    if not src_files:
        raise SystemExit(f"[hasslc] No .hassl files found in {in_path}")

    # Pass 0: parse all and assign/derive package names
    programs: List[tuple[Path, Program, str]] = []
    for p in src_files:
        with open(p, "r", encoding="utf-8") as f:
            text = f.read()
        prog = parse_hassl(text)
        pkg_name = _derive_package_name(prog, p, module_root)
        try:
            prog.package = pkg_name
        except Exception:
            pass
        programs.append((p, prog, pkg_name))

    # auto-load any missing imports from --module_root
    _ensure_imports_loaded(programs, module_root)
    
    # Pass 1: collect public exports across all programs
    GLOBAL_EXPORTS: Dict[Tuple[str,str,str], object] = {}
    for path, prog, pkg in programs:
        GLOBAL_EXPORTS.update(_collect_public_exports(prog, pkg))

    # publish global exports to analyzer
    sem_analyzer.GLOBAL_EXPORTS = GLOBAL_EXPORTS

    # Pass 2: analyze each program with global view
    os.makedirs(out_root, exist_ok=True)
    all_ir = []
    for path, prog, pkg in programs:
        print(f"[hasslc] Parsing {path}  (package: {pkg})")
        print("[hasslc] AST:", json.dumps(prog.to_dict(), indent=2))
        ir = analyze(prog)
        print("[hasslc] IR:", json.dumps(ir.to_dict(), indent=2))
        all_ir.append((pkg, ir))

    # Emit: per package subdir
    for pkg, ir in all_ir:
        # One-level output: flatten dotted package id into a single directory name
        # e.g., home.addie.automations -> packages/out/home_addie_automations/
        pkg_dir = out_root / pkg.replace(".", "_")
        print(f"[hasslc] Output directory (flat): {pkg_dir}")
        os.makedirs(pkg_dir, exist_ok=True)
        ir_dict = ir.to_dict() if hasattr(ir, "to_dict") else ir
        codegen_generate(ir_dict, str(pkg_dir))
        emit_package(ir, str(pkg_dir))
        with open(pkg_dir / "DEBUG_ir.json", "w", encoding="utf-8") as dbg:
            dbg.write(json.dumps(ir.to_dict(), indent=2))
        print(f"[hasslc] Package written to {pkg_dir}")

    # Also drop a cross-project export table for debugging
    with open(out_root / "DEBUG_exports.json", "w", encoding="utf-8") as fp:
        printable = {f"{k[0]}::{k[1]}::{k[2]}": ("Alias" if isinstance(v, Alias) else "Schedule") for k, v in GLOBAL_EXPORTS.items()}
        json.dump(printable, fp, indent=2)
    print(f"[hasslc] Global exports index written to {out_root / 'DEBUG_exports.json'}")

if __name__ == "__main__":
    main()
import argparse
import os, json, glob
from pathlib import Path
from typing import Dict, Tuple, List
from .parser.loader import load_grammar_text
from .parser.transform import HasslTransformer
from .ast.nodes import Program, Alias, Schedule
from lark import Lark
from .semantics import analyzer as sem_analyzer
from .semantics.analyzer import analyze
from .codegen.package import emit_package
from .codegen import generate as codegen_generate

def parse_hassl(text: str) -> Program:
    grammar = load_grammar_text()
    parser = Lark(grammar, start="start", parser="lalr", maybe_placeholders=False)
    tree = parser.parse(text)
    program = HasslTransformer().transform(tree)
    return program

def _derive_package_name(prog: Program, src_path: Path, module_root: Path | None) -> str:
    """
    If the source did not declare `package`, derive one from the path:
    - If module_root is given and src_path is under it: use relative path (dots)
    - Else: use file stem
    """
    if getattr(prog, "package", None):
        return prog.package  # declared
    if module_root:
        try:
            rel = src_path.resolve().relative_to(module_root.resolve())
            parts = list(rel.with_suffix("").parts)
            if parts:
                return ".".join(parts)
        except Exception:
            pass
    return src_path.stem

def _collect_public_exports(prog: Program, pkg: str) -> Dict[Tuple[str,str,str], object]:
    """
    Build (pkg, kind, name) -> node for public alias/schedule in a single Program.
    Accepts both Schedule nodes and transformer dicts {"type":"schedule_decl",...}.
    """
    out: Dict[Tuple[str,str,str], object] = {}
    # Aliases
    for s in prog.statements:
        if isinstance(s, Alias):
            if not getattr(s, "private", False):
                out[(pkg, "alias", s.name)] = s
    # Schedules (either dicts from transformer or Schedule nodes)
    for s in prog.statements:
        if isinstance(s, Schedule):
            if not getattr(s, "private", False):
                out[(pkg, "schedule", s.name)] = s
        elif isinstance(s, dict) and s.get("type") == "schedule_decl" and not s.get("private", False):
            name = s.get("name")
            if isinstance(name, str) and name.strip():
                out[(pkg, "schedule", name)] = Schedule(name=name, clauses=s.get("clauses", []) or [], private=False)
    return out

def _scan_hassl_files(path: Path) -> List[Path]:
    if path.is_file():
        return [path]
    return [Path(p) for p in glob.glob(str(path / "**" / "*.hassl"), recursive=True)]

def _module_to_path(module_root: Path, module: str) -> Path:
    return (module_root / Path(module.replace(".", "/"))).with_suffix(".hassl")

def _ensure_imports_loaded(programs, module_root: Path):
    """If imported packages aren't parsed yet, try to load their .hassl files from module_root."""
    # programs: List[tuple[Path, Program, str]]
    known_pkgs = {pkg for _, _, pkg in programs}
    added = True
    while added:
        added = False
        for _, prog, _pkg in list(programs):
            for imp in getattr(prog, "imports", []) or []:
                if not isinstance(imp, dict) or imp.get("type") != "import":
                    continue
                mod = imp.get("module", "")
                if not mod or mod in known_pkgs:
                    continue
                if not module_root:
                    continue
                candidate = _module_to_path(module_root, mod)
                if candidate.exists():
                    print(f"[hasslc] Autoload candidate FOUND for '{mod}': {candidate}")
                    with open(candidate, "r", encoding="utf-8") as f:
                        text = f.read()
                    p = parse_hassl(text)
                    # force package to declared or derived (declared will win)
                    pkg_name = p.package or mod
                    p.package = pkg_name
                    programs.append((candidate, p, pkg_name))
                    known_pkgs.add(pkg_name)
                    added = True
                else:
                    print(f"[hasslc] Autoload candidate MISS for '{mod}': {candidate}")

def main():
    ap = argparse.ArgumentParser(prog="hasslc", description="HASSL Compiler")
    ap.add_argument("input", help="Input .hassl file OR directory")
    ap.add_argument("-o", "--out", default="./packages/out", help="Output directory root for HA package(s)")
    ap.add_argument("--module-root", default=None, help="Optional root to derive package names from paths")
    args = ap.parse_args()

    in_path = Path(args.input)
    out_root = Path(args.out)
    module_root = Path(args.module_root).resolve() if args.module_root else None

    src_files = _scan_hassl_files(in_path)
    if not src_files:
        raise SystemExit(f"[hasslc] No .hassl files found in {in_path}")

    # Pass 0: parse all and assign/derive package names
    programs: List[tuple[Path, Program, str]] = []
    for p in src_files:
        with open(p, "r", encoding="utf-8") as f:
            text = f.read()
        prog = parse_hassl(text)
        pkg_name = _derive_package_name(prog, p, module_root)
        try:
            prog.package = pkg_name
        except Exception:
            pass
        programs.append((p, prog, pkg_name))

    # auto-load any missing imports from --module_root
    _ensure_imports_loaded(programs, module_root)
    
    # Pass 1: collect public exports across all programs
    GLOBAL_EXPORTS: Dict[Tuple[str,str,str], object] = {}
    for path, prog, pkg in programs:
        GLOBAL_EXPORTS.update(_collect_public_exports(prog, pkg))

    # publish global exports to analyzer
    sem_analyzer.GLOBAL_EXPORTS = GLOBAL_EXPORTS

    # Pass 2: analyze each program with global view
    os.makedirs(out_root, exist_ok=True)
    all_ir = []
    for path, prog, pkg in programs:
        print(f"[hasslc] Parsing {path}  (package: {pkg})")
        print("[hasslc] AST:", json.dumps(prog.to_dict(), indent=2))
        ir = analyze(prog)
        print("[hasslc] IR:", json.dumps(ir.to_dict(), indent=2))
        all_ir.append((pkg, ir))

    # Emit: per package subdir
    for pkg, ir in all_ir:
        pkg_dir = out_root / pkg.replace(".", "_")
        os.makedirs(pkg_dir, exist_ok=True)
        ir_dict = ir.to_dict() if hasattr(ir, "to_dict") else ir
        codegen_generate(ir_dict, str(pkg_dir))
        emit_package(ir, str(pkg_dir))
        with open(pkg_dir / "DEBUG_ir.json", "w", encoding="utf-8") as dbg:
            dbg.write(json.dumps(ir.to_dict(), indent=2))
        print(f"[hasslc] Package written to {pkg_dir}")

    # Also drop a cross-project export table for debugging
    with open(out_root / "DEBUG_exports.json", "w", encoding="utf-8") as fp:
        printable = {f"{k[0]}::{k[1]}::{k[2]}": ("Alias" if isinstance(v, Alias) else "Schedule") for k, v in GLOBAL_EXPORTS.items()}
        json.dump(printable, fp, indent=2)
    print(f"[hasslc] Global exports index written to {out_root / 'DEBUG_exports.json'}")

if __name__ == "__main__":
    main()
