# HASSL Language Specification (v1.4 – 2025 Edition)

This document describes the grammar, semantics, and runtime model for  
**HASSL** — the *Home Assistant Simple Scripting Language.*

---

## 📐 Grammar (EBNF)

```ebnf
program        = { statement } ;

statement      = alias_stmt | sync_stmt | rule_stmt | schedule_decl ;

# --- Aliases ---
alias_stmt     = "alias" ident "=" entity ;

# --- Syncs ---
sync_stmt      = "sync" sync_type "[" entity_list "]" "as" ident [ sync_opts ] ;
sync_type      = "onoff" | "dimmer" | "attribute" | "shared" | "all" ;
sync_opts      = "{" [ "invert" ":" entity_list ] "}" ;

entity_list    = entity { "," entity } ;

# --- Rules ---
rule_stmt      = "rule" ident ":" { rule_clause } ;
rule_clause    = if_clause | rule_schedule_use | rule_schedule_inline ;

if_clause      = "if" "(" expression [ qualifier ] ")" [ qualifier ] "then" actions ;

rule_schedule_use    = "schedule" "use" ident_list ";" ;
rule_schedule_inline = "schedule" schedule_clause+ ;

ident_list     = ident { "," ident } ;

# --- Schedules ---
schedule_decl  = "schedule" ident ":" schedule_clause+ ;
schedule_clause = schedule_op "from" time_spec [ schedule_end ] ";" ;
schedule_op    = "enable" | "disable" ;
schedule_end   = "to" time_spec | "until" time_spec ;

time_spec      = time_clock | time_sun | entity | ident ;
time_clock     = time_hhmm ;
time_hhmm      = /[0-2]?\d:[0-5]\d/ ;
time_sun       = ("sunrise" | "sunset") [ offset ] ;
offset         = /[+-]\d+(ms|s|m|h|d)/ ;

# --- Expressions ---
expression     = or_expr ;
or_expr        = and_expr { "||" and_expr } ;
and_expr       = unary_expr { "&&" unary_expr } ;
unary_expr     = "!" unary_expr
               | "(" expression ")"
               | comparison ;

comparison     = operand ( "==" | "!=" | "<" | ">" | "<=" | ">=" ) value
               | operand ;

operand        = entity | ident | state | number | string ;

# --- Actions ---
actions        = action { ";" action } ;
action         = assignment | attr_assign | wait_action | rule_ctrl | tag_action ;

assignment     = ident "=" state [ "for" duration ] ;
attr_assign    = entity "." ident "=" number
               | entity "." ident "=" ident
               | entity "." ident "=" string ;

wait_action    = "wait" "(" condition "for" duration ")" action ;
rule_ctrl      = ("disable" | "enable") "rule" ident ("for" duration | "until" time_spec) ;
tag_action     = "tag" ident "=" (string | number | ident) ;

# --- Atoms ---
entity         = ident "." ident { "." ident } ;
ident          = letter { letter | digit | "_" } ;
state          = "on" | "off" ;
duration       = number ( "ms" | "s" | "m" | "h" | "d" ) ;
```

---

## ⚙️ Semantics Overview

### 🧩 **Aliases**
```hassl
alias light  = light.wesley_lamp
alias motion = binary_sensor.wesley_motion_motion
alias lux    = sensor.wesley_motion_illuminance
```
- Compile-time substitution for entities.
- Simplifies long entity IDs.
- Aliases are expanded before parsing rules or syncs.

---

### 🔄 **Syncs**
```hassl
sync shared [light.desk, light.strip, light.lamp] as work_sync
sync all [light.kitchen, switch.kitchen_circuit] as kitchen_sync
sync dimmer [light.desk, light.strip] as office_sync { invert: light.strip }
```

#### Supported kinds
| Type        | Behavior |
|--------------|-----------|
| `onoff`      | Sync binary state only. |
| `dimmer`     | Sync on/off + brightness + optional color_temp. |
| `shared`     | Sync all attributes shared across *all* members. |
| `all`        | Sync attributes present in *at least two* members. |

#### Features
- **invert** option reverses on/off for specified members.
- **Context-aware** — uses HA’s `context.id` for loop prevention.
- **Automatically creates** matching helpers (`input_boolean`, `input_number`, `input_text`).

#### Upstream (device → proxy)
- Trigger on any device attribute change.
- Update proxy helper **only if change did not originate from HASSL**.
- Mode: `restart` (latest state wins).

#### Downstream (proxy → devices)
- Trigger on proxy helper change.
- For each member whose value differs, call a **writer script** to set the new value.
- Writer scripts stamp context IDs so the upstream guard can recognize their origin.

---

