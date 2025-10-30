from typing import Dict, List, Iterable, Any, Tuple, Optional
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

def _gate_entity_for_schedule(resolved: str, is_window: bool) -> str:
    # resolved is "pkg.name" (your analyzer already normalizes)
    pkg, name = resolved.rsplit(".", 1) if "." in resolved else ("", resolved)
    if is_window:
        return f"input_boolean.hassl_sched_{_safe(pkg)}_{_safe(name)}"
    return f"binary_sensor.hassl_schedule_{_safe(pkg)}_{_safe(name)}_active"

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
    period_cache: Dict[Tuple[str,str], str] = field(default_factory=dict)  # (sched, key)-> entity_id
    
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

    # New: create/reuse a period template sensor for a schedule window
    def ensure_period_sensor(self, sched_name: str, period: Dict[str, Any] | None) -> str | None:
        if not period:
            return None
        key = (sched_name, str(period))
        if key in self.period_cache:
            return self.period_cache[key]
        # Build a compact name with hash for stability
        eid_name = f"hassl_period_{self.pkg}_{_safe(sched_name)}_{abs(hash(str(period)))%100000}"
        entity_id = f"binary_sensor.{eid_name}"
        tpl = _period_template(period)
        self.sensors.append({"name": eid_name, "unique_id": eid_name, "state": f"{{{{ {tpl} }}}}"})
        self.period_cache[key] = entity_id
        return entity_id

# ---------- NEW: window helpers (mirrors rules_min logic) ----------
def _parse_offset(off: str) -> str:
    if not off: return "00:00:00"
    m = re.fullmatch(r"([+-])(\d+)(ms|s|m|h|d)", str(off).strip())
    if not m: return "00:00:00"
    sign, n, unit = m.group(1), int(m.group(2)), m.group(3)
    seconds = {"ms": 0, "s": n, "m": n*60, "h": n*3600, "d": n*86400}[unit]
    h = seconds // 3600
    m_ = (seconds % 3600) // 60
    s = seconds % 60
    return f"{sign}{h:02d}:{m_:02d}:{s:02d}"

def _wrap_tpl(expr: str) -> str:
    """Ensure a Jinja expression is wrapped safely in {{ … }}."""
    expr = expr.strip()
    if expr.startswith("{{") and expr.endswith("}}"):
        return expr
    return "{{ " + expr + " }}"

def _clock_between_cond(hhmm_start: str, hhmm_end: str):
    # Pure expression (no {% %} / inner {{ }}), safe to embed in {{ ... }}
    ns = "now().strftime('%H:%M')"
    s  = hhmm_start
    e  = hhmm_end

    expr = (
        f"( ('{s}' < '{e}' and ({ns} >= '{s}' and {ns} < '{e}')) "
        f"or ('{s}' >= '{e}' and ({ns} >= '{s}' or {ns} < '{e}')) )"
    )
    
    return {
        "condition": "template",
        "value_template": _wrap_tpl(expr)
    }

def _sun_edge_cond(edge: str, ts: dict):
    event = ts.get("event", "sunrise")
    off = _parse_offset(ts.get("offset", "0s"))
    cond = {"condition": "sun", edge: event}
    if off and off != "00:00:00":
        cond["offset"] = off
    return cond

def _window_condition_from_specs(start_ts, end_ts):
    # clock → clock
    if isinstance(start_ts, dict) and start_ts.get("kind") == "clock" and \
       isinstance(end_ts, dict) and end_ts.get("kind") == "clock":
        s = start_ts.get("value", "00:00")
        e = end_ts.get("value", "00:00")
        return _clock_between_cond(s, e)
    # sun → sun
    if isinstance(start_ts, dict) and start_ts.get("kind") == "sun" and \
       isinstance(end_ts, dict) and end_ts.get("kind") == "sun":
        after_start = _sun_edge_cond("after", start_ts)
        before_end  = _sun_edge_cond("before", end_ts)
        wrap = (start_ts.get("event") == "sunset" and end_ts.get("event") == "sunrise")
        if wrap:
            return {"condition": "or", "conditions": [after_start, before_end]}
        return {"condition": "and", "conditions": [after_start, before_end]}
    # mixed → minute-of-day template (pure expression, no {% %})
    NOWM = "(now().hour*60 + now().minute)"
    SM = "(((start.value[0:2]|int)*60 + (start.value[3:5]|int)) if start.kind == 'clock' else (as_local(state_attr('sun.sun','next_' ~ start.event)).hour*60 + as_local(state_attr('sun.sun','next_' ~ start.event)).minute))"
    EM = "(((end.value[0:2]|int)*60 + (end.value[3:5]|int)) if end.kind == 'clock' else (as_local(state_attr('sun.sun','next_' ~ end.event)).hour*60 + as_local(state_attr('sun.sun','next_' ~ end.event)).minute))"
    return {
        "condition": "template",
        "value_template": (
            f"( ({SM} < {EM} and ({NOWM} >= {SM} and {NOWM} < {EM})) "
            f"or ({SM} >= {EM} and ({NOWM} >= {SM} or {NOWM} < {EM})) )"
        ),
        "variables": {"start": start_ts or {}, "end": end_ts or {}}
    }
    

