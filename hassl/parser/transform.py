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
        self.package = None
        self.imports = []
        
    # --- Program / Aliases / Syncs ---
    def start(self, *stmts):
        # We’ve been accumulating into self.stmts to preserve order.
        # Prefer newer Program signature if available; fallback otherwise.
        try:
            return nodes.Program(statements=self.stmts, package=self.package,
                                 imports=self.imports)
        except TypeError:
            # Keep backward-compat by returning statements-only Program.
            # (Analyzer can optionally find package/imports via sentinel
            # dicts in self.stmts if you choose to append them.)
            return nodes.Program(statements=self.stmts)

    # alias: PRIVATE? "alias" CNAME "=" entity
    def alias(self, *args):
        # Handles both legacy (name, entity) and new (PRIVATE?, name,
        #entity) shapes.
        private = False
        if len(args) == 2:
            name, entity = args
        else:
            # (priv, name, entity)
            priv_tok, name, entity = args
            private = True if isinstance(priv_tok, Token) and priv_tok.type == "PRIVATE" else bool(priv_tok)
        try:
            a = nodes.Alias(name=str(name), entity=str(entity), private=private)
        except TypeError:
            # nodes.Alias may not yet accept 'private'; store on a dict for analyzer to read.
            a = nodes.Alias(name=str(name), entity=str(entity))
            setattr(a, "private", private)
        self.stmts.append(a)
        return a

    def sync(self, synctype, members, name, syncopts=None):
        invert = []
        if isinstance(syncopts, list):
            invert = syncopts
        s = nodes.Sync(kind=str(synctype), members=members, name=str(name), invert=invert)
        self.stmts.append(s)
        return s

    def synctype(self, tok): return str(tok)
    def syncopts(self, *args): return list(args)[-1] if args else []
    def entity_list(self, *entities): return [str(e) for e in entities]
    def member(self, val): return val
    def entity(self, *parts): return ".".join(str(p) for p in parts)

    # ================
    # Package / Import
    # ================
    # package_decl: "package" entity
    def package_decl(self, _pkg_kw, dotted):
        self.package = str(dotted)
        # Optionally keep a sentinel in stmts for older analyzers:
        self.stmts.append({"type": "package", "name": self.package})
        return self.package

    # import_stmt: "import" entity import_tail
    # import_tail: ".*" | ":" import_list | "as" CNAME
    def import_stmt(self, _imp_kw, module, tail):
        mod = str(module)
        kind, items, as_name = tail
        imp = {"type": "import", "module": mod, "kind": kind, "items": items, "as": as_name}
        self.imports.append(imp)
        # Also drop a sentinel in stmts for backward-compat if desired:
        self.stmts.append({"type": "import", **imp})
        return imp

    def import_tail(self, *args):
        # normalized to (kind, items, as_name)
        # Shapes from the parser:
        #   ".*"                  -> ( "glob", [], None )
        #   ":" import_list       -> ( "list", [items], None )
        #   "as" CNAME            -> ( "alias", [], "name" )
        if len(args) == 1 and isinstance(args[0], Token) and str(args[0]) == ".*":
            return ("glob", [], None)
        if len(args) == 2 and isinstance(args[0], Token) and str(args[0]) == ":":
            return ("list", args[1], None)
        if len(args) == 2 and isinstance(args[0], Token) and args[0].type == "AS":
            # Some Lark configs may not emit AS; grammar uses literal "as". Handle generically:
            pass
        if len(args) == 2 and isinstance(args[0], str) and args[0] == "as":
            return ("alias", [], str(args[1]))
        if len(args) == 2 and isinstance(args[0], Token) and str(args[0]) == "as":
            return ("alias", [], str(args[1]))
        # Fallback — treat as glob
        return ("glob", [], None)

    def import_list(self, *items): return list(items)

    # import_item: CNAME ("as" CNAME)?
    def import_item(self, *parts):
        if len(parts) == 1:
            return {"name": str(parts[0]), "as": None}
        # (name, "as", alias) or (name, Token('as'), alias)
        return {"name": str(parts[0]), "as": str(parts[-1])}

    # --- Rules / if_clause ---
    def rule(self, name, *clauses):
        # clauses may include IfClause nodes AND schedule_* dicts (we keep both).
        r = nodes.Rule(name=str(name), clauses=list(clauses))
        self.stmts.append(r)
        return r

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
        if op is None:
            return left
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
        if for_parts:
            act["for"] = for_parts[0]
        return act

    def attr_assign(self, *parts):
        value = _atom(parts[-1])
        cnames = [str(p) for p in parts[:-1]]
        attr = cnames[-1]
        entity = ".".join(cnames[:-1])
        return {"type": "attr_assign", "entity": entity, "attr": attr, "value": value}

    def waitact(self, cond, dur, action):
        return {"type": "wait", "condition": cond, "for": dur, "then": action}

    # Robust rule control
    def rulectrl(self, *parts):
        from lark import Token

        def s(x):  # normalize tokens -> str/primitive
            return str(x) if isinstance(x, Token) else x

        vals = [s(p) for p in parts]

        # op
        op = None
        for v in vals:
            if isinstance(v, str) and v.lower() in ("disable", "enable"):
                op = v.lower()
                break
        if not op:
            op = "disable"

        # name
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

        # tail
        payload = {}
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

        if not payload:
            units = ("ms", "s", "m", "h", "d")
            for v in vals[start_idx:]:
                if isinstance(v, str) and any(v.endswith(u) for u in units):
                    payload["for"] = v
                    break

        if not payload:
            payload["for"] = "0s"

        return {"type": "rule_ctrl", "op": op, "rule": str(name), **payload}

    def tagact(self, name, val):
        return {"type": "tag", "name": str(name), "value": _atom(val)}

    # ======================
    # Schedules (composable)
    # ======================

    # schedule_decl: PRIVATE? SCHEDULE CNAME ":" schedule_clause+
    def schedule_decl(self, *parts):
        idx = 0
        private = False
        if idx < len(parts) and isinstance(parts[idx], Token) and parts[idx].type == "PRIVATE":
            private = True; idx += 1
        # next must be SCHEDULE
        if idx < len(parts) and isinstance(parts[idx], Token) and parts[idx].type == "SCHEDULE":
            idx += 1
        if idx >= len(parts):
            raise ValueError("schedule_decl: missing schedule name")
        name = str(parts[idx]); idx += 1
        # optional ":" token
        if idx < len(parts) and isinstance(parts[idx], Token) and str(parts[idx]) == ":":
            idx += 1
        clauses = [c for c in parts[idx:] if isinstance(c, dict) and c.get("type") == "schedule_clause"]
        node = {"type": "schedule_decl", "name": name, "clauses": clauses, "private": private}
        self.stmts.append(node)
        return node
    
    # rule_schedule_use: SCHEDULE USE name_list ";"
    def rule_schedule_use(self, _sched_kw, _use_kw, names, _semi=None):
        return {"type": "schedule_use", "names": [str(n) for n in names]}

    # rule_schedule_inline: SCHEDULE schedule_clause+
    def rule_schedule_inline(self, _sched_kw, *clauses):
        clist = [c for c in clauses if isinstance(c, dict) and c.get("type") == "schedule_clause"]
        return {"type": "schedule_inline", "clauses": clist}

    # schedule_clause: schedule_op FROM time_spec schedule_end? ";"
    def schedule_clause(self, op, _from_kw, start, end=None, _semi=None):
        d = {"type": "schedule_clause", "op": str(op), "from": start}
        if isinstance(end, dict):
            d.update(end)  # {"to": ...} or {"until": ...}
        return d

    # schedule_op: ENABLE | DISABLE
    def schedule_op(self, tok):
        return str(tok).lower()

    # schedule_end: TO time_spec -> schedule_to
    def schedule_to(self, _to_kw, ts):
        return {"to": ts}

    # schedule_end: UNTIL time_spec -> schedule_until
    def schedule_until(self, _until_kw, ts):
        return {"until": ts}

    # name_list: CNAME ("," CNAME)*
    def name_list(self, *names):
        return [str(n) for n in names]

    # time_spec: TIME_HHMM -> time_clock
    def time_clock(self, tok):
        return {"kind": "clock", "value": str(tok)}

    # sun_spec: (SUNRISE|SUNSET) OFFSET? -> time_sun
    def time_sun(self, event_tok, offset_tok=None):
        event = str(event_tok).lower()
        off = str(offset_tok) if offset_tok is not None else "0s"
        return {"kind": "sun", "event": event, "offset": off}

    # Unwrap rule_clause so clauses list contains IfClause nodes and/or dicts
    def rule_clause(self, item):
        return item
