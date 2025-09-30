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
    schedule_uses: List[str] = None
    schedules_inline: List[dict] = None

@dataclass
class IRProgram:
    aliases: Dict[str, str]
    syncs: List[IRSync]
    rules: List[IRRule]
    schedules: Dict[str, List[dict]] = None
    
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
    amap: Dict[str,str] = {}
    for s in prog.statements:
        if isinstance(s, Alias): amap[s.name]=s.entity

    syncs: List[IRSync] = []
    rules: List[IRRule] = []

    for s in prog.statements:
        if isinstance(s, Sync):
            mem = [_resolve_alias(m,amap) for m in s.members]
            inv = [_resolve_alias(m,amap) for m in s.invert]
            props = _props_for_sync(s.kind, mem)
            syncs.append(IRSync(s.name, s.kind, mem, inv, props))

    for s in prog.statements:
        if isinstance(s, Rule):
            clauses = []
            for c in s.clauses:
                # Only transform IfClause-like items (schedules are dicts, ignore here)
                if hasattr(c, "condition") and hasattr(c, "actions"):
                    cond = _walk_alias(c.condition, amap)
                    acts = _walk_alias(c.actions, amap)
                    clauses.append({"condition": cond, "actions": acts})
                else:
                    # schedule_use / schedule_inline dicts or anything else → skip for now
                    # (rules_min/codegen doesn't consume them yet)
                    continue
                
            rules.append(IRRule(s.name, clauses))

    scheds: Dict[str, List[dict]] = {}

    for st in prog.statements:
        # schedule_decl comes from the transformer as a dict: {"type":"schedule_decl","name":..., "clauses":[...]}
        if isinstance(st, dict) and st.get("type") == "schedule_decl":
            name = st.get("name")
            clauses = st.get("clauses", []) or []
            if isinstance(name, str) and name.strip():
                # No alias resolution needed inside schedule clauses; they are time specs
                scheds[name] = clauses

    # --- Rules ---
    ir_rules: List[IRRule] = []
    for r in [s for s in prog.statements if isinstance(s, Rule)]:
        clauses: List[dict] = []
        schedule_uses: List[str] = []
        schedules_inline: List[dict] = []

        for c in r.clauses:
            # IfClause nodes have .condition and .actions
            if hasattr(c, "condition") and hasattr(c, "actions"):
                cond = _walk_alias(c.condition, amap)
                acts = _walk_alias(c.actions, amap)
                clauses.append({"condition": cond, "actions": acts})
            elif isinstance(c, dict) and c.get("type") == "schedule_use":
                # e.g. {"type":"schedule_use","names":[...]}
                schedule_uses.extend([str(n) for n in (c.get("names") or []) if isinstance(n, str)])
            elif isinstance(c, dict) and c.get("type") == "schedule_inline":
                # e.g. {"type":"schedule_inline","clauses":[...]}
                for sc in c.get("clauses") or []:
                    if isinstance(sc, dict):
                        schedules_inline.append(sc)
            else:
                # unknown clause → ignore
                pass

        ir_rules.append(IRRule(
            name=r.name,
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
