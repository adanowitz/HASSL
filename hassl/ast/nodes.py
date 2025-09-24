from dataclasses import dataclass, asdict, field
from typing import List, Any, Dict

@dataclass
class Alias:
    name: str
    entity: str

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
class Rule:
    name: str
    clauses: List[IfClause]

@dataclass
class Program:
    statements: List[object]
    def to_dict(self):
        def enc(x):
            if isinstance(x, (Alias, Sync, Rule, IfClause)):
                d = asdict(x); d["type"] = x.__class__.__name__; return d
            return x
        return {"type": "Program","statements": [enc(s) for s in self.statements]}
