from typing import Dict, List, Iterable, Any
import os, re
from dataclasses import dataclass, field
from ..semantics.analyzer import IRProgram, IRSync
from .yaml_emit import _dump_yaml, ensure_dir

# ----------------------------
# Property configuration for proxies and services
# ----------------------------
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

# ----------------------------
# Utility helpers
# ----------------------------
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

# ----------------------------
#   SCHEDULE HELPER EMISSION
# ----------------------------
@dataclass
class ScheduleRegistry:
    """Per-package registry to ensure each named schedule helper is emitted once."""
    pkg: str
    created: Dict[str, str] = field(default_factory=dict)   # name -> entity_id
    sensors: List[Dict] = field(default_factory=list)       # collected template sensors (for YAML)

    def eid_for(self, name: str) -> str:
        return f"binary_sensor.hassl_schedule_{self.pkg}_{_safe(name)}_active".lower()

    def register_decl(self, name: str, clauses: List[Dict]) -> str:
        if name in self.created:
            return self.created[name]
        eid = self.eid_for(name)
        sensor_def = _emit_schedule_helper_yaml(eid, self.pkg, name, clauses)
        self.sensors.append(sensor_def)
        self.created[name] = eid
        return eid

def _jinja_offset(offset: str) -> str:
    """
    Convert '+15m'/'-10s'/'2h' to a Jinja timedelta expression snippet:
      ' + timedelta(minutes=15)' / ' - timedelta(seconds=10)' / ' + timedelta(hours=2)'
    Home Assistant’s Jinja has 'timedelta' filter available. Milliseconds are ignored.
    """
    if not offset:
        return ""
    m = re.fullmatch(r"([+-])(\d+)(ms|s|m|h|d)", str(offset).strip())
    if not m:
        return ""
    sign, n, unit = m.group(1), int(m.group(2)), m.group(3)
    if unit == "ms":
        return ""  # HA templates don’t support ms granularity cleanly; ignore
    kw = {"s":"seconds", "m":"minutes", "h":"hours", "d":"days"}[unit]
    return f" {sign} timedelta({kw}={n})"