def _day_selector_condition(sel: Optional[str]):
    if sel == "weekdays":
        return {"condition": "time", "weekday": ["mon","tue","wed","thu","fri"]}
    if sel == "weekends":
        return {"condition": "time", "weekday": ["sat","sun"]}
    # daily / None
    return None

def _holiday_condition(mode: Optional[str], hol_id: Optional[str]):
    if not (mode and hol_id):
        return None
    # True when today is a holiday for 'only', false when 'except'
    eid = f"binary_sensor.hassl_holiday_{hol_id}"
    return {"condition": "state", "entity_id": eid, "state": "on" if mode == "only" else "off"}
    
def _trigger_for(ts: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a HA trigger for a time-spec dict:
      - {"kind":"clock","value":"HH:MM"}  -> time at HH:MM:00
      - {"kind":"sun","event":"sunrise|sunset","offset":"+15m"} -> sun trigger (with offset)
    """
    if isinstance(ts, dict) and ts.get("kind") == "clock":
        hhmm = ts.get("value", "00:00")
        at = hhmm if len(hhmm) == 8 else (hhmm + ":00" if len(hhmm) == 5 else "00:00:00")
        return {"platform": "time", "at": str(at)}
    if isinstance(ts, dict) and ts.get("kind") == "sun":
        trig = {"platform": "sun", "event": ts.get("event", "sunrise")}
        off = _parse_offset(ts.get("offset", "0s"))
        if off and off != "00:00:00":
           trig["offset"] = off
        return trig
    # Fallback: evaluate soon; maintenance automation will correct state anyway
    return {"platform": "time_pattern", "minutes": "/1"}

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

# ---------- NEW: period sensor template builders ----------
def _period_template(period: Dict[str, Any]) -> str:
    """
    period is a dict of shape:
      {"kind":"months","data":{"list":[Mon,...]}} or {"kind":"months","data":{"range":[A,B]}}
      {"kind":"dates","data":{"start":"MM-DD","end":"MM-DD"}}   # can wrap year
      {"kind":"range","data":{"start":"YYYY-MM-DD","end":"YYYY-MM-DD"}}
    Returns a Jinja boolean expression.
    """
    kind = period.get("kind")
    data = period.get("data", {})
    
    if kind == "months":
        def m2n(m: str) -> int:
            order = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
            return order.index(m)+1
        if "list" in data:
            months = [m2n(m) for m in data["list"]]
            return f"( now().month in {months} )"
        if "range" in data:
            a, b = [m2n(x) for x in data["range"]]
            return (
                f"( ({a} <= now().month <= {b}) or "
                f"  ({a} > {b} and (now().month >= {a} or now().month <= {b})) )"
            )

    if kind == "dates":
        # Compare zero-padded strings '%m-%d' (lexicographic works).
        start = data.get("start"); end = data.get("end")
        d = "now().strftime('%m-%d')"
        return (
            f"( ('{start}' <= {d} <= '{end}') or "
            f"  ('{start}' > '{end}' and ({d} >= '{start}' or {d} <= '{end}')) )"
        )
    if kind == "range":
        start = data.get("start"); end = data.get("end")
        # Keep this as a pure expression using filters.
        return (
            f"( now().date() >= '{start}'|as_datetime|date and "
            f"  now().date() <= '{end}'|as_datetime|date )"
        )
    return "true"

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

    # derive package slug early; use IR package if present
    pkg = getattr(ir, "package", None) or _pkg_slug(outdir)
    sched_reg = ScheduleRegistry(pkg)

    helpers: Dict = {"input_text": {}, "input_boolean": {}, "input_number": {}}
    scripts: Dict = {"script": {}}
    automations: List[Dict] = []

    # We no longer emit legacy YAML 'platform: workday' sections.
    # Only emit template sensors that reference UI-defined Workday entities.
    holiday_tpl_defs: List[Dict] = []
    
    # ---------- PASS 1: create named schedule helpers ONCE per (pkg, name) ----------
    for s in _collect_named_schedules(ir):
        if not s.get("name"):
            continue
        sched_reg.register_decl(s["name"], s.get("clauses", []))

    # ---------- PASS 1b: Holidays -> (template only; Workday via UI) ----------
    # ir.holidays is {"id": {...}}; we only need the id to reference UI entities.
    holidays_ir = getattr(ir, "holidays", {}) or {}
    if holidays_ir:
        for hid, h in holidays_ir.items():
            # Template: holiday = NOT(not_holiday)
            # Assumes you configured a UI Workday instance that:
            #   - has workdays = Mon..Sun
            #   - excludes = ['holiday']
            # and renamed it to: binary_sensor.hassl_<id>_not_holiday
            eid_name = f"hassl_holiday_{hid}"
            holiday_tpl_defs.append({
                "name": eid_name,
                "unique_id": eid_name,
                "state": "{{ is_state('binary_sensor.hassl_" + hid + "_not_holiday', 'off') }}"
            })

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
                
                ptype = PROP_CONFIG.get(prop, {}).get("proxy", {}).get("type")
                if ptype == "input_text":
                    proxy_e = f"input_text.hassl_{_safe(s.name)}_{prop}"
                elif ptype == "input_boolean":
                    proxy_e = f"input_boolean.hassl_{_safe(s.name)}_{prop}"
                else:
                    proxy_e = f"input_number.hassl_{_safe(s.name)}_{prop}"

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
                ptype = PROP_CONFIG.get(prop, {}).get("proxy", {}).get("type")
                if ptype == "input_text":
                    proxy_e = f"input_text.hassl_{_safe(s.name)}_{prop}"
                elif ptype == "input_boolean":
                    proxy_e = f"input_boolean.hassl_{_safe(s.name)}_{prop}"
                else:
                    proxy_e = f"input_number.hassl_{_safe(s.name)}_{prop}"

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

    # ---------- New schedule windows (emit input_boolean + minute/sun maintenance automation) ----------
    # IR provides schedules_windows: { name: [ {start,end,day_selector,period,holiday_*} ] }
    sched_windows_ir = getattr(ir, "schedules_windows", {}) or {}
    per_schedule_automations: Dict[str, List[Dict]] = {}
    for sched_name, wins in sched_windows_ir.items():
        # Ensure schedule boolean exists in helpers (include pkg prefix!)
        sched_bool_key = f"hassl_sched_{_safe(pkg)}_{_safe(sched_name)}"
        helpers["input_boolean"][sched_bool_key] = {
            "name": f"HASSL Schedule {pkg}.{sched_name}"
        }
        bool_eid = f"input_boolean.{sched_bool_key}"

        # --- Back-compat: emit '_active' template mirrors that follow the input_boolean ---
        # 1) Primary mirror: binary_sensor.hassl_schedule_<pkg>_<name>_active
        pkg_safe = _safe(pkg)
        mirror_name = f"hassl_schedule_{pkg_safe}_{_safe(sched_name)}_active"
        sched_reg.sensors.append({
            "name": mirror_name,
            "unique_id": mirror_name,
            "state": "{{ is_state('" + bool_eid + "', 'on') }}"
        })
        # 2) Legacy alias (no pkg): binary_sensor.hassl_schedule_automations_<name>_active
        # Some existing rulegen referenced this older name; keep it as a thin mirror.
        legacy_alias = f"hassl_schedule_automations_{_safe(sched_name)}_active"
        sched_reg.sensors.append({
            "name": legacy_alias,
            "unique_id": legacy_alias,
            "state": "{{ is_state('" + bool_eid + "', 'on') }}"
        })

        # Build OR-of-windows condition bundles
        or_conditions: List[Dict[str, Any]] = []
        need_sun_triggers = False

        for idx, w in enumerate(wins):

            ds = w.get("day_selector")
            href = w.get("holiday_ref")
            hmode = w.get("holiday_mode")
            period = w.get("period")

            # Coerce time specs to dicts compatible with _trigger_for/_window_condition_from_specs
            raw_start = w.get("start")
            raw_end   = w.get("end")
            def _coerce(ts):
                if isinstance(ts, dict):
                    return ts
                if isinstance(ts, str):
                    # accept "HH:MM" or "HH:MM:SS"
                    v = ts if len(ts) in (5,8) else "00:00"
                    return {"kind":"clock","value": v[:5] if len(v)==5 else v[:8]}
                return {"kind":"clock","value":"00:00"}
            start_ts = _coerce(raw_start)
            end_ts   = _coerce(raw_end)

            # window condition (handles clock↔sun and wrap)
            window_cond = _window_condition_from_specs(start_ts, end_ts)
            if start_ts.get("kind") == "sun" or end_ts.get("kind") == "sun":
                need_sun_triggers = True

            # day selector & holiday & period
            conds_and = [ c for c in (_day_selector_condition(ds),
                                      _holiday_condition(hmode, href),
                                      window_cond)
                          if c is not None ]
            period_eid = sched_reg.ensure_period_sensor(sched_name, period)
            if period_eid:
                conds_and.append({"condition":"state", "entity_id": period_eid, "state":"on"})
            or_conditions.append({ "condition": "and", "conditions": conds_and })

            # --- Per-window explicit ON/OFF automations (edges only) ---
            edge_conds = [ _day_selector_condition(ds), _holiday_condition(hmode, href) ]
            if period_eid:
                edge_conds.append({"condition": "state", "entity_id": period_eid, "state": "on"})
                edge_conds = [c for c in edge_conds if c]

            on_auto = {
                "alias": f"HASSL schedule {pkg}.{sched_name} on_{idx}",
                "mode": "single",
                "trigger": [ _trigger_for(start_ts) ],
                "action": [ { "service": "input_boolean.turn_on", "target": {"entity_id": bool_eid} } ]
            }
            ec = [c for c in edge_conds if c]
            if ec:
                on_auto["condition"] = ec
            per_schedule_automations.setdefault(sched_name, []).append(on_auto)

            off_auto = {
                "alias": f"HASSL schedule {pkg}.{sched_name} off_{idx}",
                "mode": "single",
                "trigger": [ _trigger_for(end_ts) ],
                "action": [ { "service": "input_boolean.turn_off", "target": {"entity_id": bool_eid} } ]
            }
            if ec:
                off_auto["condition"] = ec
            per_schedule_automations.setdefault(sched_name, []).append(off_auto)
            
        # Composite choose: ON when any window matches, else OFF
        choose_block = [{
            "conditions": [{ "condition": "or", "conditions": or_conditions }] if or_conditions else [{"condition":"template","value_template":"false"}],
            "sequence": [{"service": "input_boolean.turn_on", "target": {"entity_id": bool_eid}}]
        }]
        

        triggers = [
            {"platform": "time_pattern", "minutes": "/1"},
            # Re-evaluate on HA restart so the boolean is correct immediately
            {"platform": "homeassistant", "event": "start"},
        ]
        if need_sun_triggers:
            # Nudge immedately at edges so the boolean flips promptly
            triggers.extend([
                {"platform": "sun", "event": "sunrise"},
                {"platform": "sun", "event": "sunset"}
            ])

        per_schedule_automations.setdefault(sched_name, []).append({
            "alias": f"HASSL schedule {pkg}.{sched_name} maint",
            "mode": "single",
            "trigger": triggers,
            "condition": [],
            "action": [
                {"choose": choose_block,
                 "default": [{"service": "input_boolean.turn_off", "target": {"entity_id": bool_eid}}]
                }
            ]
        })

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

    # Holidays file: emit only the template sensors; Workday instances are created via UI
    if holiday_tpl_defs:
        hol_doc: Dict[str, Any] = {}
        hol_doc["template"] = [{"binary_sensor": holiday_tpl_defs}]
        _dump_yaml(os.path.join(outdir, f"holidays_{pkg}.yaml"), hol_doc)

    # automations per sync
    for s in ir.syncs:
        doc = [a for a in automations if a["alias"].startswith(f"HASSL sync {s.name}")]
        if doc:
            _dump_yaml(os.path.join(outdir, f"sync_{pkg}_{_safe(s.name)}.yaml"), {"automation": doc})

    # automations per schedule (new windows)
    for sched_name, autos in per_schedule_automations.items():
        if autos:
            _dump_yaml(
                os.path.join(outdir, f"schedule_{pkg}_{_safe(sched_name)}.yaml"),
                {"automation": autos}
            )
