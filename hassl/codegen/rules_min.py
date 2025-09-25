import os
from pathlib import Path
import yaml
import re

def _gate_entity(rule_name: str) -> str:
    slug = rule_name.lower().replace(" ", "_")
    return f"input_boolean.hassl_gate__{slug}"

def _ensure_gate_defaults(output_items: list, rule_name: str):
    # We don’t write a separate helpers.yaml here; we just inject an initial step
    # into the very first automation to turn the gate on at startup.
    # If you prefer proper package helpers, we can generate a helpers.yaml instead.
    pass  # placeholder if you want to later materialize helpers.yaml

def _dur_to_hms(s):
    m = re.fullmatch(r"(\d+)(ms|s|m|h|d)", str(s).strip())
    if not m:
        return "00:00:00"
    n = int(m.group(1)); unit = m.group(2)
    seconds = {
        "ms": 0,        # HA doesn't really use ms in "for" — treat as 0
        "s": n,
        "m": n * 60,
        "h": n * 3600,
        "d": n * 86400,
    }[unit]
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def _expr_to_template(node):
    # compile boolean/compare AST to Jinja template returning true/false
    def j(n):
        if isinstance(n, dict) and "op" in n:
            op = n["op"]
            if op == "not":
                return f"(not {j(n['value'])})"
            if op == "and":
                return f"(({j(n['left'])}) and ({j(n['right'])}))"
            if op == "or":
                return f"(({j(n['left'])}) or ({j(n['right'])}))"
            # comparisons & equals
            left = n.get("left"); right = n.get("right")
            if isinstance(left, str) and "." in left:
                l = f"states('{left}')"
            else:
                l = repr(left)
            if isinstance(right, str) and right in ("on", "off"):
                # state equality checks
                if n["op"] == "==":
                    return f"(is_state('{left}','{right}'))"
                if n["op"] == "!=":
                    return f"(not is_state('{left}','{right}'))"
                # fallthrough: compare strings
            if isinstance(right, (int, float)):
                # numeric compare on left entity
                if isinstance(left, str) and "." in left:
                    l = f"({l}|float(0))"
                    r = f"{right}"
                    return f"({l} {op} {r})"
            # generic fallback
            r = repr(right)
            return f"({l} {op} {r})"
        # bare operand: entity -> state on
        if isinstance(n, str) and "." in n:
            return f"(is_state('{n}','on'))"
        # literals
        if isinstance(n, (int, float)):
            return f"({n} != 0)"
        if isinstance(n, str):
            # treat bare string (e.g., alias that should've been resolved) as truthy
            return "(true)"
        return "(true)"
    return "{{ " + j(node) + " }}"

def _slug(s):
    return s.lower().replace(" ", "_")

def _entity_ids_in_expr(expr):
    ids = set()
    if isinstance(expr, dict):
        for k, v in expr.items():
            ids.update(_entity_ids_in_expr(v))
    elif isinstance(expr, list):
        for v in expr:
            ids.update(_entity_ids_in_expr(v))
    elif isinstance(expr, str):
        if "." in expr and all(part for part in expr.split(".")):
            ids.add(expr)
    return ids

def _condition_to_ha(cond):
    def cv(node):
        if isinstance(node, dict) and "op" in node:
            op = node["op"]
            if op in ("and", "or"):
                key = "and" if op == "and" else "or"
                return {"condition": key, "conditions": [cv(node["left"]), cv(node["right"])]}
            if op == "not":
                return {"condition": "not", "conditions": [cv(node["value"])]}
            left = node.get("left")
            right = node.get("right")
            if op == "==":
                eid = left if isinstance(left, str) else str(left)
                val = right
                if isinstance(val, str) and val in ("on", "off"):
                    return {"condition": "state", "entity_id": eid, "state": val}
                else:
                    return {"condition": "template", "value_template": f"{{{{ states('{eid}')|float(0) == {val} }}}}"}
            if op in ("<", ">", "<=", ">="):
                eid = left if isinstance(left, str) else str(left)
                return {"condition": "template", "value_template": f"{{{{ states('{eid}')|float(0) {op} {right} }}}}"}
        if isinstance(node, str):
            if "." in node:
                return {"condition": "state", "entity_id": node, "state": "on"}
        return {"condition": "template", "value_template": "true"}
    expr = cond.get("expr", cond)
    return cv(expr)

