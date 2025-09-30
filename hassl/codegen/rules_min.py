import os, re, yaml
from pathlib import Path

# ----------------- slug helpers -----------------
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

def _schedule_bool(name: str) -> str:
    return f"input_boolean.hassl_schedule_{_slug(name)}"

def _rule_schedule_bool(rule_name: str) -> str:
    return f"input_boolean.hassl_schedule_rule_{_slug(rule_name)}"


# ----------------- utilities -----------------
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


# ----------------- schedule helpers -----------------
def _parse_offset(off: str) -> str:
    """Convert +15m / -10s / +2h to +/-HH:MM:SS string for HA sun offset"""
    if not off: return "00:00:00"
    m = re.fullmatch(r"([+-])(\d+)(ms|s|m|h|d)", str(off).strip())
    if not m: return "00:00:00"
    sign, n, unit = m.group(1), int(m.group(2)), m.group(3)
    seconds = {"ms": 0, "s": n, "m": n*60, "h": n*3600, "d": n*86400}[unit]
    h = seconds // 3600
    m_ = (seconds % 3600) // 60
    s = seconds % 60
    return f"{sign}{h:02d}:{m_:02d}:{s:02d}"

def _time_trigger_from_spec(ts):
    """Return a HA trigger (or list of triggers) from a time_spec dict or entity string."""
    if isinstance(ts, dict):
        kind = ts.get("kind")
        if kind == "clock":
            hhmm = ts.get("value", "00:00")
            hh, mm = hhmm.split(":")
            at = f"{int(hh):02d}:{int(mm):02d}:00"
            return {"platform": "time", "at": at}
        if kind == "sun":
            event = ts.get("event", "sunrise")
            off = _parse_offset(ts.get("offset", "0s"))
            trig = {"platform": "sun", "event": event}
            if off and off != "00:00:00":
                trig["offset"] = off
            return trig
        # Fallback – treat dict with stringified entity
        val = ts.get("value")
        if isinstance(val, str) and "." in val:
            return {"platform": "state", "entity_id": val, "to": "on"}
    elif isinstance(ts, str) and "." in ts:
        return {"platform": "state", "entity_id": ts, "to": "on"}
    # default: 00:00
    return {"platform": "time", "at": "00:00:00"}

def _end_trigger_from_spec(ts):
    """Like _time_trigger_from_spec but choose the natural 'end' event:
       - clock/sun → same kind at end time
       - entity → to: 'off' """
    if isinstance(ts, dict):
        kind = ts.get("kind")
        if kind == "clock":
            hhmm = ts.get("value", "00:00")
            hh, mm = hhmm.split(":")
            at = f"{int(hh):02d}:{int(mm):02d}:00"
            return {"platform": "time", "at": at}
        if kind == "sun":
            event = ts.get("event", "sunset")
            off = _parse_offset(ts.get("offset", "0s"))
            trig = {"platform": "sun", "event": event}
            if off and off != "00:00:00":
                trig["offset"] = off
            return trig
        val = ts.get("value")
        if isinstance(val, str) and "." in val:
            return {"platform": "state", "entity_id": val, "to": "off"}
    elif isinstance(ts, str) and "." in ts:
        return {"platform": "state", "entity_id": ts, "to": "off"}
    return {"platform": "time", "at": "00:00:00"}

def _emit_schedule_clause_automations(name: str, clause: dict, autos: list):
    """
    For a schedule clause:
      {"type":"schedule_clause","op":"enable|disable","from": <ts>, ["to": <ts>] | ["until": <ts>]}
    we emit two one-shot automations (start/end) that set input_boolean.hassl_schedule_<name>
    """
    bool_e = _schedule_bool(name)
    op = clause.get("op", "enable").lower()

    start_ts = clause.get("from")
    end_ts = clause.get("to", clause.get("until"))

    start_trig = _time_trigger_from_spec(start_ts) if start_ts is not None else None
    end_trig   = _end_trigger_from_spec(end_ts)   if end_ts   is not None else None

    # At start: set according to op
    if start_trig:
        autos.append({
            "alias": f"HASSL schedule {name} start",
            "mode": "single",
            "trigger": [start_trig],
            "action": [{
                "service": "input_boolean.turn_on" if op == "enable" else "input_boolean.turn_off",
                "target": {"entity_id": bool_e}
            }]
        })
    # At end: invert
    if end_trig:
        autos.append({
            "alias": f"HASSL schedule {name} end",
            "mode": "single",
            "trigger": [end_trig],
            "action": [{
                "service": "input_boolean.turn_off" if op == "enable" else "input_boolean.turn_on",
                "target": {"entity_id": bool_e}
            }]
        })

