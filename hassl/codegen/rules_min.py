import os, re, yaml
from pathlib import Path

def _slug(s: str) -> str:
    return str(s).lower().replace(" ", "_")

def _safe_entity(e: str) -> str:
    # safe for helper entity ids (dots → underscores)
    return str(e).replace(".", "_")

def _gate_entity(rule_name: str) -> str:
    # single underscores only (HA slug rules)
    slug = _slug(rule_name)
    return f"input_boolean.hassl_gate_{slug}"

def _rule_ctx_key(rule_name: str, entity_id: str) -> str:
    # input_text key to hold the last context for a rule → entity action
    return f"hassl_ctx_rule_{_slug(rule_name)}_{_safe_entity(entity_id)}"

def _entity_ctx_key(entity_id: str) -> str:
    # input_text key to hold the last context for a plain entity action
    return f"hassl_ctx_{_safe_entity(entity_id)}"

def _entity_ids_in_expr(expr):
    ids = set()
    if isinstance(expr, dict):
        for _, v in expr.items():
            ids.update(_entity_ids_in_expr(v))
    elif isinstance(expr, list):
        for v in expr:
            ids.update(_entity_ids_in_expr(v))
    elif isinstance(expr, str):
        if "." in expr and all(part for part in expr.split(".")):
            ids.add(expr)
    return ids

def _dur_to_hms(s):
    s = str(s).strip()
    m = re.fullmatch(r"(\d+)(ms|s|m|h|d)", s)
    if not m:
        return "00:00:00"
    n = int(m.group(1)); unit = m.group(2)
    seconds = {"ms": 0, "s": n, "m": n*60, "h": n*3600, "d": n*86400}[unit]
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def _expr_to_template(node):
    def j(n):
        if isinstance(n, dict) and "op" in n:
            op = n["op"]
            if op == "not":
                return f"(not {j(n['value'])})"
            if op == "and":
                return f"(({j(n['left'])}) and ({j(n['right'])}))"
            if op == "or":
                return f"(({j(n['left'])}) or ({j(n['right'])}))"
            left = n.get("left"); right = n.get("right")
            if isinstance(left, str) and "." in left:
                l = f"states('{left}')"
            else:
                l = repr(left)
            if isinstance(right, str) and right in ("on", "off"):
                if n["op"] == "==":
                    return f"(is_state('{left}','{right}'))"
                if n["op"] == "!=":
                    return f"(not is_state('{left}','{right}'))"
            if isinstance(right, (int, float)):
                if isinstance(left, str) and "." in left:
                    l = f"({l}|float(0))"
                    r = f"{right}"
                    return f"({l} {op} {r})"
            r = repr(right)
            return f"({l} {op} {r})"
        if isinstance(n, str) and "." in n:
            return f"(is_state('{n}','on'))"
        if isinstance(n, (int, float)):
            return f"({n} != 0)"
        return "(true)"
    return "{{ " + j(node) + " }}"