def generate_rules(ir, outdir):
    rules = ir.get("rules", [])
    if not rules:
        return
    Path(outdir).mkdir(parents=True, exist_ok=True)

    bundled = []
    for rule in rules:
        rname = rule["name"]
        for idx, clause in enumerate(rule["clauses"]):
            cname = f"{_slug(rname)}__{idx+1}"
            expr = clause["condition"].get("expr", {})
            actions = clause["actions"]
            entities = sorted(_entity_ids_in_expr(expr))
            triggers = [{"platform": "state", "entity_id": e} for e in entities] or [{"platform":"time","at":"00:00:00"}]
            cond_ha = _condition_to_ha(clause["condition"])

            act_list = []
            for act in actions:
                if act["type"] == "assign":
                    eid = act["target"]
                    if "." not in eid:
                        eid = eid
                    service = "turn_on" if act["state"] == "on" else "turn_off"
                    act_list.append({"service": f"homeassistant.{service}", "target": {"entity_id": eid}})
                elif act["type"] == "attr_assign":
                    eid = act["entity"]; attr = act["attr"]; val = act["value"]
                    if attr == "brightness":
                        act_list.append({"service": "light.turn_on", "target": {"entity_id": eid}, "data": {"brightness": val}})
                    else:
                        act_list.append({"service": "homeassistant.turn_on", "target": {"entity_id": eid}, "data": {attr: val}})
                elif act["type"] == "wait":
                    # Use a template trigger that becomes true when the wait condition expr is true for duration
                    cond_expr = act["condition"].get("expr", act["condition"])
                    vt = _expr_to_template(cond_expr)
                    act_list.append({
                        "wait_for_trigger": [{
                            "platform": "template",
                            "value_template": vt,
                            "for": _dur_to_hms(act["for"])
                        }]
                    })
                    # then perform inner action
                    inner = act["then"]
                    if inner["type"] == "assign":
                        eid = inner["target"]
                        service = "turn_on" if inner["state"] == "on" else "turn_off"
                        act_list.append({"service": f"homeassistant.{service}", "target": {"entity_id": eid}})
                elif act["type"] == "rule_ctrl":
                    target_rule = act["rule"]
                    gate = _gate_entity(target_rule)
                    if act["op"] == "disable":
                        dur = act.get("for")
                        steps = [
                            {"service": "input_boolean.turn_off", "target": {"entity_id": gate}},
                        ]
                        if dur:
                            steps.append({"delay": _dur_to_hms(dur)})
                            steps.append({"service": "input_boolean.turn_on", "target": {"entity_id": gate}})
                        act_list.extend(steps)
                    elif act["op"] == "enable":
                        act_list.append({"service": "input_boolean.turn_on", "target": {"entity_id": gate}})
                    else:
                        # fallback: log unknown op
                        act_list.append({"service": "logbook.log", "data": {"name": "HASSL", "message": f"{act['op']} rule {target_rule} {act.get('for') or act.get('until') or ''}".strip()}})
                else:
                    act_list.append({"delay": "00:00:01"})

            # Inject gate condition
            gate_cond = {
                "condition": "state",
                "entity_id": _gate_entity(rname),
                "state": "on",
            }

            auto = {
                "id": cname,
                "alias": f"HASSL {rname} #{idx+1}",
                "mode": "restart",
                "trigger": triggers,
                "condition": [cond_ha],
                "action": act_list
            }
            bundled.append(auto)

    out_path = Path(outdir) / "rules__bundled.yaml"
    with open(out_path, "w") as f:
        yaml.safe_dump(bundled, f, sort_keys=False)

    # Optional: write a helpers file creating the gate booleans
    gates = sorted({_gate_entity(r["name"]) for r in rules})
    if gates:
        helpers = {"input_boolean": {}}
        for g in gates:
            helpers["input_boolean"][g.split(".", 1)[1]] = {
                "name": f"HASSL Gate {g.split('__', 1)[-1]}",
                "icon": "mdi:shield-check",
            }
        with open(Path(outdir) / "helpers.yaml", "w") as f:
            yaml.safe_dump(helpers, f, sort_keys=False)

    return str(out_path)
