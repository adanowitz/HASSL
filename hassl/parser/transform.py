from lark import Transformer, v_args, Token, Tree
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
        try:
            return nodes.Program(statements=self.stmts, package=self.package,
                                 imports=self.imports)
        except TypeError:
            return nodes.Program(statements=self.stmts)

    # alias: PRIVATE? "alias" CNAME "=" entity
    def alias(self, *args):
        private = False
        if len(args) == 2:
            name, entity = args
        else:
            priv_tok, name, entity = args
            private = True if isinstance(priv_tok, Token) and priv_tok.type == "PRIVATE" else bool(priv_tok)
        try:
            a = nodes.Alias(name=str(name), entity=str(entity), private=private)
        except TypeError:
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
    def package_decl(self, *children):
        if not children:
            raise ValueError("package_decl: missing children")
        dotted = children[-1]  # handle optional literal "package"
        self.package = str(dotted)
        self.stmts.append({"type": "package", "name": self.package})
        return self.package

    # ---- NEW: module_ref to support bare or dotted imports ----
    # module_ref: CNAME ("." CNAME)*
    def module_ref(self, *parts):
        return ".".join(str(p) for p in parts)

    # import_stmt: "import" module_ref import_tail?
    def import_stmt(self, *children):
        """
        Accepts:
          [module_ref]                       -> bare:  import aliases
          [module_ref, import_tail]          -> import home.shared: x, y
          ["import", module_ref, ...]        -> if the literal sneaks in
        Normalizes to:
          {"type":"import","module":<str>,"kind":<glob|list|alias|none>,
           "items":[...], "as":<str|None>}
        """
        if not children:
            return None

        # If the literal "import" is present, drop it.
        if isinstance(children[0], Token) and str(children[0]) == "import":
            children = children[1:]

        if len(children) == 1:
            module = children[0]
            tail = None
        elif len(children) == 2:
            module, tail = children
        else:
            raise ValueError(f"import_stmt: unexpected children {children!r}")

        # module_ref should already be a str (via module_ref()), but normalize just in case
        if isinstance(module, Tree) and module.data == "module_ref":
            module = ".".join(str(t.value) for t in module.children)
        else:
            module = str(module)

        # Normalize tail
        kind, items, as_name = ("none", [], None)
        if tail is not None:
            if isinstance(tail, tuple) and len(tail) == 3:
                kind, items, as_name = tail
            else:
                # Defensive: try to parse tail-like shapes
                norm = self.import_tail(tail)
                if isinstance(norm, tuple) and len(norm) == 3:
                    kind, items, as_name = norm

        imp = {"type": "import", "module": module, "kind": kind, "items": items, "as": as_name}
        self.imports.append(imp)
        self.stmts.append({"type": "import", **imp})
        return imp

    # import_tail: ".*" | ":" import_list | "as" CNAME
    # normalize to a tuple: (kind, items, as_name)
    def import_tail(self, *args):
        # Forms we might see:
        #   (Token('.*'),)                          -> glob
        #   (Token('":"'), import_list_tree)        -> list
        #   (Token('AS',"as"), Token('CNAME',...))  -> alias
        if len(args) == 1 and isinstance(args[0], Token):
            if str(args[0]) == ".*":
                return ("glob", [], None)

        if len(args) == 2:
            a0, a1 = args
            # ":" import_list
            if isinstance(a0, Token) and str(a0) == ":":
                # a1 should already be a python list via import_list()
                return ("list", a1 if isinstance(a1, list) else [a1], None)
            # "as" CNAME  (either literal or tokenized)
            if (isinstance(a0, Token) and str(a0) == "as") or (isinstance(a0, str) and a0 == "as"):
                return ("alias", [], str(a1))

        # Already normalized (kind, items, as_name)
        if len(args) == 3 and isinstance(args[0], str):
            return args  # trust caller

        # Optional tail missing or unknown -> "none"
        return ("none", [], None)

    def import_list(self, *items): return list(items)

    # import_item: CNAME ("as" CNAME)?
    def import_item(self, *parts):
        if len(parts) == 1:
            return {"name": str(parts[0]), "as": None}
        return {"name": str(parts[0]), "as": str(parts[-1])}

    # --- Rules / if_clause ---
    def rule(self, name, *clauses):
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
        def s(x): return str(x) if isinstance(x, Token) else x
        vals = [s(p) for p in parts]

        op = next((v.lower() for v in vals if isinstance(v, str) and v.lower() in ("disable","enable")), "disable")

        name = None
        keywords = {"rule", "for", "until", "disable", "enable"}
        if "rule" in [str(v).lower() for v in vals if isinstance(v, str)]:
            for i, v in enumerate(vals):
                if isinstance(v, str) and v.lower() == "rule" and i + 1 < len(vals):
                    name = vals[i + 1]; break
        if name is None:
            for v in vals:
                if isinstance(v, str) and v.lower() not in keywords:
                    name = v; break
        if name is None:
            raise ValueError(f"rulectrl: could not determine rule name from parts={vals!r}")

        payload = {}
        try:
            start_idx = vals.index(name) + 1
        except ValueError:
            start_idx = 1

        i = start_idx
        while i < len(vals):
            v = vals[i]; vlow = str(v).lower() if isinstance(v, str) else ""
            if vlow == "for" and i + 1 < len(vals):
                payload["for"] = vals[i + 1]; i += 2; continue
            if vlow == "until" and i + 1 < len(vals):
                payload["until"] = vals[i + 1]; i += 2; continue
            i += 1

        if not payload:
            for v in vals[start_idx:]:
                if isinstance(v, str) and any(v.endswith(u) for u in ("ms","s","m","h","d")):
                    payload["for"] = v; break

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
        if idx < len(parts) and isinstance(parts[idx], Token) and parts[idx].type == "SCHEDULE":
            idx += 1
        if idx >= len(parts):
            raise ValueError("schedule_decl: missing schedule name")
        name = str(parts[idx]); idx += 1
        if idx < len(parts) and isinstance(parts[idx], Token) and str(parts[idx]) == ":":
            idx += 1
        clauses = [c for c in parts[idx:] if isinstance(c, dict) and c.get("type") == "schedule_clause"]
        node = {"type": "schedule_decl", "name": name, "clauses": clauses, "private": private}
        self.stmts.append(node)
        return node
    
    # rule_schedule_use: SCHEDULE USE name_list ";"
    def rule_schedule_use(self, _sched_kw, _use_kw, names, _semi=None):
        norm = [n if isinstance(n, str) else str(n) for n in names]
        return {"type": "schedule_use", "names": norm}

    # rule_schedule_inline: SCHEDULE schedule_clause+
    def rule_schedule_inline(self, _sched_kw, *clauses):
        clist = [c for c in clauses if isinstance(c, dict) and c.get("type") == "schedule_clause"]
        return {"type": "schedule_inline", "clauses": clist}

    # schedule_clause: schedule_op FROM time_spec schedule_end? ";"
    def schedule_clause(self, op, _from_kw, start, end=None, _semi=None):
        d = {"type": "schedule_clause", "op": str(op), "from": start}
        if isinstance(end, dict):
            d.update(end)
        return d

    def schedule_op(self, tok):
        return str(tok).lower()

    def schedule_to(self, _to_kw, ts):
        return {"to": ts}

    def schedule_until(self, _until_kw, ts):
        return {"until": ts}

    def name_list(self, *names):
        return [n if isinstance(n, str) else str(n) for n in names]

    def name(self, val):
        return str(val)

    def time_clock(self, tok):
        return {"kind": "clock", "value": str(tok)}

    def time_sun(self, event_tok, offset_tok=None):
        event = str(event_tok).lower()
        off = str(offset_tok) if offset_tok is not None else "0s"
        return {"kind": "sun", "event": event, "offset": off}

    def time_spec(self, *children):
        return children[0] if children else None

    def rule_clause(self, item):
        return item