def _emit_schedule_helper_yaml(entity_id: str, pkg: str, name: str, clauses: List[Dict]) -> Dict:
    """
    Build a Home Assistant template binary_sensor for a *named* schedule.
    Semantics: ON = (OR of all ENABLE windows) AND NOT (OR of all DISABLE windows)
    Supports:
      - clock windows with wrap (e.g., 22:00..06:00)
      - sun windows with optional offsets (e.g., sunrise-30m..09:30 or sunset..sunrise)
      - entity/CNAME refs (treated as on/off booleans)
    """
    enable_exprs: List[str] = []
    disable_exprs: List[str] = []

    def ts_to_expr_in_window(start: Any, end: Any) -> str:
        # CLOCK → CLOCK: pure expression (no control blocks) so it can live inside {{ ... }}
        if isinstance(start, dict) and start.get("kind") == "clock" and \
           isinstance(end, dict) and end.get("kind") == "clock":
            s = start["value"]
            e = end["value"]
            # Zero-padded HH:MM strings compare lexicographically.
            # in_window = (S <= now < E) if S < E
            #           = (now >= S) or (now < E) if S >= E  (wrap past midnight)
            now_call = "now().strftime('%H:%M')"
            return (
                f"( ('{s}' < '{e}' and ({now_call} >= '{s}' and {now_call} < '{e}')) "
                f"or ('{s}' >= '{e}' and ({now_call} >= '{s}' or {now_call} < '{e}')) )"
            )

        # SUN → SUN: use sun condition edges; sunset..sunrise wraps, sunrise..sunset doesn’t
        if isinstance(start, dict) and start.get("kind") == "sun" and isinstance(end, dict) and end.get("kind") == "sun":
            s_ev = start["event"]; s_off = _jinja_offset(start.get("offset", "0s"))
            e_ev = end["event"];   e_off = _jinja_offset(end.get("offset", "0s"))
            # after start AND before end, with wrap handled by OR(after start, before end) for sunset->sunrise
            if s_ev == "sunset" and e_ev == "sunrise":
                return (
                    f"( now() >= (as_local({s_ev}()){s_off}) ) "
                    f"or ( now() <= (as_local({e_ev}()){e_off}) )"
                )
            return (
                f"( now() >= (as_local({s_ev}()){s_off}) ) "
                f"and ( now() <= (as_local({e_ev}()){e_off}) )"
            )

        # MIXED (clock ↔ sun or others):
        # Use a conservative check: after start AND before end in wall-clock sense,
        # relying on HA updating templates minutely. This won’t be perfect for
        # all edge cases but is robust enough for typical use.
        def single_edge(ts: Any, edge: str) -> str:
            if isinstance(ts, dict) and ts.get("kind") == "clock":
                hhmm = ts["value"]
                if edge == "after":
                    return f"( now().strftime('%H:%M') >= '{hhmm}' )"
                else:
                    return f"( now().strftime('%H:%M') <= '{hhmm}' )"
            if isinstance(ts, dict) and ts.get("kind") == "sun":
                ev = ts["event"]; off = _jinja_offset(ts.get("offset", "0s"))
                if edge == "after":
                    return f"( now() >= (as_local({ev}()){off}) )"
                else:
                    return f"( now() <= (as_local({ev}()){off}) )"
            # entity/CNAME: treat as boolean state('on')
            if isinstance(ts, str):
                return f"( is_state('{ts}', 'on') )"
            return "true"

        return f"( {single_edge(start,'after')} and {single_edge(end,'before')} )"

    for c in clauses or []:
        op = (c.get("op") or "enable").lower()
        st = c.get("from")
        en = c.get("to", c.get("until"))
        if st is None and en is None:
            # degenerate clause -> true
            expr = "true"
        elif st is not None and en is not None:
            expr = ts_to_expr_in_window(st, en)
        else:
            # only 'from' or only 'to' present → treat as single-edge guard
            ts = st if st is not None else en
            if isinstance(ts, dict) and ts.get("kind") == "clock":
                hhmm = ts["value"]
                expr = f"( now().strftime('%H:%M') >= '{hhmm}' )" if st is not None else f"( now().strftime('%H:%M') <= '{hhmm}' )"
            elif isinstance(ts, dict) and ts.get("kind") == "sun":
                ev = ts["event"]; off = _jinja_offset(ts.get("offset","0s"))
                expr = f"( now() >= (as_local({ev}()){off}) )" if st is not None else f"( now() <= (as_local({ev}()){off}) )"
            elif isinstance(ts, str):
                expr = f"( is_state('{ts}', 'on') )"
            else:
                expr = "true"

        if op == "enable":
            enable_exprs.append(expr)
        elif op == "disable":
            disable_exprs.append(expr)

    if not enable_exprs and not disable_exprs:
        state_tpl = "true"
    else:
        en = " or ".join(f"({e})" for e in enable_exprs) if enable_exprs else "true"
        dis = " or ".join(f"({d})" for d in disable_exprs) if disable_exprs else "false"
        state_tpl = f"( {en} ) and not ( {dis} )"

    # Template binary_sensor block (for inclusion under template: -> binary_sensor:)
    # Home Assistant expects structure:
    # template:
    #   - binary_sensor:
    #       - name: ...
    #         unique_id: ...
    #         state: "{{ ... }}"
    return {
        "name": entity_id.split(".", 1)[1],
        "unique_id": entity_id.split(".", 1)[1],
        "state": f"{{{{ {state_tpl} }}}}"
    }

