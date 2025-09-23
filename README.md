# HASSL — Home Assistant Simple Scripting Language

HASSL is a tiny domain-specific language for writing **simple, declarative automations** for [Home Assistant](https://www.home-assistant.io/).  
It lets you describe synchronization groups and rules in **one line or two** instead of dozens of lines of YAML or Node-RED JSON.

---

## ✨ Features

- **Aliases** for entities  
- **Sync groups** (`onoff`, `dimmer`, `shared`, `all`) with *implied loop-safety*  
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
sync_type      = "onoff" | "dimmer" | "attribute" | "shared" | "all" ;
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
```

---

## 🔧 Semantics

### Aliases
```hassl
alias light = light.living
alias motion = binary_sensor.hall_motion
alias lux    = sensor.living_luminance
```

### Sync groups
```hassl
sync onoff [light.kitchen, switch.floor] as circuit
sync shared [light.kitchen, switch.floor] as shared_sync
sync all [light.desk, light.strip, switch.floor] as mixed_sync { invert: switch.floor }
```

- **onoff**: sync only on/off state  
- **dimmer**: sync on/off + brightness (and color temp if supported)  
- **shared**: sync properties supported by *all* members  
- **all**: sync any property supported by *≥ 2* members  
- **invert**: optional, only for on/off

> 🔒 All syncs have **implied `not_by this` guards** → no race loops, no debounce required.

### Rules
```hassl
rule motion_on_light:
  if (light == off && motion && lux < 50)
  then light = on for 10m
```

- **Assignments** can have a `for` duration → auto-revert  
- **Conditions** can use `&&`, `||`, `!`, comparisons

### Waits
```hassl
rule switch_keep_on:
  if (light == on not_by any_hassl)
  then wait (!motion for 1h) light = off
```

- `wait (COND for DUR) A` suspends until condition holds continuously, then performs action.

### Rule control
```hassl
rule switch_off_disable_motion:
  if (light transitions off not_by this)
  then disable rule motion_on_light for 3m
```

### Tags
```hassl
if (light == on not_by any_hassl) then tag override = "manual"
```

Tags are stored in helpers; can be used for debugging or extra logic.

---

## 🛠 Compiler Mapping

HASSL compiles to a **Home Assistant package** containing:

- **Helpers**
  - `input_text.hassl_ctx__<entity>[__<prop>]` for context tracking  
  - `input_boolean.hassl_rule__<rule>__enabled` for rule gating  
  - `input_boolean` / `input_number` proxies for sync groups  
- **Writer scripts**  
  - Scripts that stamp `this.context.id` → helper, then call the real HA service  
- **Automations**  
  - Device→Proxy (with `not_by this` guards)  
  - Proxy→Devices (idempotent fan-out)  
  - Rules with waits, disables, and tags

---

## ✅ Examples

### Motion + Lux + Override
```hassl
alias light  = light.living
alias motion = binary_sensor.hall_motion
alias lux    = sensor.living_luminance

rule motion_on_light:
  if (light == off && motion && lux < 50)
  then light = on for 10m

rule switch_keep_on:
  if (light == on not_by any_hassl)
  then wait (!motion for 1h) light = off

rule switch_off_disable_motion:
  if (light transitions off not_by any_hassl)
  then disable rule motion_on_light for 3m
```

### Sync group
```hassl
sync shared [light.living, switch.living_circuit] as living_sync
```

---

## 🚀 Roadmap

- [ ] MVP compiler to Home Assistant YAML packages  
- [ ] Extended property coverage (media players, covers, fans)  
- [ ] Optional backends: pyscript, AppDaemon, Node-RED  
- [ ] CLI tool `hasslc file.hassl -o packages/`

---

