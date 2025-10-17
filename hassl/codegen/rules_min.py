import os, re, yaml
from pathlib import Path
from hassl.semantics import analyzer as sem_analyzer

# ----------------- slug helpers -----------------
def _slug(s: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', str(s).lower()).strip('_')

def _safe_entity(e: str) -> str:
    # safe for helper entity ids (dots → underscores)
    return str(e).replace(".", "_")

def _dedup_dicts(seq):
    """Stable de-dup for lists of small dicts (e.g., triggers)."""
    out, seen = [], set()
    for d in seq:
        key = tuple(sorted((k, str(v)) for k, v in d.items()))
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out

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

def _schedule_sensor(name: str, pkg: str) -> str:
    """
    Match the entity_id that package.py's ScheduleRegistry emits:
      binary_sensor.hassl_schedule_{pkg}_{name}_active
    NOTE: 'name' here is the *base* schedule name (without package prefix).
    """
    base = str(name).split(".")[-1]
    return f"binary_sensor.hassl_schedule_{_slug(pkg)}_{_slug(base)}_active"

def _alias_map(ir: dict) -> dict:
    """
    Build alias -> entity_id mapping from the current IR and any imported exports.
    """
    amap = dict(ir.get("aliases", {}) or {})
    # Pull in imported public aliases from GLOBAL_EXPORTS
    for (pkg_name, kind, alias_name), obj in getattr(sem_analyzer, "GLOBAL_EXPORTS", {}).items():
        if kind != "alias":
            continue
        target = getattr(obj, "entity", None)
        if target is None and isinstance(obj, dict):
            target = obj.get("entity")
        if target:
            amap[str(alias_name)] = str(target)
    return amap

def _resolve_name(name: str, aliases: dict) -> str:
    """Return resolved entity_id if 'name' is an alias, else the original string."""
    return aliases.get(str(name), str(name))

def _resolve_expr_aliases(node, aliases: dict):
    """
    Walk the analyzer expr tree and replace bare alias tokens with entity_ids
    so triggers/conditions are built against real entities.
    """
    if isinstance(node, dict) and "op" in node:
        op = node["op"]
        if op in ("and", "or"):
            return {
                "op": op,
                "left": _resolve_expr_aliases(node["left"], aliases),
                "right": _resolve_expr_aliases(node["right"], aliases),
            }
        if op == "not":
            return {"op": "not", "value": _resolve_expr_aliases(node["value"], aliases)}
        # binary compare
        left = _resolve_expr_aliases(node.get("left"), aliases)
        right = _resolve_expr_aliases(node.get("right"), aliases)
        return {"op": op, "left": left, "right": right}
    if isinstance(node, str):
        # If it isn't already an entity_id (no dot) and matches an alias, swap it
        if "." not in node and node in aliases:
            return aliases[node]
        return node
    if isinstance(node, list):
        return [_resolve_expr_aliases(x, aliases) for x in node]
    return node

def _pkg_slug(outdir: str) -> str:
    base = os.path.basename(os.path.abspath(outdir))
    s = re.sub(r'[^a-z0-9]+', '_', base.lower()).strip('_')
    return s or "pkg"

def _ctx_key_and_entity(entity_id: str, attr: str | None = None):
    """
    Build the input_text key (without domain), its full entity_id, and a
    nice display label for stamping the parent context id used by NOT_BY
    guards.
    """
    if attr:
        key = f"hassl_ctx_{_safe_entity(entity_id)}_{attr}"
        label = f"{entity_id} {attr}"
    else:
        key = f"hassl_ctx_{_safe_entity(entity_id)}"
        label = f"{entity_id}"
    return key, f"input_text.{key}", label

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

def _kelvin_to_mireds(k):
    """Convert Kelvin → mireds (rounded, clamped >=1)."""
    try:
        v = float(k)
        if v <= 0:
            return 153  # safe-ish default (~6500K) if bad input
        m = int(round(1000000.0 / v))
        return m if m >= 1 else 1
    except Exception:
        return 153

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


# ----------------- schedule → conditions -----------------
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

def _clock_between_cond(hhmm_start: str, hhmm_end: str):
    """
    Build a HA template condition that is true when current time (HH:MM) is within [start, end),
    correctly handling wrap-past-midnight (e.g., 22:00..06:00).
    """
    return {
        "condition": "template",
        "value_template": (
            "{% set now_s = now().strftime('%H:%M') %}"
            f"{{% set s = '{hhmm_start}' %}}{{% set e = '{hhmm_end}' %}}"
            "{{ (s < e and (now_s >= s and now_s < e)) "
            "or (s >= e and (now_s >= s or now_s < e)) }}"
        )
    }

def _sun_edge_cond(edge: str, ts: dict):
    """
    Build a HA 'sun' condition edge ('after' or 'before') from a sun time_spec dict.
    """
    event = ts.get("event", "sunrise")
    off = _parse_offset(ts.get("offset", "0s"))
    cond = {"condition": "sun", edge: event}
    if off and off != "00:00:00":
        cond["offset"] = off
    return cond

def _window_condition_from_specs(start_ts, end_ts):
    """
    Return a HA condition dict that is true when 'now' is inside the window [start, end),
    supporting clock and sun specs, including wrap across midnight.
    Mixed (clock ↔ sun) windows fall back to a simple minute template using next events.
    """
    # Clock → Clock
    if isinstance(start_ts, dict) and start_ts.get("kind") == "clock" and \
       isinstance(end_ts, dict) and end_ts.get("kind") == "clock":
        s = start_ts.get("value", "00:00")
        e = end_ts.get("value", "00:00")
        return _clock_between_cond(s, e)

    # Sun → Sun
    if isinstance(start_ts, dict) and start_ts.get("kind") == "sun" and \
       isinstance(end_ts, dict) and end_ts.get("kind") == "sun":
        # If the window wraps overnight (e.g., sunset..sunrise), we use OR(after start, before end)
        # Otherwise AND(after start, before end)
        after_start = _sun_edge_cond("after", start_ts)
        before_end  = _sun_edge_cond("before", end_ts)
        wrap = (start_ts.get("event") == "sunset" and end_ts.get("event") == "sunrise")
        if wrap:
            return {"condition": "or", "conditions": [after_start, before_end]}
        return {"condition": "and", "conditions": [after_start, before_end]}

    # Mixed (clock ↔ sun) → fall back to a template that compares minute-of-day
    return {
        "condition": "template",
        "value_template": (
            "{% set now_m = now().hour*60 + now().minute %}"
            "{% macro tod(hhmm) %}{% set h=hhmm[0:2]|int %}{% set m=hhmm[3:5]|int %}{{ h*60+m }}{% endmacro %}"
            "{% set s_m = ("
            "{% if start.kind == 'clock' %}"
            "  tod(start.value)"
            "{% else %}"
            "  (as_local(state_attr('sun.sun', 'next_' + start.event)).hour*60 + "
            "   as_local(state_attr('sun.sun', 'next_' + start.event)).minute) "
            "{% endif %}"
            ") %}"
            "{% set e_m = ("
            "{% if end.kind == 'clock' %}"
            "  tod(end.value)"
            "{% else %}"
            "  (as_local(state_attr('sun.sun', 'next_' + end.event)).hour*60 + "
            "   as_local(state_attr('sun.sun', 'next_' + end.event)).minute) "
            "{% endif %}"
            ") %}"
            "{{ (s_m < e_m and (now_m >= s_m and now_m < e_m)) "
            "or (s_m >= e_m and (now_m >= s_m or now_m < e_m)) }}"
        ),
        "variables": {
            "start": start_ts or {},
            "end": end_ts or {},
        }
    }

def _schedule_clause_to_condition(clause: dict):
    """
    Convert a single inline schedule clause to a HA condition.
    enable  from A to B  -> within window(A,B)
    disable from A to B  -> NOT within window(A,B)
    """
    op = (clause.get("op") or "enable").lower()
    start_ts = clause.get("from")
    end_ts = clause.get("to", clause.get("until"))
    base = _window_condition_from_specs(start_ts, end_ts) if (start_ts and end_ts) else {"condition":"template","value_template":"true"}
    if op == "disable":
        return {"condition": "not", "conditions": [base]}
    return base

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
    # Always build outputs, even if there are no rules (schedules may still exist)
    rules = ir.get("rules", []) or []
    Path(outdir).mkdir(parents=True, exist_ok=True)

    bundled = []
    # collect helper keys we must ensure exist
    ctx_inputs = {}

    # package slug for schedule sensor ids
    pkg = _pkg_slug(outdir)

    # --- schedules collection ---
    declared_schedules, inline_by_rule, use_by_rule = _collect_schedules(ir)

    # --- alias resolution map (local + imported public aliases) ---
    aliases = _alias_map(ir)

    if os.getenv("HASSL_DEBUG"):
        print("HASSL aliases:", aliases)  # should include {'master_lights': 'light....', 'master_stands': 'light....'}
        
    # Build exported schedules map from GLOBAL_EXPORTS: base_name -> declaring_pkg
    
    exported_sched_pkgs = {}
    for (pkg_name, kind, sched_name), obj in getattr(sem_analyzer, "GLOBAL_EXPORTS", {}).items():
        if kind == "schedule":
            exported_sched_pkgs[str(sched_name)] = str(pkg_name)

    # --- validation: every 'schedule use <name>' must be declared locally OR exported by imports ---
    declared_base_names = {str(nm).split(".")[-1] for nm in (declared_schedules.keys() if declared_schedules else [])}
    for rule in ir.get("rules", []) or []:
        rname = rule.get("name", "<unnamed>")
        for nm in (use_by_rule.get(rname, []) or []):
            base = str(nm).split(".")[-1]
            if base not in declared_base_names and (base not in exported_sched_pkgs):
                expected_sensor = _schedule_sensor(base, pkg)
                raise ValueError(
                    "HASSL: schedule reference not found. "
                    f"Rule '{rname}' uses schedule '{nm}', but no schedule named '{base}' "
                    f"was declared in this package '{pkg}'.\n\n"
                    "Declare it with:\n"
                    f"  schedule {base}:\n"
                    "    enable from <start> to <end>;\n\n"
                    "Or ensure the schedule is declared in the same package.\n"
                    f"(If you expected a sensor, codegen looks for: {expected_sensor})"
                )

    # NOTE: No helper creation here — package.py owns schedule sensors.

    # ---- build automations (rules) ----
    for rule in rules:
        rname = rule["name"]
        gate = _gate_entity(rname)

        # gather schedule conditions for this rule
        cond_schedule_entities = []

        # 1) named schedules used by this rule → state('on') of the schedule sensor
        for nm in use_by_rule.get(rname, []) or []:
            base = str(nm).split(".")[-1]
            decl_pkg = exported_sched_pkgs.get(base, pkg)
            cond_schedule_entities.append(_schedule_sensor(base, decl_pkg))

        # 2) inline schedule clauses → compile directly to HA conditions (no helpers)
        inline_clauses = inline_by_rule.get(rname, []) or []
        inline_schedule_conditions = []
        for cl in inline_clauses:
            if isinstance(cl, dict) and cl.get("type") == "schedule_clause":
                inline_schedule_conditions.append(_schedule_clause_to_condition(cl))

        # de-dup schedule conditions
        cond_schedule_entities = sorted(set(cond_schedule_entities))

        # Now process each 'if' clause
        for idx, clause in enumerate(rule["clauses"]):
            # Each clause is {"condition": ..., "actions": [...]}
            cname = f"{_slug(rname)}__{idx+1}"
            expr0 = clause["condition"].get("expr", {})
            # resolve aliases inside the boolean expression
            expr = _resolve_expr_aliases(expr0, aliases)
            actions = clause["actions"]
            entities = sorted(_entity_ids_in_expr(expr))
            triggers = [{"platform": "state", "entity_id": e} for e in entities] or [{"platform": "time", "at": "00:00:00"}]
            triggers = _dedup_dicts(triggers)
            # rebuild condition dict with resolved expr
            cond_in = dict(clause["condition"])
            cond_in["expr"] = expr
            cond_ha = _condition_to_ha(cond_in)
            gate_cond = {"condition": "state", "entity_id": gate, "state": "on"}

            # schedule gate conditions (all must be satisfied)
            sched_conds = [{"condition":"state","entity_id": e,"state":"on"} for e in cond_schedule_entities]
            if inline_schedule_conditions:
                sched_conds.extend(inline_schedule_conditions)

            # --- NOT_BY guard (qualifier) ---
            qual = clause.get("condition", {}).get("not_by")
            qual_cond = None
            if qual:
                ent0 = entities[0] if entities else None
                if ent0:
                    if isinstance(qual, dict) and "rule" in qual:
                        rname_qual = _slug(str(qual["rule"]))
                        it_key = _rule_ctx_key(rname_qual, ent0)
                        ctx_inputs[it_key] = ent0
                        qual_cond = {
                            "condition": "template",
                            "value_template": "{{ trigger.to_state.context.parent_id != "
                                              "states('input_text.%s') }}" % it_key
                        }
                    else:
                        it_key = _entity_ctx_key(ent0)
                        ctx_inputs[it_key] = ent0
                        qual_cond = {
                            "condition": "template",
                            "value_template": "{{ trigger.to_state.context.parent_id != "
                                              "states('input_text.%s') }}" % it_key
                        }

            act_list = []
            for act in actions:
                if act["type"] == "assign":
                    eid = _resolve_name(act["target"], aliases)
                    service = "turn_on" if act["state"] == "on" else "turn_off"

                    # stamp parent context so NOT_BY can ignore our own writes
                    _k, _e, _label = _ctx_key_and_entity(eid, None)
                    ctx_inputs[_k] = _label
                    act_list.append({
                        "service": "input_text.set_value",
                        "data": {"entity_id": _e, "value": "{{ this.context.id }}"}
                    })
                    act_list.append({"service": f"homeassistant.{service}", "target": {"entity_id": eid}})
                elif act["type"] == "attr_assign":
                    eid = _resolve_name(act["entity"], aliases); attr = act["attr"]; val = act["value"]
                    # stamp parent context (attr-specific)
                    _k, _e, _label = _ctx_key_and_entity(eid, attr)
                    ctx_inputs[_k] = _label
                    act_list.append({
                        "service": "input_text.set_value",
                        "data": {"entity_id": _e, "value": "{{ this.context.id }}"}
                    })
                    if attr == "brightness":
                        act_list.append({"service": "light.turn_on", "target": {"entity_id": eid}, "data": {"brightness": val}})
                    elif attr == "kelvin":
                        # Prefer native kelvin with a color_temp fallback for older integrations
                        if isinstance(val, (int, float)):
                            act_list.append({
                                "service": "light.turn_on",
                                "target": {"entity_id": eid},
                                "data": {
                                    "kelvin": val,
                                    "color_temp": _kelvin_to_mireds(val)
                                }
                            })
                        else:
                            act_list.append({
                                "service": "light.turn_on",
                                "target": {"entity_id": eid},
                                "data": {
                                    "kelvin": val
                                }
                            })
                    else:
                        act_list.append({"service": "homeassistant.turn_on", "target": {"entity_id": eid}, "data": {attr: val}})
                elif act["type"] == "wait":
                    cond_expr = act["condition"].get("expr", act["condition"])
                    vt = _expr_to_template(_resolve_expr_aliases(cond_expr, aliases))
                    act_list.append({"wait_for_trigger": [{"platform": "template", "value_template": vt, "for": _dur_to_hms(act["for"])}]})
                    inner = act["then"]
                    if inner["type"] == "assign":
                        eid = _resolve_name(inner["target"], aliases)
                        service = "turn_on" if inner["state"] == "on" else "turn_off"
                        # stamp parent context for the inner action
                        _k, _e, _label = _ctx_key_and_entity(eid, None)
                        ctx_inputs[_k] = _label
                        act_list.append({
                            "service": "input_text.set_value",
                            "data": {"entity_id": _e, "value": "{{ this.context.id }}"}
                        })
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
                elif act["type"] == "tag":
                    # Store tag value in an input_text so other automations/templates can read it
                    key = f"hassl_tag_{_slug(act['name'])}"
                    full = f"input_text.{key}"
                    ctx_inputs.setdefault(key, act["name"])
                    val = act["value"]
                    act_list.append({
                        "service": "input_text.set_value",
                        "data": {"entity_id": full, "value": str(val)}
                    })
                else:
                    # Try light.turn_on data first; fallback to generic service data
                    if eid.startswith("light."):
                        act_list.append({"service": "light.turn_on", "target": {"entity_id": eid}, "data": {attr: val}})
                    else:
                        act_list.append({"service": "homeassistant.turn_on", "target": {"entity_id": eid}, "data": {attr: val}})

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

    pkg = _pkg_slug(outdir)

    out_path = Path(outdir) / f"rules_bundled_{pkg}.yaml"
    with open(out_path, "w") as f:
        # packages expect a mapping, not a bare list
        yaml.safe_dump({"automation": bundled}, f, sort_keys=False)

    helpers_path = Path(outdir) / f"helpers_{pkg}.yaml"

    # --- Build gate names from rules & rule_ctrl targets (preserve original names for display)
    gate_names = {rule["name"] for rule in rules}
    for rule in rules:
        for clause in rule.get("clauses", []):
            for act in clause.get("actions", []):
                if act.get("type") == "rule_ctrl" and "rule" in act:
                    gate_names.add(act["rule"])

    # Load existing helpers if present
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

    # 3) Merge our gate booleans using the original rule name for display
    for name in sorted(n for n in gate_names if isinstance(n, str) and n.strip()):
        key = f"hassl_gate_{_slug(name)}"
        merged["input_boolean"][key] = {
            "name": f"HASSL Gate {name}",
            "initial": "on",
        }

    # 4) Ensure input_text helpers referenced by NOT_BY guards *and* context stamps exist
    #    (ctx_inputs maps key -> human-friendly label for display)
    for it_key, label in sorted(ctx_inputs.items()):
        merged["input_text"].setdefault(it_key, {
            "name": f"HASSL Ctx {label}",
            "max": 64
        })

    # 5) (removed) schedule helpers are emitted in package.py as template binary_sensors

    # 6) Write back
    header = "# Generated by HASSL codegen\n"
    helpers_yaml = yaml.safe_dump(merged, sort_keys=False)
    helpers_path.write_text(header + helpers_yaml)

    return str(out_path)
