from typing import Dict, List
import os, re
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
    "kelvin": {
        # Typical usable range; adjust if your bulbs differ (e.g., 2000–6500K)
        "proxy": {"type": "input_number", "min": 2000, "max": 6500, "step": 50},
        # Newer HA exposes color_temp_kelvin; we prefer that for upstream reads
        "upstream": {"attr": "color_temp_kelvin"},
        # Downstream: HA light.turn_on supports 'kelvin' directly
        "service": {"domain": "light", "service": "light.turn_on", "data_key": "kelvin"}
    },
    "hs_color": {
        "proxy": {"type": "input_text"},
        "upstream": {"attr": "hs_color"},
        "service": {"domain": "light", "service": "light.turn_on", "data_key": "hs_color"}
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

def _safe(name: str) -> str:
    return name.replace(".", "_")

def _pkg_slug(outdir: str) -> str:
    base = os.path.basename(os.path.abspath(outdir))
    s = re.sub(r'[^a-z0-9]+', '_', base.lower()).strip('_')
    return s or "pkg"

def _proxy_entity(sync_name: str, prop: str) -> str:
    return (f"input_boolean.hassl_{_safe(sync_name)}_onoff" if prop == "onoff"
            else f"input_number.hassl_{_safe(sync_name)}_{prop}" if PROP_CONFIG.get(prop,{}).get("proxy",{}).get("type")=="input_number"
            else f"input_text.hassl_{_safe(sync_name)}_{prop}")

def _context_entity(entity: str, prop: str = None) -> str:
    if prop and prop != "onoff":
        return f"input_text.hassl_ctx_{_safe(entity)}_{prop}"
    return f"input_text.hassl_ctx_{_safe(entity)}"

def _domain(entity: str) -> str:
    return entity.split(".", 1)[0]

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
        helpers["input_text"][f"hassl_ctx_{_safe(e)}"] = {"name": f"HASSL Ctx {e}", "max": 64}
        for prop in sorted(entity_props[e]):
            if prop != "onoff":
                helpers["input_text"][f"hassl_ctx_{_safe(e)}_{prop}"] = {
                    "name": f"HASSL Ctx {e} {prop}", "max": 64
                }

    # Proxies
    for s in ir.syncs:
        for p in s.properties:
            cfg = PROP_CONFIG.get(p.name, {})
            proxy = cfg.get("proxy", {"type":"input_number","min":0,"max":255,"step":1})
            if p.name == "onoff" or proxy.get("type") == "input_boolean":
                helpers["input_boolean"][f"hassl_{_safe(s.name)}_{p.name}"] = {"name": f"HASSL Proxy {s.name} {p.name}"}
            elif proxy.get("type") == "input_text":
                helpers["input_text"][f"hassl_{_safe(s.name)}_{p.name}"] = {"name": f"HASSL Proxy {s.name} {p.name}", "max": 120}
            else:
                helpers["input_number"][f"hassl_{_safe(s.name)}_{p.name}"] = {
                    "name": f"HASSL Proxy {s.name} {p.name}", "min": proxy.get("min", 0), "max": proxy.get("max", 255),
                    "step": proxy.get("step", 1), "mode": "slider"
                }

    # Writer scripts per (sync, member, prop)
    for s in ir.syncs:
        # be defensive in case props/members are empty
        if not getattr(s, "properties", None):
            continue
        if not getattr(s, "members", None):
            continue

        for p in s.properties:
            prop = getattr(p, "name", None) or (p.get("name") if isinstance(p, dict) else None)
            if not prop:
                continue

            for m in s.members:
                dom = _domain(m)
                script_key = f"hassl_write_sync_{_safe(s.name)}_{_safe(m)}_{prop}_set"

                # Step 1: always stamp context to block feedback loops
                seq = [{
                    "service": "input_text.set_value",
                    "data": {
                        "entity_id": _context_entity(m, prop if prop != "onoff" else None),
                        "value": "{{ this.context.id }}"
                    }
                }]

                # Step 2: for non-onoff, forward the value to the actual device
                if prop == "hs_color":
                    # value is a JSON string; HA expects a list
                    seq.append({
                        "service": "light.turn_on",
                        "target": {"entity_id": m},
                        "data": { "hs_color": "{{ value | from_json }}" }
                    })
                elif prop != "onoff":
                    svc = PROP_CONFIG.get(prop, {}).get("service", {})
                    service = svc.get("service", f"{dom}.turn_on")
                    data_key = svc.get("data_key", prop)
                    seq.append({
                        "service": service,
                        "target": {"entity_id": m},
                        "data": { data_key: "{{ value }}" }
                    })

                # actually register the script
                scripts["script"][script_key] = {
                    "alias": f"HASSL write (sync {s.name} → {m} {prop})",
                    "mode": "single",
                    "sequence": seq
                }
                        
    # Upstream automations
    for s in ir.syncs:
        for p in s.properties:
            prop = p.name;
            triggers = [];
            conditions = [];
            actions = []
            
            if prop == "onoff":
                for m in s.members:
                    triggers.append({"platform": "state", "entity_id": m})
                    
                conditions.append({"condition": "template",
                                   "value_template": (
                                       "{{ trigger.to_state.context.parent_id != "
                                       "states('input_text.hassl_ctx_' ~ trigger.entity_id|replace('.','_')) }}"
                                   )
                                   })
                actions = [{
                    "choose": [
                        {"conditions": [{"condition":"template","value_template":"{{ trigger.to_state.state == 'on' }}"}],
                         "sequence": [{"service":"input_boolean.turn_on","target":{"entity_id":f"input_boolean.hassl_{_safe(s.name)}_onoff"}}]
                         },
                        {"conditions": [{"condition":"template","value_template":"{{ trigger.to_state.state != 'on' }}"}],
                         "sequence": [{"service":"input_boolean.turn_off","target": {"entity_id": f"input_boolean.hassl_{_safe(s.name)}_onoff"}}]
                         }
                    ]
                }]
            else:
                cfg = PROP_CONFIG.get(prop, {});
                attr = cfg.get("upstream", {}).get("attr", prop)

                #state trigger on attribute
                for m in s.members:
                    triggers.append({"platform": "state", "entity_id": m, "attribute": attr})
                suffix = f"_{prop}" if prop != "onoff" else ""    
                conditions.append({
                    "condition":"template",
                    "value_template": (
                        "{{ trigger.to_state.context.parent_id != "
                        "states('input_text.hassl_ctx_' ~ trigger.entity_id|replace('.', '_') ~ '" + suffix + "')  }}"
                        )
                })
                
                proxy_e = (
                    f"input_text.hassl_{_safe(s.name)}_{prop}"
                    if PROP_CONFIG.get(prop,{}).get("proxy",{}).get("type") == "input_text"
                    else f"input_number.hassl_{_safe(s.name)}_{prop}"
                )

                if prop == "mute":
                    actions = [{
                        "choose": [
                            {
                                "conditions": [{"condition":"template","value_template": f"{{{{ state_attr(trigger.entity_id, '{attr}') | bool }}}}"}],
                                "sequence": [{"service": "input_boolean.turn_on", "target": {"entity_id": proxy_e}}]
                            },
                            {
                                "conditions": [{"condition":"template","value_template": f"{{{{ not (state_attr(trigger.entity_id, '{attr}') | bool) }}}}"}],
                                "sequence": [{"service": "input_boolean.turn_off", "target": {"entity_id": proxy_e}}]
                            }
                        ]
                    }]
                elif prop == "preset_mode":
                    actions = [{"service": "input_text.set_value", "data": {"entity_id": proxy_e, "value": f"{{{{ state_attr(trigger.entity_id, '{attr}') }}}}"}}]
                elif prop == "hs_color":
                    # Store JSON so we can send a real list back later
                    actions = [{"service": "input_text.set_value", "data": {"entity_id": proxy_e, "value": f"{{{{ state_attr(trigger.entity_id, '{attr}') | to_json }}}}"}}]
                else:
                    actions = [{"service": "input_number.set_value", "data": {"entity_id": proxy_e, "value": f"{{{{ state_attr(trigger.entity_id, '{attr}') }}}}"}}]
                    
            if triggers:
                automations.append({
                    "alias": f"HASSL sync {s.name} upstream {prop}",
                    "mode": "restart",
                    "trigger": triggers,
                    "condition": conditions,
                    "action": actions
                })

    # Downstream automations
        # Downstream automations
    for s in ir.syncs:
        for p in s.properties:
            prop = p.name
            if prop == "onoff":
                trigger = [{"platform":"state","entity_id": f"input_boolean.hassl_{_safe(s.name)}_onoff"}]
                actions = []
                for m in s.members:
                    dom = _domain(m)
                    cond_tpl = "{{ is_state('%s','on') != is_state('%s','on') }}" % (f"input_boolean.hassl_{_safe(s.name)}_onoff", m)
                    service_on  = _turn_service(dom, True)
                    service_off = _turn_service(dom, False)
                    actions.append({
                        "choose":[
                            {
                                "conditions":[
                                    {"condition":"template","value_template":cond_tpl},
                                    {"condition":"state","entity_id": f"input_boolean.hassl_{_safe(s.name)}_onoff","state":"on"}
                                ],
                                "sequence":[
                                    {"service":"script.%s" % f"hassl_write_sync_{_safe(s.name)}_{_safe(m)}_onoff_set"},
                                    {"service": service_on, "target":{"entity_id": m}}
                                ]
                            },
                            {
                                "conditions":[
                                    {"condition":"template","value_template":cond_tpl},
                                    {"condition":"state","entity_id": f"input_boolean.hassl_{_safe(s.name)}_onoff","state":"off"}
                                ],
                                "sequence":[
                                    {"service":"script.%s" % f"hassl_write_sync_{_safe(s.name)}_{_safe(m)}_onoff_set"},
                                    {"service": service_off, "target":{"entity_id": m}}
                                ]
                            }
                        ]
                    })
                automations.append({"alias": f"HASSL sync {s.name} downstream onoff","mode":"queued","max":10,"trigger": trigger,"action": actions})
            else:
                proxy_e = (
                    f"input_text.hassl_{_safe(s.name)}_{prop}"
                    if PROP_CONFIG.get(prop,{}).get("proxy",{}).get("type") == "input_text"
                    else f"input_number.hassl_{_safe(s.name)}_{prop}"
                )
                trigger = [{"platform": "state","entity_id": proxy_e}]
                actions = []
                cfg = PROP_CONFIG.get(prop, {})
                attr = cfg.get("upstream", {}).get("attr", prop)

                for m in s.members:
                    if prop == "mute":
                        diff_tpl = "{{ (states('%s') == 'on') != (state_attr('%s','%s') | bool) }}" % (proxy_e, m, attr)
                        val_expr = "{{ iif(states('%s') == 'on', true, false) }}" % (proxy_e)
                    elif prop == "preset_mode":
                        diff_tpl = "{{ (states('%s') != state_attr('%s','%s') ) }}" % (proxy_e, m, attr)
                        val_expr = "{{ states('%s') }}" % (proxy_e)
                    elif prop == "hs_color":
                        # compare JSON string vs current attr rendered to JSON
                        diff_tpl = "{{ states('%s') != (state_attr('%s','%s') | to_json) }}" % (proxy_e, m, attr)
                        # pass JSON string to script; script converts with from_json
                        val_expr = "{{ states('%s') }}" % (proxy_e)
                    else:
                        diff_tpl = "{{ (states('%s') | float) != (state_attr('%s','%s') | float) }}" % (proxy_e, m, attr)
                        val_expr = "{{ states('%s') }}" % (proxy_e)

                    actions.append({
                        "choose":[
                            {
                                "conditions":[{"condition":"template","value_template": diff_tpl}],
                                "sequence":[
                                    {"service":"script.%s" % f"hassl_write_sync_{_safe(s.name)}_{_safe(m)}_{prop}_set","data":{"value": val_expr}}
                                ]
                            }
                        ]
                    })
                automations.append({"alias": f"HASSL sync {s.name} downstream {prop}","mode":"queued","max":10,"trigger": trigger,"action": actions})

    pkg = _pkg_slug(outdir)
    
    # Write helpers.yaml / scripts.yaml
    _dump_yaml(os.path.join(outdir, f"helpers_{pkg}.yaml"), helpers, ensure_sections=True)
    _dump_yaml(os.path.join(outdir, f"scripts_{pkg}.yaml"), scripts)

    # Write automations per sync
    for s in ir.syncs:
        doc = [a for a in automations if a["alias"].startswith(f"HASSL sync {s.name}")]
        if doc:
            _dump_yaml(os.path.join(outdir, f"sync_{pkg}_{_safe(s.name)}.yaml"), {"automation": doc})
