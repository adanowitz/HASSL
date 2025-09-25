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
        if len(args) == 1 and isinstance(args[0], str):
            return {"not_by": args[0]}
        if len(args) == 2 and str(args[0]) == "rule":
            return {"not_by": {"rule": str(args[1])}}
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

        # Accept flexible args; support:
    #   disable rule NAME for 3m
    #   enable rule NAME until sunrise
    #   disable rule NAME
    def rulectrl(self, *parts):
        # Normalize tokens -> python values
        def norm(x):
            from lark import Token
            if isinstance(x, Token):
                return str(x)
            return x

        vals = [norm(p) for p in parts]

        # op is always first ("disable" | "enable")
        if not vals:
            raise ValueError("rulectrl: missing op")
        op = str(vals[0]).lower()

        # Find NAME (first CNAME after 'rule' if present; else first CNAME-ish)
        name = None
        i = 1
        while i < len(vals):
            v = vals[i]
            if isinstance(v, str) and v.lower() == "rule":
                # next token should be the rule name
                if i + 1 < len(vals):
                    name = str(vals[i + 1])
                    i += 2
                    break
            i += 1
        if name is None:
            # fallback: first string that isn't a keyword
            keywords = {"rule", "for", "until", "enable", "disable"}
            for v in vals[1:]:
                if isinstance(v, str) and v.lower() not in keywords:
                    name = v
                    break
        if name is None:
            raise ValueError(f"rulectrl: could not determine rule name from parts={vals!r}")

        # Parse tail: "for <dur>" or "until <timepoint>"
        payload = {}
        # scan remaining elements after we consumed name (or after op if fallback)
        try:
            tail_start = vals.index("rule") + 2
        except ValueError:
            # no explicit 'rule' literal; start after op and name
            tail_start = max(2, vals.index(name) + 1 if name in vals else 2)

        i = tail_start
        while i < len(vals):
            v = str(vals[i]).lower()
            if v == "for":
                if i + 1 < len(vals):
                    payload["for"] = vals[i + 1]  # duration already normalized by dur()
                    i += 2
                    continue
            elif v == "until":
                if i + 1 < len(vals):
                    payload["until"] = vals[i + 1]  # keep as string/timepoint atom
                    i += 2
                    continue
            i += 1

        # If nothing specified, treat as immediate toggle with 0s to keep IR consistent
        if not payload:
            payload["for"] = "0s"

        return {"type": "rule_ctrl", "op": op, "rule": str(name), **payload}

    def tagact(self, name, val):
        return {"type": "tag", "name": str(name), "value": _atom(val)}
