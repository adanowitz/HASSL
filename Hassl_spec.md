# HASSL Language Specification (v1.4 – 2025 Edition)
_Updated for toolchain release **v0.3.0** (packages/imports, “private” visibility, schedule sensors, and semicolon rules)._

This document describes the grammar, semantics, and runtime model for  
**HASSL** — the *Home Assistant Simple Scripting Language.*

---

## 📦 Modules & Visibility (NEW in v0.3.0)

HASSL sources live in **packages** and can **import** other packages.

```hassl
package home.landing
import std.shared.*        # import all public exports
import std.lights.aliases  # or import a specific unit (future granular forms)
```

### Exports
- **Public** by default.
- Mark with `private` to keep within the declaring package.

```hassl
package std.shared

alias landing_light = light.landing_main
private alias _debug = light.dev_fixture

schedule wake_hours:
  enable from 07:00 to 23:00;
```

**Import semantics**:
- Importers see only public exports.
- Aliases are injected into the importer’s symbol table (compile-time).
- Schedules are referenced by name via `schedule use <name>;` and are resolved to schedule **sensors** created by the declaring package (details below).

---

## 📐 Grammar (EBNF)

> Notes for v0.3.0:
> - Added `package`, `import`, and `private`.
> - Semicolons (`;`) are **required only** inside `then` action lists and in `schedule` clause lists.
> - Top-level statements do **not** require trailing semicolons.

```ebnf
program        = package_decl? { import_decl | statement } ;

package_decl   = "package" package_name ;
import_decl    = "import" import_spec ";"? ;
package_name   = ident {"." ident} ;
import_spec    = package_name ".*"           # public exports of a package
               | package_name                # (reserved for future selective)

statement      = alias_stmt | sync_stmt | rule_stmt | schedule_decl ;

# --- Aliases ---
alias_stmt     = ["private"] "alias" ident "=" entity ;

# --- Syncs ---
sync_stmt      = "sync" sync_type "[" entity_list "]" "as" ident [ sync_opts ] ;
sync_type      = "onoff" | "dimmer" | "attribute" | "shared" | "all" ;
sync_opts      = "{" [ "invert" ":" entity_list ] "}" ;

entity_list    = entity { "," entity } ;

# --- Rules ---
rule_stmt      = "rule" ident ":" { rule_item } ;
rule_item      = if_clause | rule_schedule_use | rule_schedule_inline ;

if_clause      = "if" "(" expression [ qualifier ] ")" [ qualifier ]
                 "then" actions ;                       # actions require ';' separators

rule_schedule_use    = "schedule" "use" ident_list ";" ;
rule_schedule_inline = "schedule" schedule_clause+ ;    # clauses end with ';'

ident_list     = ident { "," ident } ;

# --- Schedules (top-level declaration) ---
schedule_decl  = "schedule" ident ":" schedule_clause+ ;  # clauses end with ';'
schedule_clause = schedule_op ["from" time_spec] [ schedule_end ] ";" ;
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

# --- Qualifiers (loop/feedback guards) ---
qualifier      = "not_by" ("this" | "any_hassl" | rule_ref) ;
rule_ref       = "rule" "(" string | ident ")" ;

# --- Actions ---
actions        = action { ";" action } ;                # semicolons required here
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

### Semicolon Rules (v0.3.0)
- **Required** between actions in `then` blocks and between `schedule` clauses.
- **Optional/unused** elsewhere (e.g., after `import` is allowed, but not required).

---

## ⚙️ Semantics Overview

### 🧩 Aliases (public & private)
```hassl
alias light  = light.wesley_lamp
private alias _debug = light.test_fixture
```
- **Public** (default) aliases are importable with `import pkg.*`.
- **Private** aliases remain within the defining package.
- Codegen resolves aliases **in expressions and actions** before emitting YAML targets.

### 🔄 Syncs
```hassl
sync shared [light.desk, light.strip, light.lamp] as work_sync
sync all [light.kitchen, switch.kitchen_circuit] as kitchen_sync
sync dimmer [light.desk, light.strip] as office_sync { invert: light.strip }
```
- Emit helpers (`input_boolean/input_number/input_text`) as proxies.
- **Upstream** changes (device → proxy) only write when **not** originated by HASSL (context guard).
- **Downstream** changes (proxy → device) use stamped writer scripts to prevent loops.
- Supports `brightness`, `color_temp`, `kelvin`, `hs_color`, `percentage`, `preset_mode`, `volume`, `mute`.
- `kelvin` emits **dual data** (`kelvin` + computed `color_temp`) for compatibility.

### 🧠 Rules
```hassl
rule motion_light:
  schedule use wake_hours;
  if (motion && lux < 20) then light = on;
  wait (!motion for 10m) light = off
