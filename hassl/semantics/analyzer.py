from dataclasses import dataclass
from typing import Dict, List, Any
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

@dataclass
class IRProgram:
    aliases: Dict[str, str]
    syncs: List[IRSync]
    rules: List[IRRule]
    def to_dict(self):
        return {
            "aliases": self.aliases,
            "syncs": [{
                "name": s.name, "kind": s.kind, "members": s.members,
                "invert": s.invert, "properties": [p.name for p in s.properties]
            } for s in self.syncs],
            "rules": [{"name": r.name, "clauses": r.clauses} for r in self.rules],
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
                cond = _walk_alias(c.condition, amap)
                acts = _walk_alias(c.actions, amap)
                clauses.append({"condition": cond, "actions": acts})
            rules.append(IRRule(s.name, clauses))

    return IRProgram(aliases=amap, syncs=syncs, rules=rules)
