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

# ---- NEW: Holiday sets & structured schedule windows ----
@dataclass
class HolidaySet:
    id: str
    country: str
    province: Optional[str] = None
    add: List[str] = field(default_factory=list)       # YYYY-MM-DD
    remove: List[str] = field(default_factory=list)
    workdays: List[str] = field(default_factory=lambda: ["mon","tue","wed","thu","fri"])
    excludes: List[str] = field(default_factory=lambda: ["sat","sun","holiday"])

@dataclass
class PeriodSelector:
    # kind = 'months' | 'dates' | 'range'
    kind: str
    # data:
    #  - months: {"list":[Mon,...]} or {"range":[Mon,Mon]}
    #  - dates:  {"start":"MM-DD","end":"MM-DD"}
    #  - range:  {"start":"YYYY-MM-DD","end":"YYYY-MM-DD"}
    data: Dict[str, Any]

@dataclass
class ScheduleWindow:
    start: str                    # "HH:MM"
    end: str                      # "HH:MM"
    day_selector: str             # "weekdays" | "weekends" | "daily"
    period: Optional[PeriodSelector] = None
    holiday_ref: Optional[str] = None   # id from HolidaySet (for 'except'/'only')
    holiday_mode: Optional[str] = None  # "except" | "only" | None

@dataclass
class Schedule:
    name: str
    # raw clauses as produced by the transformer (legacy form)
    clauses: List[Dict[str, Any]]
    # structured windows for the new 'on ...' syntax (optional)
    windows: List[ScheduleWindow] = field(default_factory=list)
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
            if isinstance(x, (Alias, Sync, Rule, IfClause, Schedule,
                              HolidaySet, ScheduleWindow, PeriodSelector)):
                d = asdict(x); d["type"] = x.__class__.__name__; return d
            return x
        return {
            "type": "Program",
            "package": self.package,
            "imports": self.imports,
            "statements": [enc(s) for s in self.statements],
        }
