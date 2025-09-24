from typing import Dict, List
import os
from ..semantics.analyzer import IRProgram, IRSync
from .yaml_emit import _dump_yaml, ensure_dir

# Property configuration for proxies and services
PROP_CONFIG = {
    "onoff": {"proxy": {"type": "input_boolean"}},
    "brightness": {
        "proxy": {"type": "input_number", "min": 0, "max": 255, "step": 1},
        "upstream": {"attr": "brightness"},
        "service": {"domain": "light", "service": "light.turn_on", "data_key": "brightness"}
    },
    "color_temp": {
        "proxy": {"type": "input_number", "min": 150, "max": 500, "step": 1},
        "upstream": {"attr": "color_temp"},
        "service": {"domain": "light", "service": "light.turn_on", "data_key": "color_temp"}
    },
    "percentage": {
        "proxy": {"type": "input_number", "min": 0, "max": 100, "step": 1},
        "upstream": {"attr": "percentage"},
        "service": {"domain": "fan", "service": "fan.set_percentage", "data_key": "percentage"}
    },
    "preset_mode": {
        "proxy": {"type": "input_text"},
        "upstream": {"attr": "preset_mode"},
        "service": {"domain": "fan", "service": "fan.set_preset_mode", "data_key": "preset_mode"}
    },
    "volume": {
        "proxy": {"type": "input_number", "min": 0, "max": 1, "step": 0.01},
        "upstream": {"attr": "volume_level"},
        "service": {"domain": "media_player", "service": "media_player.volume_set", "data_key": "volume_level"}
    },
    "mute": {
        "proxy": {"type": "input_boolean"},
        "upstream": {"attr": "is_volume_muted"},
        "service": {"domain": "media_player", "service": "media_player.volume_mute", "data_key": "is_volume_muted"}
    }
}

def _safe(name: str) -> str: return name.replace(".", "_")
def _proxy_entity(sync_name: str, prop: str) -> str:
    return (f"input_boolean.hassl__{_safe(sync_name)}__onoff" if prop == "onoff"
            else f"input_number.hassl__{_safe(sync_name)}__{prop}" if PROP_CONFIG.get(prop,{}).get("proxy",{}).get("type")=="input_number"
            else f"input_text.hassl__{_safe(sync_name)}__{prop}")
def _context_entity(entity: str, prop: str = None) -> str:
    if prop and prop != "onoff":
        return f"input_text.hassl_ctx__{_safe(entity)}__{prop}"
    return f"input_text.hassl_ctx__{_safe(entity)}"
def _domain(entity: str) -> str: return entity.split(".", 1)[0]
def _turn_service(domain: str, state_on: bool) -> str:
    if domain in ("light","switch","fan","media_player","cover"):
        return f"{domain}.turn_on" if state_on else f"{domain}.turn_off"
    return "homeassistant.turn_on" if state_on else "homeassistant.turn_off"