def _collect_schedules(ir: dict):
    """
    Return:
      declared: dict name -> list[clause]
      inline_by_rule: dict rule_name -> list[clause]
      use_by_rule: dict rule_name -> list[name]
    NOTE: matches analyzer that emits:
      ir["schedules"]               == dict{name: [clauses]}
      rule["schedule_uses"]         == list[str]
      rule["schedules_inline"]      == list[clause dict]
    """
    declared = {}
    inline_by_rule = {}
    use_by_rule = {}

    # top-level declared schedules
    schedules_obj = ir.get("schedules") or {}
    if isinstance(schedules_obj, dict):
        declared = {str(k): (v or []) for k, v in schedules_obj.items()}

    # per-rule data
    for rule in ir.get("rules", []):
        rname = rule.get("name")
        if not rname: 
            continue
        if "schedule_uses" in rule and isinstance(rule["schedule_uses"], list):
            use_by_rule[rname] = [str(n) for n in rule["schedule_uses"]]
        if "schedules_inline" in rule and isinstance(rule["schedules_inline"], list):
            inline_by_rule[rname] = [c for c in rule["schedules_inline"] if isinstance(c, dict)]

    return declared, inline_by_rule, use_by_rule


# ----------------- main generate -----------------
def generate_rules(ir, outdir):
    rules = ir.get("rules", [])
    if not rules:
        return
    Path(outdir).mkdir(parents=True, exist_ok=True)

    bundled = []
    # collect helper keys we must ensure exist
    ctx_inputs = set()

    # --- schedules collection ---
    declared_schedules, inline_by_rule, use_by_rule = _collect_schedules(ir)

    # Ensure helpers for declared schedule booleans (default off)
    schedule_helpers = {
        _schedule_bool(nm).split(".",1)[1]: {
            "name": f"HASSL Schedule {nm}", "initial": "off"
        } for nm in declared_schedules.keys()
    }

    # Build schedule automations for declared schedules
    for nm, clauses in declared_schedules.items():
        for cl in clauses:
            if isinstance(cl, dict) and cl.get("type") == "schedule_clause":
                _emit_schedule_clause_automations(nm, cl, bundled)

    # ---- build automations (rules) ----
    for rule in rules:
        rname = rule["name"]
        gate = _gate_entity(rname)

        # gather schedule conditions for this rule
        cond_schedule_entities = []

        # 1) named schedules used by this rule
        for nm in use_by_rule.get(rname, []) or []:
            cond_schedule_entities.append(_schedule_bool(nm))

        # 2) inline schedule clauses → compile into a per-rule schedule boolean
        inline_clauses = inline_by_rule.get(rname, []) or []
        rule_sched_bool = None
        if inline_clauses:
            rule_sched_bool = _rule_schedule_bool(rname)
            # ensure helper exists (default off)
            schedule_helpers[rule_sched_bool.split(".",1)[1]] = {
                "name": f"HASSL Schedule (rule) {rname}",
                "initial": "off"
            }
            # emit start/end automations for the rule's schedule gate
            # name is "rule_<slug>" so _schedule_bool(name) == _rule_schedule_bool(rname)
            inline_name = f"rule_{_slug(rname)}"
            for cl in inline_clauses:
                if isinstance(cl, dict) and cl.get("type") == "schedule_clause":
                    _emit_schedule_clause_automations(inline_name, cl, bundled)

            cond_schedule_entities.append(rule_sched_bool)

        # Now process each 'if' clause
        for idx, clause in enumerate(rule["clauses"]):
            # Each clause is {"condition": ..., "actions": [...]}
            cname = f"{_slug(rname)}__{idx+1}"
            expr = clause["condition"].get("expr", {})
            actions = clause["actions"]
            entities = sorted(_entity_ids_in_expr(expr))
            triggers = [{"platform": "state", "entity_id": e} for e in entities] or [{"platform": "time", "at": "00:00:00"}]
            cond_ha = _condition_to_ha(clause["condition"])
            gate_cond = {"condition": "state", "entity_id": gate, "state": "on"}

            # schedule gate conditions (all must be ON)
            sched_conds = [{"condition":"state","entity_id": e,"state":"on"} for e in cond_schedule_entities]

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

            conds = [gate_cond] + sched_conds + [cond_ha]
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

    # 5) Add schedule booleans (declared + per-rule)
    for key, obj in schedule_helpers.items():
        merged["input_boolean"].setdefault(key, obj)

    # 6) Write back with friendly header
    header = "# Generated by HASSL codegen\n"
    helpers_yaml = yaml.safe_dump(merged, sort_keys=False)
    helpers_path.write_text(header + helpers_yaml)

    return str(out_path)
