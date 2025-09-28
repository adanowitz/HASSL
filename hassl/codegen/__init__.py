from pathlib import Path
from .package import emit_package
from .rules_min import generate_rules

def generate(ir_obj, outdir):
    """
    Orchestrate codegen in a merge-safe order:
      1) emit_package: writes/merges helpers, scripts, and sync automations
      2) generate_rules: writes rules automations & merges gate booleans into helpers.yaml
+    """
    Path(outdir).mkdir(parents=True, exist_ok=True)

    # 1) Sync & helpers first (merge-safe via yaml_emit._dump_yaml)
    try:
        emit_package(ir_obj if hasattr(ir_obj, "syncs") else ir_obj, outdir)
    except Exception:
        # keep going to still emit rules even if sync pass fails
        pass

    # 2) Rules last (adds gate booleans; also merge-safe)
    generate_rules(ir_obj if isinstance(ir_obj, dict) else getattr(ir_obj, "to_dict", lambda: ir_obj)(), outdir)
    return True