def _condition_to_ha(cond):
    def cv(node):
        if isinstance(node, dict) and "op" in node:
            op = node["op"]
            if op in ("and", "or"):
                key = "and" if op == "and" else "or"
                return {"condition": key, "conditions": [cv(node["left"]), cv(node["right"])]}
            if op == "not":
                return {"condition": "not", "conditions": [cv(node["value"])]}
            left = node.get("left"); right = node.get("right")
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
        if isinstance(node, str) and "." in node:
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
    # collect helper keys we must ensure exist
    ctx_inputs = set()

    # ---- build automations ----
    for rule in rules:
        rname = rule["name"]
        gate = _gate_entity(rname)
        for idx, clause in enumerate(rule["clauses"]):
            cname = f"{_slug(rname)}__{idx+1}"
            expr = clause["condition"].get("expr", {})
            actions = clause["actions"]
            entities = sorted(_entity_ids_in_expr(expr))
            triggers = [{"platform": "state", "entity_id": e} for e in entities] or [{"platform": "time", "at": "00:00:00"}]
            cond_ha = _condition_to_ha(clause["condition"])
            gate_cond = {"condition": "state", "entity_id": gate, "state": "on"}

            # --- NOT_BY guard (qualifier) ---
            qual = clause.get("condition", {}).get("not_by")
            qual_cond = None
            if qual:
                ent0 = entities[0] if entities else None
                if ent0:
                    if isinstance(qual, dict) and "rule" in qual:
                        rname_qual = _slug(str(qual["rule"]))
                        it_key = _rule_ctx_key(rname_qual, ent0)
                        ctx_inputs.add(it_key)
                        qual_cond = {
                            "condition": "template",
                            "value_template": "{{ trigger.to_state.context.parent_id != "
                                              "states('input_text.%s') }}" % it_key
                        }
                    else:
                        it_key = _entity_ctx_key(ent0)
                        ctx_inputs.add(it_key)
                        qual_cond = {
                            "condition": "template",
                            "value_template": "{{ trigger.to_state.context.parent_id != "
                                              "states('input_text.%s') }}" % it_key
                        }

            act_list = []
            for act in actions:
                if act["type"] == "assign":
                    eid = act["target"]
                    service = "turn_on" if act["state"] == "on" else "turn_off"
                    act_list.append({"service": f"homeassistant.{service}", "target": {"entity_id": eid}})
                elif act["type"] == "attr_assign":
                    eid = act["entity"]; attr = act["attr"]; val = act["value"]
                    if attr == "brightness":
                        act_list.append({"service": "light.turn_on", "target": {"entity_id": eid}, "data": {"brightness": val}})
                    else:
                        act_list.append({"service": "homeassistant.turn_on", "target": {"entity_id": eid}, "data": {attr: val}})
                elif act["type"] == "wait":
                    cond_expr = act["condition"].get("expr", act["condition"])
                    vt = _expr_to_template(cond_expr)
                    act_list.append({"wait_for_trigger": [{"platform": "template", "value_template": vt, "for": _dur_to_hms(act["for"])}]})
                    inner = act["then"]
                    if inner["type"] == "assign":
                        eid = inner["target"]
                        service = "turn_on" if inner["state"] == "on" else "turn_off"
                        act_list.append({"service": f"homeassistant.{service}", "target": {"entity_id": eid}})
                elif act["type"] == "rule_ctrl":
                    target_rule = act["rule"]
                    gate_target = _gate_entity(target_rule)
                    if act["op"] == "disable":
                        dur = act.get("for")
                        steps = [{"service": "input_boolean.turn_off", "target": {"entity_id": gate_target}}]
                        if dur:
                            steps.append({"delay": _dur_to_hms(dur)})
                            steps.append({"service": "input_boolean.turn_on", "target": {"entity_id": gate_target}})
                        act_list.extend(steps)
                    elif act["op"] == "enable":
                        act_list.append({"service": "input_boolean.turn_on", "target": {"entity_id": gate_target}})
                    else:
                        act_list.append({"service": "logbook.log", "data": {"name": "HASSL", "message": f"{act['op']} rule {target_rule}"}})
                else:
                    act_list.append({"delay": "00:00:01"})

            conds = [gate_cond, cond_ha]
            if qual_cond:
                conds.append(qual_cond)

            auto = {
                "id": cname,
                "alias": f"HASSL {rname} #{idx+1}",
                "mode": "restart",
                "trigger": triggers,
                "condition": conds,
                "action": act_list
            }
            bundled.append(auto)

    out_path = Path(outdir) / "rules__bundled.yaml"
    with open(out_path, "w") as f:
        # packages expect a mapping, not a bare list
        yaml.safe_dump({"automation": bundled}, f, sort_keys=False)

    helpers_path = Path(outdir) / "helpers.yaml"

    # 1) Build our gate booleans from rule names & rule_ctrl targets
    gate_names = {rule["name"] for rule in rules}
    for rule in rules:
        for clause in rule.get("clauses", []):
            for act in clause.get("actions", []):
                if act.get("type") == "rule_ctrl" and "rule" in act:
                    gate_names.add(act["rule"])

    gates = {f"input_boolean.hassl_gate_{_slug(name)}"
             for name in gate_names if isinstance(name, str) and name.strip()}

    # 2) Load existing helpers (if any), or start with skeleton
    if helpers_path.exists():
        try:
            existing = yaml.safe_load(helpers_path.read_text()) or {}
        except Exception:
            existing = {}
    else:
        existing = {}

    merged = {
        "input_text": existing.get("input_text", {}) or {},
        "input_boolean": existing.get("input_boolean", {}) or {},
        "input_number": existing.get("input_number", {}) or {},
    }

    # 3) Merge our gate booleans
    for g in sorted(gates):
        key = g.split(".", 1)[1]
        merged["input_boolean"][key] = {
            "name": f"HASSL Gate {key}",
            "initial": "on",
        }

    # 4) Ensure input_text helpers referenced by NOT_BY guards exist
    for it_key in sorted(ctx_inputs):
        merged["input_text"].setdefault(it_key, {
            "name": f"HASSL Ctx {it_key}",
            "max": 64
        })

    # 5) Write back with friendly header
    header = "# Generated by HASSL codegen\n"
    helpers_yaml = yaml.safe_dump(merged, sort_keys=False)
    helpers_path.write_text(header + helpers_yaml)

    return str(out_path)