```
- Boolean expressions support `&&`, `||`, `!`, comparisons, and aliases.
- **Qualifiers** (loop protection): `not_by this`, `not_by any_hassl`, `not_by rule("other")`.
- Each rule has a gate: `input_boolean.hassl_gate_<rule_name>` (default **on**).

### ⏳ Waits
```hassl
wait (!motion for 10m) light = off
```
- Compiles to `wait_for_trigger` with a `template` trigger and `for` duration.
- Rule restarts cancel outstanding waits (`mode: restart`).

### 🔒 Rule Control
```hassl
disable rule motion_light for 3m
enable rule night_scene until sunrise+15m
```

### 🕒 Schedules (v0.3.0 tooling behavior)
#### Top-level Declarations
```hassl
schedule wake_hours:
  enable from 07:00 to 23:00;
```
**package.py** emits a **template binary_sensor** per named schedule:
```
binary_sensor.hassl_schedule_<package>_<name>_active
```
- `state:` is a safe Jinja expression (no `{% %}` inside `{{ }}`) using:
  - clock windows with wrap (e.g., `22:00..06:00`),
  - sun windows with offsets (e.g., `sunrise+15m`),
  - OR-of-ENABLE minus OR-of-DISABLE clauses.
- Rules that `schedule use <name>;` add a `condition: state` on that sensor.
- Importing packages **reuses** the declaring package’s sensor name; rules_min resolves the correct sensor by the schedule’s **base name** and the **declaring package**.

#### Inline Rule Schedules
```hassl
rule porch:
  schedule
    enable from sunset until 23:00;
  if (motion) then light = on
```
- No helpers created; rules_min compiles inline schedule clauses into HA `condition:` blocks (sun/clock/templated window checks).

---

## 💡 Attribute Assignments
```hassl
light.brightness = 255
light.kelvin = 2700        # also emits color_temp fallback
```
- `brightness` uses `light.turn_on` with `brightness` data.
- `kelvin` uses `light.turn_on` with `kelvin` and a computed `color_temp` fallback.
- Other attributes default to `homeassistant.turn_on` with data.

---

## 🧯 Runtime Guarantees
| Guarantee | Description |
|------------|-------------|
| **Loop-safe** | Every write stamps `context.id`; upstream guards ignore our own writes. |
| **Restart-safe** | Schedule sensors re-evaluate continuously (templates use time/sun/state). |
| **Deterministic** | Triggers come only from referenced entities; `mode: restart` ensures latest state wins. |
| **Composable** | Rules, syncs, schedules, and imports can be combined safely. |
| **Readable** | Emitted YAML names are predictable: `hassl_<scope>_<name>_<attr>` and schedule sensors as above. |

---

## ✅ End-to-End Example

```hassl
package home.landing
import std.shared.*

alias motion = binary_sensor.landing_motion
alias lux    = sensor.landing_lux
alias light  = light.landing_main

schedule wake_hours:
  enable from 08:00 until 19:00;

rule motion_light:
  schedule use wake_hours;
  if (motion && lux < 50)
  then light = on;
  wait (!motion for 10m) light = off
```

Generates:
- `binary_sensor.hassl_schedule_home.landing_wake_hours_active` (sensor id normalized).
- A rule automation gated by the schedule sensor and the rule gate boolean.
- Context-stamped writes and safe waits.

---

## 🧭 Versioning

| Feature | Introduced | Notes |
|--------|------------|-------|
| Modules (`package`/`import`) | v0.3.0 | Public/private exports; alias & schedule import behavior |
| Schedule **sensors** in codegen | v0.3.0 | Emitted by `package.py` as template binary_sensors |
| Inline schedule → conditions | v0.3.0 | No helpers; compiled to `condition:` blocks |
| Kelvin fallback | v1.4 | Emits `kelvin` + `color_temp` |
| `wait (...)` | v1.2 | Template wait triggers |
| `not_by` guards | v1.1 | Loop prevention |

---

## ℹ️ Notes & Limitations
- Semicolons are only significant in **action lists** and **schedule clause** lists.
- Schedule sensor IDs include the **declaring package** slug and the **base schedule name**; consumers should not hardcode the declaring package—use `rules_min` to resolve imported usage.
- Future releases may add grouped attribute assignments and enhanced error reporting.
