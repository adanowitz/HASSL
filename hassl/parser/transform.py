# Transformer patch: robust dur() and stable synctype via explicit terminals
from lark import Transformer, v_args, Token
from ..ast import nodes

def _atom(val):
    if isinstance(val, Token):
        t = val.type
        s = str(val)
        if t in ("INT",):
            return int(s)
        if t in ("SIGNED_NUMBER","NUMBER"):
            try:
                return int(s)
            except ValueError:
                return float(s)
        if t in ("CNAME", "STATE", "UNIT", "ONOFF", "DIMMER", "ATTRIBUTE", "SHARED", "ALL"):
            return s
        if t == "STRING":
            return s[1:-1]
    return val

@v_args(inline=True)
class HasslTransformer(Transformer):
    def __init__(self):
        super().__init__()
        self.stmts = []

    def start(self, *stmts):
        return nodes.Program(statements=self.stmts)

    # alias
    def alias(self, name, entity):
        a = nodes.Alias(name=str(name), entity=str(entity))
        self.stmts.append(a)
        return a

    # sync
    def sync(self, synctype, members, name, syncopts=None):
        invert = []
        if isinstance(syncopts, list):
            invert = syncopts
        s = nodes.Sync(kind=str(synctype), members=members, name=str(name), invert=invert)
        self.stmts.append(s); return s

    # synctype now always receives one explicit terminal (ONOFF/DIMMER/ATTRIBUTE/SHARED/ALL)
    def synctype(self, tok):
        return str(tok)

    def syncopts(self, *args): return list(args)[-1] if args else []
    def entity_list(self, *entities): return [str(e) for e in entities]
    def entity(self, *parts): return ".".join(str(p) for p in parts)

    # rules
    def rule(self, name, *if_clauses):
        r = nodes.Rule(name=str(name), clauses=list(if_clauses))
        self.stmts.append(r); return r

    def if_clause(self, condition, actions): return nodes.IfClause(condition=condition, actions=actions)

    # ---- condition + expr ----
    def qualifier(self, *args):
        if len(args) == 1 and isinstance(args[0], str):
            return {"not_by": args[0]}
        if len(args) == 2 and str(args[0]) == "rule":
            return {"not_by": {"rule": str(args[1])}}
        return {"not_by": "this"}

    def condition(self, expr, qual=None):
        cond = {"expr": expr}
        if qual is not None: cond.update(qual)
        return cond

    def or_(self, left, right):  return {"op": "or", "left": left, "right": right}
    def and_(self, left, right): return {"op": "and", "left": left, "right": right}
    def not_(self, term):        return {"op": "not", "value": term}

    def comparison(self, left, op=None, right=None):
        if op is None: return left
        return {"op": str(op), "left": left, "right": right}

    def bare_operand(self, val): return _atom(val)
    def operand(self, val): return _atom(val)
    def OP(self, tok): return str(tok)

    # ---- actions ----
    def actions(self, *acts): return list(acts)
    def action(self, act): return act  # unwrap Tree('action', ...)

    def dur(self, n, unit):
        return f"{int(str(n))}{str(unit)}"

    def assign(self, name, state, *for_parts):
        act = {"type": "assign", "target": str(name), "state": str(state)}
        if for_parts:
            act["for"] = for_parts[0]  # already normalized by dur()
        return act

    def attr_assign(self, *parts):
        value = _atom(parts[-1])
        cnames = [str(p) for p in parts[:-1]]
        attr = cnames[-1]
        entity = ".".join(cnames[:-1])
        return {"type":"attr_assign","entity": entity, "attr": attr, "value": value}

    def waitact(self, cond, dur, action):
        return {"type": "wait", "condition": cond, "for": dur, "then": action}

    def rulectrl(self, op, _rule_kw, name, dur):
        return {"type": "rule_ctrl", "op": str(op), "rule": str(name), "for": dur}

    def tagact(self, name, val):
        return {"type": "tag", "name": str(name), "value": _atom(val)}
