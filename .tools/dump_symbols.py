import ast, pathlib, hashlib, sys

root = pathlib.Path("hassl")
files = list(root.rglob("*.py")) + list(root.rglob("*.lark"))

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]

for p in sorted(files):
    h = sha(p)
    print(f"\n== {p} [{h}] ==")
    if p.suffix == ".lark":
        txt = p.read_text(errors="ignore")
        print("  (lark) lines:", txt.count("\n")+1, "bytes:", len(txt))
        # quick skim of top-level rules
        for line in txt.splitlines():
            if line.strip().endswith(":") and not line.strip().startswith("//"):
                print("   rule:", line.strip().split(":")[0])
        continue
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"))
    except Exception as e:
        print("  [parse error]", e)
        continue
    classes = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
    funcs   = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
    assigns = [n.targets[0].id for n in tree.body
               if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)]
    print("  classes:", ", ".join(classes) or "-")
    print("  funcs  :", ", ".join(funcs) or "-")
    print("  assigns:", ", ".join(assigns) or "-")