def emit_package(ir: IRProgram, outdir: str):
    ensure_dir(outdir)
    helpers: Dict = {"input_text": {}, "input_boolean": {}, "input_number": {}}
    scripts: Dict = {"script": {}}
    automations: List[Dict] = []

    # Context helpers for entities & per-prop contexts
    sync_entities = set(); entity_props = {}
    for s in ir.syncs:
        for m in s.members:
            sync_entities.add(m)
            entity_props.setdefault(m, set())
            for p in s.properties: entity_props[m].add(p.name)

    for e in sorted(sync_entities):
        helpers["input_text"][f"hassl_ctx__{_safe(e)}"] = {"name": f"HASSL Ctx {e}", "max": 64}
        for prop in sorted(entity_props[e]):
            helpers["input_text"][f"hassl_ctx__{_safe(e)}__{prop}"] = {"name": f"HASSL Ctx {e} {prop}", "max": 64}

    # Proxies
    for s in ir.syncs:
        for p in s.properties:
            cfg = PROP_CONFIG.get(p.name, {})
            proxy = cfg.get("proxy", {"type":"input_number","min":0,"max":255,"step":1})
            if p.name == "onoff" or proxy.get("type") == "input_boolean":
                helpers["input_boolean"][f"hassl__{_safe(s.name)}__{p.name}"] = {"name": f"HASSL Proxy {s.name} {p.name}"}
            elif proxy.get("type") == "input_text":
                helpers["input_text"][f"hassl__{_safe(s.name)}__{p.name}"] = {"name": f"HASSL Proxy {s.name} {p.name}", "max": 120}
            else:
                helpers["input_number"][f"hassl__{_safe(s.name)}__{p.name}"] = {
                    "name": f"HASSL Proxy {s.name} {p.name}", "min": proxy.get("min", 0), "max": proxy.get("max", 255),
                    "step": proxy.get("step", 1), "mode": "slider"
                }

    # Writer scripts per (sync, member, prop)
    for s in ir.syncs:
        for p in ir.syncs[0].properties if False else s.properties:  # keep loop explicit
            for m in s.members:
                prop = p.name; dom = _domain(m)
                script_key = f"hassl_write__sync_{_safe(s.name)}__{_safe(m)}__{prop}__set"
                seq = [{
                    "service": "input_text.set_value",
                    "data": {"entity_id": _context_entity(m, prop if prop!="onoff" else None), "value": "{{ this.context.id }}"}
                }]
                if prop == "onoff":
                    seq.append({"service": "<FILL_BY_AUTOMATION>", "target": {"entity_id": m}})
                else:
                    svc = PROP_CONFIG.get(prop, {}).get("service", {})
                    service = svc.get("service", f"{dom}.turn_on")
                    data_key = svc.get("data_key", prop)
                    seq.append({"service": service, "target": {"entity_id": m}, "data": { data_key: "{{ value }}" }})
                scripts["script"][script_key] = {"alias": f"HASSL write (sync {s.name} → {m} {prop})", "mode": "single", "sequence": seq}

    # Upstream automations
    for s in ir.syncs:
        for p in s.properties:
            prop = p.name; triggers = []; conditions = []; actions = []
            if prop == "onoff":
                for m in s.members: triggers.append({"platform": "state", "entity_id": m})
                conditions.append({"condition": "template", "value_template": "{{ trigger.to_state.context.parent_id != states('%s') }}" % _context_entity("{{ trigger.entity_id }}")})
                actions = [{
                    "choose": [
                        {"conditions": [{"condition":"template","value_template":"{{ trigger.to_state.state == 'on' }}"}],
                         "sequence": [{"service":"input_boolean.turn_on","target":{"entity_id":_proxy_entity(s.name,"onoff")}}]},
                        {"conditions": [{"condition":"template","value_template":"{{ trigger.to_state.state != 'on' }}"}],
                         "sequence": [{"service":"input_boolean.turn_off","target":{"entity_id":_proxy_entity(s.name,"onoff")}}]}
                    ]
                }]
            else:
                cfg = PROP_CONFIG.get(prop, {}); attr = cfg.get("upstream", {}).get("attr", prop)
                for m in s.members:
                    triggers.append({"platform": "template", "value_template": "{{ state_attr('%s','%s') }}" % (m, attr)})
                conditions.append({"condition":"template","value_template":"{{ trigger.to_state.context.parent_id != states('%s') }}" % _context_entity("{{ trigger.entity_id }}", prop)})
                proxy_e = _proxy_entity(s.name, prop)
                if prop in ("mute",):
                    actions = [{
                        "choose":[
                            {"conditions":[{"condition":"template","value_template":"{{ state_attr(trigger.entity_id, '%s') | bool }}" % attr}],
                             "sequence":[{"service":"input_boolean.turn_on","target":{"entity_id": proxy_e}}]},
                            {"conditions":[{"condition":"template","value_template":"{{ not (state_attr(trigger.entity_id, '%s') | bool) }}" % attr}],
                             "sequence":[{"service":"input_boolean.turn_off","target":{"entity_id": proxy_e}}]}
                        ]}]
                elif prop in ("preset_mode",):
                    actions = [{"service":"input_text.set_value","data":{"entity_id": proxy_e,"value":"{{ state_attr(trigger.entity_id, '%s') }}" % attr}}]
                else:
                    actions = [{"service":"input_number.set_value","data":{"entity_id": proxy_e,"value":"{{ state_attr(trigger.entity_id, '%s') }}" % attr}}]
            if triggers:
                automations.append({"alias": f"HASSL sync {s.name} upstream {prop}", "mode": "restart", "trigger": triggers, "condition": conditions, "action": actions})

    # Downstream automations
    for s in ir.syncs:
        for p in s.properties:
            prop = p.name
            if prop == "onoff":
                trigger = [{"platform":"state","entity_id": _proxy_entity(s.name,"onoff")}]; actions = []
                for m in s.members:
                    dom = _domain(m); cond_tpl = "{{ is_state('%s','on') != is_state('%s','on') }}" % (_proxy_entity(s.name,"onoff"), m)
                    service_on  = _turn_service(dom, True); service_off = _turn_service(dom, False)
                    actions.append({"choose":[
                        {"conditions":[{"condition":"template","value_template":cond_tpl},{"condition":"state","entity_id": _proxy_entity(s.name,"onoff"),"state":"on"}],
                         "sequence":[{"service":"script.%s" % f"hassl_write__sync_{_safe(s.name)}__{_safe(m)}__onoff__set"},{"service": service_on, "target":{"entity_id": m}}]},
                        {"conditions":[{"condition":"template","value_template":cond_tpl},{"condition":"state","entity_id": _proxy_entity(s.name,"onoff"),"state":"off"}],
                         "sequence":[{"service":"script.%s" % f"hassl_write__sync_{_safe(s.name)}__{_safe(m)}__onoff__set"},{"service": service_off, "target":{"entity_id": m}}]}
                    ]})
                automations.append({"alias": f"HASSL sync {s.name} downstream onoff","mode":"queued","max":1,"trigger": trigger,"action": actions})
            else:
                proxy_e = _proxy_entity(s.name, prop); trigger = [{"platform": "state","entity_id": proxy_e}]; actions = []
                cfg = PROP_CONFIG.get(prop, {}); attr = cfg.get("upstream", {}).get("attr", prop)
                for m in s.members:
                    if prop in ("mute",):
                        diff_tpl = "{{ (states('%s') == 'on') != (state_attr('%s','%s') | bool) }}" % (proxy_e, m, attr)
                        val_expr = "{{ iif(states('%s') == 'on', true, false) }}" % (proxy_e)
                    elif prop in ("preset_mode",):
                        diff_tpl = "{{ (states('%s') != state_attr('%s','%s') ) }}" % (proxy_e, m, attr)
                        val_expr = "{{ states('%s') }}" % (proxy_e)
                    else:
                        diff_tpl = "{{ (states('%s') | float) != (state_attr('%s','%s') | float) }}" % (proxy_e, m, attr)
                        val_expr = "{{ states('%s') }}" % (proxy_e)
                    actions.append({"choose":[{"conditions":[{"condition":"template","value_template": diff_tpl}], "sequence":[
                        {"service":"script.%s" % f"hassl_write__sync_{_safe(s.name)}__{_safe(m)}__{prop}__set","data":{"value": val_expr}}
                    ]}]})
                automations.append({"alias": f"HASSL sync {s.name} downstream {prop}","mode":"queued","max":1,"trigger": trigger,"action": actions})

    # Write helpers.yaml / scripts.yaml
    _dump_yaml(os.path.join(outdir, "helpers.yaml"), helpers)
    _dump_yaml(os.path.join(outdir, "scripts.yaml"), scripts)

    # Write automations per sync
    for s in ir.syncs:
        doc = [a for a in automations if a["alias"].startswith(f"HASSL sync {s.name}")]
        if doc:
            _dump_yaml(os.path.join(outdir, f"sync__{_safe(s.name)}.yaml"), {"automation": doc})