### 🧠 **Rules**
```hassl
rule motion_light:
  if (motion && lux < 50) then light = on;
  wait (!motion for 10m) light = off
```

#### Components
- **Conditions:** standard boolean logic (`&&`, `||`, `!`, comparisons)
- **Actions:** `assignment`, `attr_assign`, `wait`, `rule_ctrl`, `tag`
- **Qualifiers:**  
  - `not_by this` → ignore self-triggered writes  
  - `not_by rule("other")` → ignore another rule’s writes  
  - `not_by any_hassl` → ignore all HASSL writes  

#### Example with qualifier
```hassl
rule landing_manual_off:
  if (light == off) not_by any_hassl
  then disable rule motion_light for 3m
```

---

### ⏳ **Waits**
```hassl
wait (!motion for 10m) light = off
```
- Compiled to HA `wait_for_trigger` with duration.
- Interrupts if rule restarts before completion.
- Common for occupancy or timeout logic.

---

### 🔒 **Rule Control**
Each rule compiles to:
```yaml
input_boolean.hassl_gate_<rule_name>
```
Used to globally enable/disable rule activity.

Actions:
```hassl
disable rule motion_light for 3m
enable rule night_scene for 1h
```

---

### 🏷 **Tags**
```hassl
tag override = "manual"
```
- Stored as `input_text.hassl_tag_<name>`.
- Useful for tracking manual overrides or context.

---

### 🕒 **Schedules**
Schedules are first-class gates controlling automation availability.

#### Top-level schedule declarations
```hassl
schedule wake_hours:
  enable from 08:00 until 19:00;
```

Creates:
- `input_boolean.hassl_schedule_wake_hours`
- Automations that:
  - Turn it ON at 08:00
  - Turn it OFF at 19:00
  - Maintain state correctly on restart or mid-day install

#### Inline rule schedules
```hassl
rule motion_light:
  schedule enable from sunset to sunrise;
  if (motion && lux < 50) then light = on
```

Creates a per-rule schedule gate:
- `input_boolean.hassl_schedule_rule_motion_light`
- Rule triggers only when the gate is ON.

#### Reuse schedules
```hassl
rule wesley_motion_light:
  schedule use wake_hours;
  if (motion && lux < 50) then light = on;
  wait (!motion for 10m) light = off
```

Each referenced schedule acts as an extra `condition: state` gate in Home Assistant.

---

### 💡 **Attribute Assignments**
```hassl
light.brightness = 255
light.kelvin = 2700
```

#### Supported targets
- `brightness` → numeric 0–255  
- `color_temp` → mireds  
- `kelvin` → Kelvin, auto-converted to `color_temp` for compatibility  
- `color_temp_kelvin` → synonym for `kelvin`  

When `kelvin` is used, HASSL emits both:
```yaml
service: light.turn_on
data:
  kelvin: 2700
  color_temp: 370  # for backward compatibility
```

---

## ⚙️ **Runtime Guarantees**

| Guarantee | Description |
|------------|--------------|
| **Loop-safe** | Every automation stamps its context to prevent self-retriggering. |
| **Restart-safe** | Schedule booleans re-evaluate on HA start or every minute. |
| **Deterministic** | Evaluations occur only when dependencies change. |
| **Composable** | Rules, syncs, and schedules can coexist without cross-collision. |
| **Human-readable** | Generated YAML uses consistent names: `hassl_<scope>_<name>_<attr>` |

---

## ✅ **End-to-End Example**

```hassl
alias light  = light.wesley_lamp
alias motion = binary_sensor.wesley_motion_motion
alias lux    = sensor.wesley_motion_illuminance

schedule wake_hours:
  enable from 08:00 until 19:00;

rule wesley_motion_light:
  schedule use wake_hours;
  if (motion && lux < 50)
  then light = on;
  wait (!motion for 10m) light = off

rule landing_manual_off:
  if (light == off) not_by any_hassl
  then disable rule wesley_motion_light for 3m
```

Generates:
- ✅ Schedules that track real time and restart cleanly.  
- ✅ Context-aware automations for motion logic.  
- ✅ A gate toggle (`input_boolean.hassl_gate_wesley_motion_light`) to disable logic for manual use.

---

## 🚀 **Versioning & Backward Compatibility**

| Feature | Introduced | Notes |
|----------|-------------|-------|
| `sync all` / `shared` | v1.0 | Multi-device synchronization |
| `not_by` guards | v1.1 | Context-safe rule evaluation |
| `wait (...)` blocks | v1.2 | Continuous state waits |
| `schedule` blocks | v1.3 | Declarative time-of-day gating |
| `kelvin` support | v1.4 | Native + color_temp fallback |
| `restart maintenance` for schedules | v1.4 | Evaluates schedule booleans at startup |

