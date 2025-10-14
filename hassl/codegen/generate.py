# Minimal wrapper so CLI can import `generate`
from . import rules_min

def generate(ir_dict, outdir: str):
    # delegate to the tested minimal emitter
    return rules_min.generate_rules(ir_dict, outdir)
