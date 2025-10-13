from dataclasses import dataclass
from typing import Dict, List, Any, Optional, Tuple
from ..ast.nodes import Program, Alias, Sync, Rule, Schedule
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
    """
    Import + package semantics
    -------------------------
    - Supports:
        import pkg.*                      # glob injects public aliases & schedules
        import pkg: a, b as c             # list import (with optional renames)
        import pkg as ns                  # qualified access via 'ns.x'
    - Resolution uses a global export registry if provided by the build:
        GLOBAL_EXPORTS: Dict[(pkg, kind, name), node]
      where kind ∈ {"alias","schedule"} and node is Alias|Schedule.
      Falls back to intra-file visibility if GLOBAL_EXPORTS absent.
    """
    package_name: str = prog.package or ""

    # Local (this file) exports — public only (private stays local)
    local_aliases: Dict[str, str] = {}
    local_schedules: Dict[str, List[dict]] = {}
    local_public: Dict[Tuple[str, str, str], Any] = {}  # (pkg, kind, name) -> node

    # 1) Collect local declarations (aliases & schedules)
    for s in prog.statements:
        if isinstance(s, Alias):
            local_aliases[s.name] = s.entity
            is_private = getattr(s, "private", False)
            if not is_private:
                local_public[(package_name, "alias", s.name)] = s
        elif isinstance(s, dict) and s.get("type") == "schedule_decl":
            name = s.get("name")
            if isinstance(name, str) and name.strip():
                local_schedules.setdefault(name, []).extend(s.get("clauses", []) or [])
                if not s.get("private", False):
                    # wrap in a lightweight Schedule node shape for export table parity
                    local_public[(package_name, "schedule", name)] = Schedule(name=name, clauses=s.get("clauses", []), private=False)
        elif isinstance(s, Schedule):
            local_schedules.setdefault(s.name, []).extend(s.clauses or [])
            if not getattr(s, "private", False):
                local_public[(package_name, "schedule", s.name)] = s

    # 2) Build import view
    # Maps for analysis:
    injected_aliases: Dict[str, str] = {}      # local name -> entity id (from imports)
    imported_schedules: Dict[str, Tuple[str, str]] = {}  # local name -> (pkg, name)
    qualified_prefixes: Dict[str, str] = {}    # ns -> module path (for "import X as ns")

    # Helper to resolve from global exports if available; otherwise, from locals only
    def _get_export(mod: str, kind: str, name: str) -> Optional[Any]:
        key = (mod, kind, name)
        if 'GLOBAL_EXPORTS' in globals():
            return globals()['GLOBAL_EXPORTS'].get(key)
        # intra-file fallback: only resolve if the target module == this file's package
        if mod == package_name:
            return local_public.get(key)
        return None

        # Warn or raise if imported modules aren't in GLOBAL_EXPORTS
    def _check_import_exists(mod: str):
        """Emit a warning if module not found in GLOBAL_EXPORTS (user likely compiled only a subdir)."""
        if 'GLOBAL_EXPORTS' not in globals():
            return  # single-file compile, skip
        exists = any(pkg == mod for (pkg, _kind, _name) in globals()['GLOBAL_EXPORTS'])
        if not exists:
            import sys
            print(f"[hasslc] WARNING: imported module '{mod}' not found in build inputs "
                  f"(run hasslc from a directory that includes it)", file=sys.stderr)
        
    # Dig through Program.imports if present (transformer supplies a normalized list)
    for imp in getattr(prog, "imports", []) or []:
        if not isinstance(imp, dict) or imp.get("type") != "import":
            # transformer may also append sentinels to statements; ignore here
            continue
        mod = imp.get("module", "")
        kind = imp.get("kind")
        _check_import_exists(mod)
        if kind == "glob":
            # bring in all public aliases & schedules from 'mod'
            source = globals().get('GLOBAL_EXPORTS', local_public)
            for (pkg, k, nm), node in source.items():
                if pkg != mod or k not in ("alias", "schedule"):
                    continue
                if k == "alias" and isinstance(node, Alias):
                    injected_aliases[nm] = node.entity
                elif k == "schedule":
                    imported_schedules[nm] = (pkg, nm)
        elif kind == "list":
            for it in imp.get("items") or []:
                nm = it.get("name")
                as_nm = it.get("as") or nm
                # prefer alias, then schedule
                node = _get_export(mod, "alias", nm)
                if isinstance(node, Alias):
                    injected_aliases[as_nm] = node.entity
                    continue
                node = _get_export(mod, "schedule", nm)
                if isinstance(node, Schedule) or (isinstance(node, dict) and node.get("type") == "schedule_decl"):
                    imported_schedules[as_nm] = (mod, nm)
                    continue
                raise KeyError(f"ImportError: module '{mod}' has no public symbol '{nm}'")
        elif kind == "alias":
            # import mod as ns  -> track mapping for qualified access
            ns = imp.get("as")
            if not ns:
                raise KeyError(f"ImportError: missing alias name for module '{mod}'")
            qualified_prefixes[str(ns)] = mod
        else:
            # ignore unknown import kinds gracefully
            pass

    # 3) Merge alias maps: imported first, then local (locals win)
    amap: Dict[str, str] = {**injected_aliases, **local_aliases}

    # --- Syncs ---
    syncs: List[IRSync] = []
    for s in prog.statements:
        if isinstance(s, Sync):
            mem = [_resolve_alias(m,amap) for m in s.members]
            inv = [_resolve_alias(m,amap) for m in s.invert]
            props = _props_for_sync(s.kind, mem)
            syncs.append(IRSync(s.name, s.kind, mem, inv, props))

    # --- Top-level schedules (from transformer or Schedule nodes) ---
    scheds: Dict[str, List[dict]] = {}
    # seed with locals collected earlier (so we keep a single source of truth)
    for nm, cls in local_schedules.items():
        scheds.setdefault(nm, []).extend(cls)

    # --- Rules (with schedule use/inline) ---
    rules: List[IRRule] = []

    def _resolve_qualified_alias(name: str) -> Optional[str]:
        """
        Resolve a dotted alias reference like 'ns.light_alias' via 'import pkg as ns'.
        Returns the entity string if found, else None.
        """
        if "." not in name:
            return None
        head, tail = name.split(".", 1)
        mod = qualified_prefixes.get(head)
        if not mod:
            return None
        node = _get_export(mod, "alias", tail)
        if isinstance(node, Alias):
            return node.entity
        return None

    def _resolve_schedule_name(nm: str) -> str:
        """
        Normalize a schedule identifier to a friendly resolved string:
        - local name: keep as-is
        - imported list/glob: keep local alias (as imported), but if we know the
          source package, annotate as 'pkg.name' for consistency
        - qualified 'ns.x': resolve 'ns' to module and return 'module.x' if found
        """
        # local schedule?
        if nm in local_schedules:
            return f"{package_name+'.' if package_name else ''}{nm}"
        # imported by name (list or glob)
        if nm in imported_schedules:
            pkg, base = imported_schedules[nm]
            return f"{pkg}.{base}"
        # qualified via ns
        if "." in nm:
            head, tail = nm.split(".", 1)
            mod = qualified_prefixes.get(head)
            if mod:
                node = _get_export(mod, "schedule", tail)
                if node is not None:
                    return f"{mod}.{tail}"
        # unknown — leave as-is (analyzer will not fail here; emitter/runner can)
        return nm

    # Upgrade alias resolution to support qualified 'ns.aliasName' in expr/actions
    def _walk_alias_with_qualified(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: _walk_alias_with_qualified(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_walk_alias_with_qualified(x) for x in obj]
        if isinstance(obj, str):
            # first try local/unqualified map
            if "." not in obj and obj in amap:
                return amap[obj]
            # then try qualified import alias
            ent = _resolve_qualified_alias(obj)
            if ent:
                return ent
        return obj
    
    for s in prog.statements:
        if isinstance(s, Rule):
            clauses: List[dict] = []
            schedule_uses: List[str] = []
            schedules_inline: List[dict] = []

            for c in s.clauses:
                # IfClause-like items have .condition/.actions
                if hasattr(c, "condition") and hasattr(c, "actions"):
                    cond = _walk_alias_with_qualified(c.condition)
                    acts = _walk_alias_with_qualified(c.actions)
                    clauses.append({"condition": cond, "actions": acts})
                elif isinstance(c, dict) and c.get("type") == "schedule_use":
                    # {"type":"schedule_use","names":[...]}
                    raw = [str(n) for n in (c.get("names") or []) if isinstance(n, str)]
                    # normalize each schedule name
                    schedule_uses.extend([_resolve_schedule_name(n) for n in raw])
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
