from dataclasses import dataclass, asdict, field
from typing import List, Any, Dict, Optional

@dataclass
class Alias:
    name: str
    entity: str
    private: bool = False

@dataclass
class Sync:
    kind: str
    members: List[str]
    name: str
    invert: List[str] = field(default_factory=list)

@dataclass
class IfClause:
    condition: Dict[str, Any]
    actions: List[Dict[str, Any]]

@dataclass
class Schedule:
    name: str
    # raw clauses as produced by the transformer, e.g. {"type":"schedule_clause", ...}
    clauses: List[Dict[str, Any]]
    private: bool = False

@dataclass
class Rule:
    name: str
    # allow schedule dicts
    clauses: List[Any]

@dataclass
class Program:
    statements: List[object]
    package: Optional[str] = None
    # normalized import entries (dicts) from the transformer:
    #   {"type":"import","module": "...", "kind": "glob|list|alias", "items":
    #[...], "as": "name"|None}
    imports: List[Dict[str, Any]] = field(default_factory=list)    
    def to_dict(self):
        def enc(x):
            if isinstance(x, (Alias, Sync, Rule, IfClause, Schedule)):
                d = asdict(x); d["type"] = x.__class__.__name__; return d
            return x
        return {
            "type": "Program",
            "package": self.package,
            "imports": self.imports,
            "statements": [enc(s) for s in self.statements],
        }
