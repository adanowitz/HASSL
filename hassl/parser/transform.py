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

def _to_str(x):
    return str(x) if not isinstance(x, Token) else str(x)

@v_args(inline=True)
class HasslTransformer(Transformer):
    def __init__(self):
        super().__init__()
        self.stmts = []

    # --- Program / Aliases / Syncs ---
    def start(self, *stmts):
        return nodes.Program(statements=self.stmts)

    def alias(self, name, entity):
        a = nodes.Alias(name=str(name), entity=str(entity))
        self.stmts.append(a); return a

    def sync(self, synctype, members, name, syncopts=None):
        invert = []
        if isinstance(syncopts, list): invert = syncopts
        s = nodes.Sync(kind=str(synctype), members=members, name=str(name), invert=invert)
        self.stmts.append(s); return s

    def synctype(self, tok): return str(tok)
    def syncopts(self, *args): return list(args)[-1] if args else []
    def entity_list(self, *entities): return [str(e) for e in entities]
    def member(self, val): return val
    def entity(self, *parts): return ".".join(str(p) for p in parts)

    # --- Rules / if_clause ---
    def rule(self, name, *if_clauses):
        r = nodes.Rule(name=str(name), clauses=list(if_clauses))
        self.stmts.append(r); return r

    # if_clause: "if" "(" expr qualifier? ")" qualifier? "then" actions
    def if_clause(self, *parts):
        actions = parts[-1]
        core = list(parts[:-1])
        expr = core[0]
        quals = [q for q in core[1:] if isinstance(q, dict) and "not_by" in q]

        cond = {"expr": expr}
        if quals:
            cond.update(quals[-1])  # prefer last qualifier
        return nodes.IfClause(condition=cond, actions=actions)

    # --- Condition & boolean ops ---
    def condition(self, expr, qual=None):
        cond = {"expr": expr}
        if qual is not None:
            cond.update(qual)
        return cond

    def qualifier(self, *args):
        # Normalize tokens to strings first
        sargs = [str(a) for a in args]
        if len(sargs) == 1:
            return {"not_by": sargs[0]}
        if len(sargs) == 2 and sargs[0] == "rule":
            return {"not_by": {"rule": sargs[1]}}
        return {"not_by": "this"}

    def or_(self, left, right):  return {"op": "or", "left": left, "right": right}
    def and_(self, left, right): return {"op": "and", "left": left, "right": right}
    def not_(self, term):        return {"op": "not", "value": term}

    def comparison(self, left, op=None, right=None):
        if op is None: return left
        return {"op": str(op), "left": left, "right": right}

    def bare_operand(self, val): return _atom(val)
    def operand(self, val): return _atom(val)
    def OP(self, tok): return str(tok)

    # --- Actions ---
    def actions(self, *acts): return list(acts)
    def action(self, act): return act

    def dur(self, n, unit):
        return f"{int(str(n))}{str(unit)}"

    def assign(self, name, state, *for_parts):
        act = {"type": "assign", "target": str(name), "state": str(state)}
        if for_parts: act["for"] = for_parts[0]
        return act

    def attr_assign(self, *parts):
        value = _atom(parts[-1])
        cnames = [str(p) for p in parts[:-1]]
        attr = cnames[-1]
        entity = ".".join(cnames[:-1])
        return {"type":"attr_assign","entity": entity, "attr": attr, "value": value}

    def waitact(self, cond, dur, action):
        return {"type": "wait", "condition": cond, "for": dur, "then": action}

    # Robust parse for:
    #   disable rule NAME for 3m
    #   enable  rule NAME until sunrise
    #   disable rule NAME
    #   NAME for 3m           (literals dropped by Lark)
    #   NAME 3m               (even 'for' dropped)
    def rulectrl(self, *parts):
        from lark import Token

        def s(x):  # normalize tokens -> str/primitive
            return str(x) if isinstance(x, Token) else x

        vals = [s(p) for p in parts]

        # 1) op: scan for 'disable' | 'enable' (may be absent if Lark dropped literals)
        op = None
        for v in vals:
            if isinstance(v, str) and v.lower() in ("disable", "enable"):
                op = v.lower()
                break
        if not op:
            # Sensible default (we currently only emit 'disable' from DSL examples)
            op = "disable"

        # 2) name: prefer token after literal 'rule', else first non-keyword string
        name = None
        keywords = {"rule", "for", "until", "disable", "enable"}
        if "rule" in [str(v).lower() for v in vals if isinstance(v, str)]:
            rs = [i for i, v in enumerate(vals) if isinstance(v, str) and v.lower() == "rule"]
            for i in rs:
                if i + 1 < len(vals):
                    name = vals[i + 1]
                    break
        if name is None:
            for v in vals:
                if isinstance(v, str) and v.lower() not in keywords:
                    name = v
                    break
        if name is None:
            raise ValueError(f"rulectrl: could not determine rule name from parts={vals!r}")

        # 3) tail: look for "for DUR" | "until TIMEPOINT".
        # If those literals are missing, accept a bare duration (e.g., '3m') after the name.
        payload = {}
        # indices after name for scanning tail
        try:
            start_idx = vals.index(name) + 1
        except ValueError:
            start_idx = 1

        i = start_idx
        while i < len(vals):
            v = vals[i]
            vlow = str(v).lower() if isinstance(v, str) else ""
            if vlow == "for" and i + 1 < len(vals):
                payload["for"] = vals[i + 1]
                i += 2
                continue
            if vlow == "until" and i + 1 < len(vals):
                payload["until"] = vals[i + 1]
                i += 2
                continue
            i += 1

        # Bare duration fallback: e.g., parts == [name, '3m']
        if not payload:
            # crude dur check: ends with a known unit
            units = ("ms", "s", "m", "h", "d")
            for v in vals[start_idx:]:
                if isinstance(v, str) and any(v.endswith(u) for u in units):
                    payload["for"] = v
                    break

        # If still nothing, keep IR consistent (treat as immediate toggle window)
        if not payload:
            payload["for"] = "0s"

        return {"type": "rule_ctrl", "op": op, "rule": str(name), **payload}
    
    def tagact(self, name, val):
        return {"type": "tag", "name": str(name), "value": _atom(val)}