def _collect_named_schedules(ir: IRProgram) -> Iterable[Dict]:
    """
    Collect named schedules from IR in either object, list, or dict form.
    Accepted shapes:
      - IRProgram.schedules: list of objects with .name/.clauses
      - IRProgram.schedules: dict{name: [clauses]}
      - (fallback) ir is dict-like: ir["schedules"] in above forms
    Yields dicts like {"name": str, "clauses": list}
    """
    def yield_list(seq):
        for s in seq or []:
            if hasattr(s, "name"):
                name = getattr(s, "name", None)
                clauses = getattr(s, "clauses", []) or []
            elif isinstance(s, dict):
                name = s.get("name")
                clauses = s.get("clauses", []) or []
            else:
                name, clauses = None, []
            if name:
                yield {"name": name, "clauses": clauses}

    # Primary: IR attribute
    schedules_attr = getattr(ir, "schedules", None)
    if isinstance(schedules_attr, dict):
        for name, clauses in schedules_attr.items():
            if name:
                yield {"name": str(name), "clauses": clauses or []}
        return
    if isinstance(schedules_attr, (list, tuple)):
        yield from yield_list(schedules_attr)
        return

    # Fallback: dict-style IR
    if isinstance(ir, dict):
        sched = ir.get("schedules")
        if isinstance(sched, dict):
            for name, clauses in sched.items():
                if name:
                    yield {"name": str(name), "clauses": clauses or []}
            return
        if isinstance(sched, (list, tuple)):
            yield from yield_list(sched)
            return

    # Last resort: scan statements/raw_statements for schedule_decl dicts
    candidates = getattr(ir, "statements", None) or getattr(ir, "raw_statements", None) or []
    for s in candidates:
        if isinstance(s, dict) and s.get("type") == "schedule_decl":
            name = s.get("name")
            if name:
                yield {"name": name, "clauses": s.get("clauses", []) or []}

# ----------------------------
# Main package emission
# ----------------------------
def emit_package(ir: IRProgram, outdir: str):
    ensure_dir(outdir)

    print("DEBUG:", getattr(ir, "schedules", None))
    # derive package slug early; use IR package if present
    pkg = getattr(ir, "package", None) or _pkg_slug(outdir)
    sched_reg = ScheduleRegistry(pkg)

    helpers: Dict = {"input_text": {}, "input_boolean": {}, "input_number": {}}
    scripts: Dict = {"script": {}}
    automations: List[Dict] = []

    # ---------- PASS 1: create named schedule helpers ONCE per (pkg, name) ----------
    for s in _collect_named_schedules(ir):
        if not s.get("name"):
            continue
        sched_reg.register_decl(s["name"], s.get("clauses", []))

    # ---------- Context helpers for entities & per-prop contexts ----------
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

    # ---------- Proxies ----------
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

    # ---------- Writer scripts per (sync, member, prop) ----------
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

    # ---------- Upstream automations ----------
    for s in ir.syncs:
        for p in s.properties:
            prop = p.name
            triggers = []
            conditions = []
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
                cfg = PROP_CONFIG.get(prop, {})
                attr = cfg.get("upstream", {}).get("attr", prop)

                # state trigger on attribute
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

    # ---------- Downstream automations ----------
    for s in ir.syncs:
        invert_set = set(getattr(s, "invert", []) or [])
        for p in s.properties:
            prop = p.name
            if prop == "onoff":
                trigger = [{"platform":"state","entity_id": f"input_boolean.hassl_{_safe(s.name)}_onoff"}]
                actions = []
                for m in s.members:
                    dom = _domain(m)
                    cond_tpl = "{{ is_state('%s','on') != is_state('%s','on') }}" % (f"input_boolean.hassl_{_safe(s.name)}_onoff", m)
                    # flip target services if this member is inverted
                    inv = (m in invert_set)
                    service_on  = _turn_service(dom, not inv)  # proxy ON -> turn_on unless inverted
                    service_off = _turn_service(dom, inv)      # proxy OFF -> turn_off unless inverted
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

    # ---------- Write YAML ----------
    # helpers & scripts
    _dump_yaml(os.path.join(outdir, f"helpers_{pkg}.yaml"), helpers, ensure_sections=True)
    _dump_yaml(os.path.join(outdir, f"scripts_{pkg}.yaml"), scripts)

    # schedule helpers (template binary_sensors) once
    if sched_reg.sensors:
        _dump_yaml(
            os.path.join(outdir, f"schedules_{pkg}.yaml"),
            {"template": [{"binary_sensor": sched_reg.sensors}]}
        )

    # automations per sync
    for s in ir.syncs:
        doc = [a for a in automations if a["alias"].startswith(f"HASSL sync {s.name}")]
        if doc:
            _dump_yaml(os.path.join(outdir, f"sync_{pkg}_{_safe(s.name)}.yaml"), {"automation": doc})