# ---- Rule codegen (incl. per-rule contexts) ----
def _safe_entity(e: str) -> str: return e.replace(".", "_")

def _rule_ctx_helper(rule_name: str, entity: str) -> str:
    return f"input_text.hassl_ctx__rule__{_safe(rule_name)}__{_safe_entity(entity)}"

def _ensure_rule_ctx_helper(helpers: Dict, rule_name: str, entity: str):
    key = f"hassl_ctx__rule__{_safe(rule_name)}__{_safe_entity(entity)}"
    helpers.setdefault("input_text", {})
    if key not in helpers["input_text"]:
        helpers["input_text"][key] = {"name": f"HASSL Ctx rule {rule_name} {entity}", "max": 64}

def _ensure_rule_writer(scripts: Dict, rule_name: str, entity: str, state_on: bool):
    dom = entity.split(".",1)[0]; op = "on" if state_on else "off"
    key = f"hassl_write__rule_{_safe(rule_name)}__{_safe_entity(entity)}__onoff__{op}"
    scripts.setdefault("script", {})
    if key in scripts["script"]: return key
    seq = [
        {"service":"input_text.set_value","data":{"entity_id": _rule_ctx_helper(rule_name, entity),"value":"{{ this.context.id }}"}},
        {"service": f"{dom}.turn_on" if state_on else f"{dom}.turn_off","target":{"entity_id": entity}}
    ]
    scripts["script"][key] = {"alias": f"HASSL write (rule {rule_name} → {entity} onoff {op})","mode":"single","sequence": seq}
    return key

