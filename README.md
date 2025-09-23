# HASSL Language Specification

This document describes the grammar, semantics, and usage examples of **HASSL** (Home Assistant Simple Scripting Language).

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
```

---

## 🔧 Semantics

### Aliases

```hassl
alias light = light.living
alias motion = binary_sensor.hall_motion
alias lux    = sensor.living_luminance
```

- Aliases are compile-time shorthands for entity IDs.
- After alias expansion, all rules and syncs work on full entity IDs.

---

### Sync

```hassl
sync onoff [light.kitchen, switch.floor] as circuit
sync all_shared [light.kitchen, switch.floor] as shared_sync
sync all [light.desk, light.strip, switch.floor] as mixed_sync { invert: switch.floor }
```

#### Properties synchronized
- **onoff** → binary state only  
- **dimmer** → on/off + brightness (and color temp if supported by both)  
- **all_shared** → properties supported by *all* entities in the group  
- **all** → properties supported by *at least two* entities in the group  
- **invert** (optional) → reverses `on ↔ off` for listed entities (onoff only)

#### Execution model
For each synchronized property:
1. **Devices → Proxy**
   - Trigger when a device’s property changes.
   - Guarded with **implied `not_by this`**: ignores changes caused by the sync itself.
   - Updates the proxy helper (`input_boolean` or `input_number`).

2. **Proxy → Devices**
   - Trigger when the proxy changes.
   - For each member whose value differs, call a **writer script** to set the new value.
   - Writer scripts stamp context IDs so the upstream guard can recognize their origin.

#### Guarantees
- **Loop-safe:** No feedback or infinite toggles (`not_by this` is implied).
- **Idempotent:** Devices are only written if their state differs from proxy.
- **Last-write-wins:** Upstream automations use `mode: restart` so the newest event dominates.

---

### Rules

```hassl
rule motion_on_light:
  if (light == off && motion && lux < 50)
  then light = on for 10m
```

#### Conditions
- Evaluated when any referenced entity changes.
- Boolean operators: `&&`, `||`, `!`
- Comparisons: `==`, `!=`, `<`, `>`, `<=`, `>=`

#### Qualifiers
- `not_by this` → event not caused by this rule’s last write  
- `not_by rule("name")` → event not caused by the named rule  
- `not_by any_hassl` → event not caused by any HASSL-generated write  

#### Actions
- **Assignment**:  
  `light = on for 10m`  
  Turns entity on; auto-reverts after 10 minutes (cancelled if rule restarts).
- **Wait**:  
  `wait (!motion for 1h) light = off`  
  Suspends until condition holds continuously for the duration, then executes.
- **Rule control**:  
  `disable rule motion_on_light for 3m`  
  Toggles rule enable flags via helpers.
- **Tags**:  
  `tag override = "manual"`  
  Stores metadata in a helper.

---

### Waits

```hassl
rule switch_keep_on:
  if (light == on not_by any_hassl)
  then wait (!motion for 1h) light = off
```

- Compiled to HA `wait_for_trigger` blocks.
- Enforces a continuous period of the condition holding true.

---

### Rule Control

```hassl
rule switch_off_disable_motion:
  if (light transitions off not_by this)
  then disable rule motion_on_light for 3m
```

- Each rule has an enable flag (`input_boolean.hassl_rule__<name>__enabled`).
- Conditions include a check for the flag.
- `disable`/`enable` rules toggle these flags for the given duration.

---

### Tags

```hassl
if (light == on not_by any_hassl) then tag override = "manual"
```

- Tags are stored in helpers (`input_text.hassl_tag__<name>`).
- Can be read by other automations or used for debugging.

---

## ⚙️ Execution Guarantees

- **Loop-safety:** All sync flows ignore their own writes (via context IDs).  
- **Race resistance:** Last external change wins; fan-out is idempotent.  
- **Determinism:** Conditions and waits are re-evaluated only when relevant entities change.  
- **Isolation:** Rules can be temporarily disabled/enabled, without affecting others.

---

## ✅ Worked Examples

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

### Sync a light with its switch (same circuit)

```hassl
sync all_shared [light.living, switch.living_circuit] as living_sync
```

### Mixed devices with inversion

```hassl
sync all [light.desk, light.strip, light.lamp] as work_sync { invert: light.lamp }
```

This keeps brightness and color in sync across the lights, while `light.lamp` has inverted on/off behavior.
