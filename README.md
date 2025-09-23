# HASSL — Home Assistant Simple Scripting Language

HASSL is a tiny domain-specific language for writing **simple, declarative automations** for [Home Assistant](https://www.home-assistant.io/).  
It lets you describe synchronization groups and rules in **one line or two** instead of dozens of lines of YAML or Node-RED JSON.

---

## ✨ Features

- **Aliases** for entities  
- **Sync groups** (`onoff`, `dimmer`, `all_shared`, `all`) with *implied loop-safety*  
- **Rules** with boolean conditions, waits, and auto-reverts  
- **Race-proof** semantics via Home Assistant context IDs (`not_by this|rule|any_hassl`)  
- **Rule control** (`disable` / `enable`) and **tags** for metadata  

---

## 📐 Grammar (EBNF)

```ebnf
program        = { statement } ;

statement      = alias_stmt | sync_stmt | rule_stmt ;

alias_stmt     = "alias" ident "=" entity ;

sync_stmt      = "sync" sync_type "[" entity_list "]" "as" ident [ sync_opts ] ;
sync_type      = "onoff" | "dimmer" | "attribute" | "all_shared" | "all" ;
sync_opts      = "{" [ sync_opt { ";" sync_opt } ] "}" ;
sync_opt       = "invert" ":" entity_list ;

entity_list    = entity { "," entity } ;

rule_stmt      = "rule" ident ":" { if_clause } ;
if_clause      = "if" "(" condition ")" "then" actions ;

condition      = expression [ qualifier ] ;
qualifier      = "not_by" ( "this" | "any_hassl" | "rule(" ident ")" ) ;

actions        = action { ";" action } ;
action         = assignment | wait_action | rule_ctrl | tag_action ;

assignment     = ident "=" state [ "for" duration ] ;
wait_action    = "wait" "(" condition "for" duration ")" action ;
rule_ctrl      = ("disable" | "enable") "rule" ident "for" duration ;
tag_action     = "tag" ident "=" value ;

expression     = or_expr ;
or_expr        = and_expr { "||" and_expr } ;
and_expr       = unary_expr { "&&" unary_expr } ;
unary_expr     = "!" unary_expr
               | "(" expression ")"
               | comparison ;

comparison     = operand ( "==" | "!=" | "<" | ">" | "<=" | ">=" ) value
               | operand ;

entity         = ident ( "." ident )+ ;
ident          = letter { letter | digit | "_" } ;
state          = "on" | "off" ;
duration       = number ( "ms" | "s" | "m" | "h" | "d" ) ;