def _ensure_rule_writer_attr(scripts: Dict, rule_name: str, entity: str, attr: str):
    dom = entity.split(".",1)[0]
    key = f"hassl_write__rule_{_safe(rule_name)}__{_safe_entity(entity)}__{attr}__set"
    scripts.setdefault("script", {})
    if key in scripts["script"]: return key
    # Map attributes
    if dom == "light" and attr in ("brightness","color_temp"):
        svc = "light.turn_on"; data_key = attr
    elif dom == "fan" and attr in ("percentage","preset_mode"):
        svc = "fan.set_percentage" if attr=="percentage" else "fan.set_preset_mode"; data_key = attr
    elif dom == "media_player":
        if attr in ("volume","volume_level"): svc, data_key = "media_player.volume_set", "volume_level"
        elif attr in ("mute","is_volume_muted"): svc, data_key = "media_player.volume_mute", "is_volume_muted"
        elif attr == "source": svc, data_key = "media_player.select_source", "source"
        else: svc, data_key = f"{dom}.turn_on", attr
    else:
        svc, data_key = f"{dom}.turn_on", attr
    seq = [
        {"service":"input_text.set_value","data":{"entity_id": _rule_ctx_helper(rule_name, entity),"value":"{{ this.context.id }}"}},
        {"service": svc,"target":{"entity_id": entity},"data":{ data_key: "{{ value }}" }}
    ]
    scripts["script"][key] = {"alias": f"HASSL write (rule {rule_name} → {entity} {attr} set)","mode":"single","sequence": seq}
    return key

def _collect_entities_from_expr(expr):
    ents = set()
    if isinstance(expr, dict):
        for k,v in expr.items(): ents |= _collect_entities_from_expr(v)
    elif isinstance(expr, list):
        for x in expr: ents |= _collect_entities_from_expr(x)
    elif isinstance(expr, str) and "." in expr:
        ents.add(expr)
    return ents

def _jinja_for_operand(x):
    if isinstance(x, str) and "." in x: return f"is_state('{x}','on')"
    if isinstance(x, str): return f"'{x}'"
    if isinstance(x, (int,float)): return str(x)
    if isinstance(x, dict): return _jinja_for_expr(x)
    return str(x)

def _as_state_value(x):
    if isinstance(x, str) and "." in x: return f"(states('{x}') | float(0))"
    if isinstance(x, str): return f"'{x}'"
    return str(x)

def _jinja_for_expr(expr):
    if isinstance(expr, dict) and "op" in expr:
        op = expr["op"]
        if op == "or":  return f"(({_jinja_for_expr(expr['left'])}) or ({_jinja_for_expr(expr['right'])}))"
        if op == "and": return f"(({_jinja_for_expr(expr['left'])}) and ({_jinja_for_expr(expr['right'])}))"
        if op == "not": return f"(not ({_jinja_for_expr(expr['value'])}))"
        left, right = expr.get("left"), expr.get("right")
        if op in ("==","!="):
            if isinstance(right, str) and right in ("on","off") and isinstance(left, str) and "." in left:
                comp = "is_state" if op=="==" else "not is_state"; return f"({comp}('{left}','{right}'))"
            if isinstance(left, str) and left in ("on","off") and isinstance(right, str) and "." in right:
                comp = "is_state" if op=="==" else "not is_state"; return f"({comp}('{right}','{left}'))"
            return f"(({_jinja_for_operand(left)}) {op} ({_jinja_for_operand(right)}))"
        if op in ("<",">","<=",">="):
            return f"(({_as_state_value(left)}) {op} ({_as_state_value(right)}))"
    return _jinja_for_operand(expr)

def dur_to_hms(d: str) -> str:
    import re
    m = re.fullmatch(r"(\d+)(ms|s|m|h|d)", d)
    if not m: return "00:00:00"
    n = int(m.group(1)); unit = m.group(2)
    if unit == "s": return f"00:00:{n:02d}"
    if unit == "m": return f"00:{n:02d}:00"
    if unit == "h": return f"{n:02d}:00:00"
    if unit == "d": return f"{n*24:02d}:00:00"
    return "00:00:01"

