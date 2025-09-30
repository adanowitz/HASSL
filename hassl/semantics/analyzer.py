from dataclasses import dataclass
from typing import Dict, List, Any, Optional
from ..ast.nodes import Program, Alias, Sync, Rule
from .domains import DOMAIN_PROPS, domain_of

@dataclass
class IRSyncedProp:
    name: str

@dataclass
class IRSync:
    name: str
    kind: str
    members: List[str]
    invert: List[str]
    properties: List[IRSyncedProp]

@dataclass
class IRRule:
    name: str
    clauses: List[dict]
    schedule_uses: Optional[List[str]] = None
    schedules_inline: Optional[List[dict]] = None

@dataclass
class IRProgram:
    aliases: Dict[str, str]
    syncs: List[IRSync]
    rules: List[IRRule]
    schedules: Optional[Dict[str, List[dict]]] = None
    
    def to_dict(self):
        return {
            "aliases": self.aliases,
            "syncs": [{
                "name": s.name, "kind": s.kind, "members": s.members,
                "invert": s.invert, "properties": [p.name for p in s.properties]
            } for s in self.syncs],
            "rules": [{
                "name": r.name,
                "clauses": r.clauses,
                "schedule_uses": r.schedule_uses or [],
                "schedules_inline": r.schedules_inline or []
            } for r in self.rules],
            "schedules": self.schedules or {},
        }

def _resolve_alias(e: str, amap: Dict[str,str]) -> str:
    if "." not in e and e in amap: return amap[e]
    return e

def _walk_alias(obj: Any, amap: Dict[str,str]) -> Any:
    if isinstance(obj, dict): return {k:_walk_alias(v,amap) for k,v in obj.items()}
    if isinstance(obj, list): return [_walk_alias(x,amap) for x in obj]
    if isinstance(obj, str) and "." not in obj and obj in amap: return amap[obj]
    return obj

def _props_for_sync(kind: str, members: List[str]) -> List[IRSyncedProp]:
    domains = [domain_of(m) for m in members]
    prop_sets = [DOMAIN_PROPS.get(d, set()) for d in domains]
    if kind == "shared":
        if not prop_sets: return []
        shared = set.intersection(*map(set, prop_sets))
        return [IRSyncedProp(p) for p in sorted(shared)]
    if kind == "all":
        from collections import Counter
        c = Counter()
        for s in prop_sets:
            for p in s: c[p]+=1
        return [IRSyncedProp(p) for p,n in c.items() if n>=2]
    if kind == "onoff":
        return [IRSyncedProp("onoff")]
    if kind == "dimmer":
        base = {"onoff","brightness"}
        if all("color_temp" in s for s in prop_sets):
            base.add("color_temp")
        return [IRSyncedProp(p) for p in sorted(base)]
    return []

def analyze(prog: Program) -> IRProgram:
    # --- Aliases ---
    amap: Dict[str,str] = {}
    for s in prog.statements:
        if isinstance(s, Alias):
            amap[s.name] = s.entity

    # --- Syncs ---
    syncs: List[IRSync] = []
    for s in prog.statements:
        if isinstance(s, Sync):
            mem = [_resolve_alias(m,amap) for m in s.members]
            inv = [_resolve_alias(m,amap) for m in s.invert]
            props = _props_for_sync(s.kind, mem)
            syncs.append(IRSync(s.name, s.kind, mem, inv, props))

    # --- Top-level schedules (from transformer) ---
    scheds: Dict[str, List[dict]] = {}
    for st in prog.statements:
        # transformer emits: {"type":"schedule_decl","name":..., "clauses":[...]}
        if isinstance(st, dict) and st.get("type") == "schedule_decl":
            name = st.get("name")
            clauses = st.get("clauses", []) or []
            if isinstance(name, str) and name.strip():
                # no aliasing inside time specs
                scheds.setdefault(name, []).extend(clauses)

    # --- Rules (with schedule use/inline) ---
    rules: List[IRRule] = []
    for s in prog.statements:
        if isinstance(s, Rule):
            clauses: List[dict] = []
            schedule_uses: List[str] = []
            schedules_inline: List[dict] = []

            for c in s.clauses:
                # IfClause-like items have .condition/.actions
                if hasattr(c, "condition") and hasattr(c, "actions"):
                    cond = _walk_alias(c.condition, amap)
                    acts = _walk_alias(c.actions, amap)
                    clauses.append({"condition": cond, "actions": acts})
                elif isinstance(c, dict) and c.get("type") == "schedule_use":
                    # {"type":"schedule_use","names":[...]}
                    schedule_uses.extend([str(n) for n in (c.get("names") or []) if isinstance(n, str)])
                elif isinstance(c, dict) and c.get("type") == "schedule_inline":
                    # {"type":"schedule_inline","clauses":[...]}
                    for sc in c.get("clauses") or []:
                        if isinstance(sc, dict):
                            schedules_inline.append(sc)
                else:
                    # ignore unknown fragments
                    pass

            rules.append(IRRule(
                name=s.name,
                clauses=clauses,
                schedule_uses=schedule_uses,
                schedules_inline=schedules_inline
            ))

    return IRProgram(
        aliases=amap,
        syncs=syncs,
        rules=rules,
        schedules=scheds
    )
