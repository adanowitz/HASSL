from pathlib import Path
from .rules_min import generate_rules

def generate(ir_obj, outdir):
    Path(outdir).mkdir(parents=True, exist_ok=True)
    generate_rules(ir_obj, outdir)
    return True