def emit_rules(ir: IRProgram, helpers: Dict, automations: List[Dict], scripts: Dict):
    # enable flags
    for r in ir.rules:
        key = f"hassl_rule__{_safe(r.name)}__enabled"
        helpers["input_boolean"][key] = {"name": f"HASSL Rule Enable {r.name}", "initial": True}

    for r in ir.rules:
        for idx, clause in enumerate(r.clauses, start=1):
            expr = clause["condition"]["expr"]
            ents = sorted(_collect_entities_from_expr(expr)) or [None]
            for ent in ents:
                triggers = [{"platform": "time_pattern", "minutes": "/1"}] if ent is None else [{"platform":"state","entity_id": ent}]
                conditions = [
                    {"condition":"state","entity_id": f"input_boolean.hassl_rule__{_safe(r.name)}__enabled","state":"on"},
                    {"condition":"template","value_template": _jinja_for_expr(expr)},
                ]
                # qualifier guard
                nb = clause["condition"].get("not_by")
                if ent and nb:
                    if nb in ("any_hassl","this"):
                        conditions.append({"condition":"template","value_template":"{{ trigger.to_state.context.parent_id != states('%s') }}" % (f"input_text.hassl_ctx__{_safe(ent)}")})
                    elif isinstance(nb, dict) and "rule" in nb:
                        rule_name = nb["rule"]
                        conditions.append({"condition":"template","value_template":"{{ trigger.to_state.context.parent_id != states('%s') }}" % (f"input_text.hassl_ctx__rule__{_safe(rule_name)}__{_safe_entity(ent)}")})
                # actions
                seq = []
                for act in clause["actions"]:
                    t = act.get("type")
                    if t == "assign":
                        target = act["target"]; state = act["state"]; turn_on = (state=="on")
                        _ensure_rule_ctx_helper(helpers, r.name, target)
                        script_key = _ensure_rule_writer(scripts, r.name, target, turn_on)
                        seq.append({"service": f"script.{script_key}"})
                        if "for" in act:
                            seq += [{"delay": dur_to_hms(act["for"])},{"service": f"script.{_ensure_rule_writer(scripts, r.name, target, not turn_on)}"}]
                    elif t == "attr_assign":
                        entity = act["entity"]; attr = act["attr"]; value = act["value"]
                        _ensure_rule_ctx_helper(helpers, r.name, entity)
                        writer_key = _ensure_rule_writer_attr(scripts, r.name, entity, attr)
                        seq.append({"service": f"script.{writer_key}", "data": {"value": value}})
                    elif t == "wait":
                        cond = act["condition"]; dur = act["for"]
                        seq += [{"wait_for_trigger":[{"platform":"template","value_template": _jinja_for_expr(cond["expr"]), "for": dur_to_hms(dur)}]}]
                        # then recurse single inner action:
                        inner = {"type":"assign"} if False else act["then"]
                        # reuse simple serializer
                        if inner.get("type") == "assign":
                            target = inner["target"]; state = inner["state"]; turn_on = (state=="on")
                            _ensure_rule_ctx_helper(helpers, r.name, target)
                            script_key = _ensure_rule_writer(scripts, r.name, target, turn_on)
                            seq.append({"service": f"script.{script_key}"})
                    elif t == "rule_ctrl":
                        op = act["op"]; name = act["rule"]; dur = act["for"]
                        bool_e = f"input_boolean.hassl_rule__{_safe(name)}__enabled"
                        set_svc = "input_boolean.turn_off" if op=="disable" else "input_boolean.turn_on"
                        seq += [{"service": set_svc, "target":{"entity_id": bool_e}},
                                {"delay": dur_to_hms(dur)},
                                {"service": "input_boolean.turn_on","target":{"entity_id": bool_e}}]
                    elif t == "tag":
                        tag = act["name"]; val = act["value"]
                        helpers["input_text"][f"hassl_tag__{_safe(tag)}"] = {"name": f"HASSL Tag {tag}", "max": 120}
                        seq.append({"service":"input_text.set_value","data":{"entity_id": f"input_text.hassl_tag__{_safe(tag)}","value": str(val)}})
                automations.append({"alias": f"HASSL rule {r.name} [clause {idx}]{' via '+ent if ent else ''}",
                                    "mode":"restart","trigger": triggers,"condition": conditions,"action": seq or [{"delay":"00:00:00"}]})

def emit_package_with_rules(ir: IRProgram, outdir: str):
    # (kept for potential external calls)
    helpers: Dict = {"input_text": {}, "input_boolean": {}, "input_number": {}}
    scripts: Dict = {"script": {}}
    automations: List[Dict] = []
    emit_rules(ir, helpers, automations, scripts)
