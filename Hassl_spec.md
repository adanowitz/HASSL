# HASSL Language Specification (v1.4 – 2025 Edition)
_Updated for toolchain **v0.5.0** (timed rules; schedule transitions; event gestures; rule arming)._
  
This document describes the grammar, semantics, and runtime model for  
**HASSL** — the *Home Assistant Simple Scripting Language.*

---

## 📦 Modules & Visibility (v0.4.0)

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

> Notes for v0.4.0:
> - Added `template` / `use template`.
> - Added schedule window syntax (`on weekdays 08:00-19:00`), plus holiday modifiers.
> - `import ... as <ns>` and `import pkg: a, b as c` are supported.
> - Semicolons (`;`) are **required only** inside `then` action lists and in `schedule` clause lists. `schedule use` accepts an optional semicolon.
> - Top-level statements do **not** require trailing semicolons.

```ebnf
program        = package_decl? { import_decl | statement } ;

package_decl   = "package" package_name ;
import_decl    = "import" import_spec ";"? ;
package_name   = ident {"." ident} ;
import_spec    = package_name ".*"           # public exports of a package
               | package_name ":" import_list
               | package_name "as" ident
               | package_name                # (reserved for future selective)

statement      = alias_stmt | sync_stmt | rule_stmt | schedule_decl | holidays_decl
               | template_decl | use_template_stmt ;

# --- Aliases ---
alias_stmt     = ["private"] "alias" ident "=" entity ;

# --- Syncs ---
sync_stmt      = "sync" sync_type "[" entity_list "]" "as" ident [ sync_opts ] ;
sync_type      = "onoff" | "dimmer" | "attribute" | "shared" | "all" ;
sync_opts      = "{" [ "invert" ":" entity_list ] "}" ;

entity_list    = entity { "," entity } ;

# --- Rules ---
rule_stmt      = "rule" ident ":" { rule_item } ;
rule_item      = if_clause | at_clause | rule_schedule_use | rule_schedule_inline | arm_clause ;

if_clause      = "if" "(" expression [ qualifier ] ")" [ qualifier ]
                 "then" actions ;                       # actions require ';' separators

at_clause      = "at" ( time_spec | schedule_transition ) "then" actions ;
schedule_transition = "schedule" ( "start" | "stop" ) ;

arm_clause     = "arm" "when" "(" expression [ qualifier ] ")"
                 [ qualifier ] [ ";" ] ;

rule_schedule_use    = "schedule" "use" ident_list [ ";" ] ;
rule_schedule_inline = "schedule" schedule_clause+ ;    # clauses end with ';'

ident_list     = ident { "," ident } ;

# --- Schedules (top-level declaration) ---
schedule_decl  = "schedule" ident ":" schedule_clause+ ;  # clauses end with ';'
schedule_clause = schedule_legacy_clause | schedule_window_clause | schedule_holiday_only ;
schedule_legacy_clause = schedule_op ["from" time_spec] [ schedule_end ] ";" ;
schedule_window_clause = [period] "on" day_selector time_range [holiday_mod] ";" ;
schedule_holiday_only  = "on" "holidays" ident time_range ";" ;
schedule_op    = "enable" | "disable" ;
schedule_end   = "to" time_spec | "until" time_spec ;
day_selector   = "weekdays" | "weekends" | "daily" ;
time_range     = time_hhmm "-" time_hhmm ;
holiday_mod    = "except" "holidays" ident ;

period         = "during" "months" month_range
               | "during" "dates"  mmdd_range
               | "during" "range"  ymd_range ;
month_range    = MONTH [".." MONTH] { "," MONTH } ;
mmdd_range     = MMDD ".." MMDD ;
ymd_range      = YMD ".." YMD ;

time_spec      = time_clock | time_sun | entity | ident ;
time_clock     = time_hhmm ;
time_hhmm      = /[0-2]?\\d:[0-5]\\d/ ;
time_sun       = ("sunrise" | "sunset") [ offset ] ;
offset         = /[+-]\\d+(ms|s|m|h|d)/ ;

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

# --- Templates ---
template_decl  = ["private"] "template" template_kind ident "(" [template_params] ")" ":" template_body ;
template_kind  = "rule" | "sync" | "schedule" ;
template_params = template_param { "," template_param } ;
template_param = ident [ "=" template_default ] ;
template_default = number | string | ident ;
template_body  = rule_body | sync_body | schedule_body ;
use_template_stmt = "use" "template" ident "(" [call_args] ")" ["as" ident] ;
call_args      = call_arg { "," call_arg } ;
call_arg       = ident "=" value | value ;

# --- Atoms ---
entity         = ident "." ident { "." ident } ;
ident          = letter { letter | digit | "_" } ;
state          = "on" | "off" ;
duration       = number ( "ms" | "s" | "m" | "h" | "d" ) ;
```

### Semicolon Rules (v0.4.0)
- **Required** between actions in `then` blocks and between `schedule` clauses.
- **Optional** after `schedule use <name>` and `import`.
- **Unused** elsewhere (top-level statements do not require trailing semicolons).

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
- A bare `button.*` or `input_button.*` operand matches a press event (a real
  state transition), so it can be combined with persistent state conditions:
  `if (button.movie_scene && binary_sensor.room_occupied) then ...`.
- A bare `event.*` operand matches each event emitted by that entity. Compare it
  to an event type to select a specific interaction. Event types may be written
  as identifiers or strings:
  `if (event.wesley_switch_button_7 == short_release) then ...`.
  For devices that emit both `initial_press` and `short_release`, filter for
  `short_release` to run an action once per completed button press.
- Friendly event gestures use `is <keyword>` and normalize legacy integration
  event names with Home Assistant's standard names:

  | HASSL gesture | Matching event types |
  | --- | --- |
  | `is pressed` | `initial_press`, `press_start` |
  | `is clicked` | `short_release`, `press_end` |
  | `is held` | `long_press`, `long_press_start` |
  | `is hold_released` | `long_release`, `long_press_end` |
  | `is multi_pressing` | `multi_press_ongoing` |
  | `is multi_pressed` | `multi_press_complete`, `multi_press_end` |

  Example: `if (wesley_button is clicked) then light = on`.
  Unknown keywords are treated as raw integration event types, so vendor-specific
  events remain usable without a HASSL update.
- **Qualifiers** (loop protection): `not_by this`, `not_by any_hassl`, `not_by rule("other")`.
- Each rule has a gate: `input_boolean.hassl_gate_<rule_name>` (default **on**).

### First-Activation Arming (NEW)
```hassl
rule hallway_motion:
  schedule use evening_hours;
  arm when (light == on) not_by this;
  if (motion) then light = on
```
- An armed rule starts blocked and uses `input_boolean.hassl_armed_<rule_name>` as a latch.
- The latch turns on when the `arm when` condition becomes true while the named schedule is active.
- The latch turns off when the effective named schedule becomes inactive, so the next schedule period starts blocked.
- `not_by this` allows another HASSL rule or an external action to arm the rule.
- `not_by any_hassl` requires a manual or non-HASSL action to arm the rule.
- `arm when` requires at least one named `schedule use` clause.

### 🧩 Templates (NEW in v0.4.0)
```hassl
template rule motion_light(name, motion, lux, light, sched=anytime):
  schedule use sched
  if (motion && lux < 50) then light = on

use template motion_light(
  name="kitchen_motion",
  motion=motion.kitchen,
  lux=sensor.kitchen_lux,
  light=light.kitchen_main
)
```
- Templates can target **rule**, **sync**, or **schedule** bodies.
- `use template` expands to a concrete rule/sync/schedule at compile time.
- The resulting rule name is taken from `as <name>` or the `name=` argument if provided.
- Parameters support defaults and named/positional arguments.

### Timed rules

An `at` clause runs its actions from a native Home Assistant clock or sun
trigger. It accepts `HH:MM`, `sunrise`, `sunset`, and signed offsets:

```hassl
template rule timed_light(name, light, turn_on, turn_off, sched=anytime):
  schedule use sched
  at turn_on then light = on
  at turn_off then light = off
```

Clock and sun values remain typed when passed into a template:

```hassl
use template timed_light(
  name=porch_evening,
  light=light.porch,
  turn_on=sunset-30m,
  turn_off=23:15,
  sched=anytime
)
```

Other valid values include `sunrise`, `sunrise+20m`, and `sunset+1h`. The named
schedule is evaluated as a gate when each trigger fires. `anytime` is not a
built-in schedule; it must be declared locally or imported like any other named
schedule.

A rule can also run actions when the combined state of its named schedules
changes:

```hassl
rule porch_lighting:
  schedule use porch_hours
  at schedule start then light.porch = on
  at schedule stop then light.porch = off
```

`at schedule start` runs when the effective schedule changes from inactive to
active; `at schedule stop` runs for the reverse transition. Multiple schedules
in `schedule use` are combined with AND. Overlapping windows therefore produce
one start and one final stop. Home Assistant startup also runs the clause that
matches the current effective state so device state is reconciled after a
restart. Re-enabling the rule performs the same reconciliation. Schedule
transitions require at least one named `schedule use` clause.

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

---

## 🕒 Schedules (v0.4.0 tooling behavior)

### Top-level Declarations
```hassl
schedule wake_hours:
  enable from 07:00 to 23:00;
```
**Codegen** emits a **template binary_sensor** per named schedule:
```
binary_sensor.hassl_schedule_<package>_<name>_active
```
- `state:` is a safe Jinja expression (no `{% %}` inside `{{ }}`) using:
  - clock windows with wrap (e.g., `22:00..06:00`),
  - sun windows with offsets (e.g., `sunrise+15m`),
  - OR-of-ENABLE minus OR-of-DISABLE clauses.
- Rules that `schedule use <name>` add a gate condition for that sensor.
- Importing packages **reuses** the declaring package’s sensor name; resolution is based on the schedule’s **base name** and the **declaring package**.

### Window Schedules (NEW in v0.4.0)
```hassl
schedule wake:
  on weekdays 08:00-19:00;
  on weekends 09:00-22:00 except holidays us;
  on holidays us 10:00-21:00;
```
**Codegen** emits an **input_boolean** gate per schedule window set:
```
input_boolean.hassl_sched_<package>_<name>
```
Rules gate on **either** the window boolean or the legacy binary_sensor (OR’d), so older schedule forms and new window forms interoperate.

### Inline Rule Schedules
```hassl
rule porch:
  schedule
    enable from sunset until 23:00;
  if (motion) then light = on
```
- No helpers created; inline schedule clauses compile directly into HA `condition:` blocks (sun/clock/templated window checks).

---

## 🗓️ Holidays, Weekdays & Weekends (clarified)

HASSL supports distinguishing **weekdays**, **weekends**, and **holidays**, with holidays remaining holidays **even if they fall on weekends**.

### What the compiler expects
HASSL relies on the **Home Assistant Workday integration** configured via the UI.

Create **two** Workday-based binary_sensors in *Settings → Devices & Services → Workday → Add*:

1) **All-days except holidays** (used to compute “holiday”)  
   - Name (Entity ID): `binary_sensor.hassl_<id>_not_holiday`  
   - Workdays: **Mon–Sun**  
   - Excludes: **holiday**  
   - Country/Province: set to **your locale** (not required to be US/CA)

2) **Weekdays** (optional, helps rule/schedule authoring for Mon–Fri)  
   - Name (Entity ID): `binary_sensor.hassl_<id>_workday`  
   - Workdays: **Mon–Fri**  
   - Excludes: **holiday**  
   - Country/Province: your locale

> Replace `<id>` with the holiday set you reference in your HASSL files, e.g., `us_ca` → `binary_sensor.hassl_us_ca_not_holiday`.

The compiler generates a template sensor:
```
binary_sensor.hassl_holiday_<id> = (binary_sensor.hassl_<id>_not_holiday == 'off')
```
This means **official holidays are “on”**, even when they land on a Saturday/Sunday.

### Using in schedules & rules
- **Weekdays**: guard with `condition: time -> weekday: mon..fri` (or the optional `*_workday` sensor if you prefer).  
- **Weekends**: guard with `weekday: sat,sun`.  
- **Holidays**: guard with `binary_sensor.hassl_holiday_<id> == 'on'`.  
- **Not holidays**: guard with `binary_sensor.hassl_holiday_<id> == 'off'`.

> Design intent: **weekend schedules do not automatically include holidays**. You can exclude or include holidays explicitly using the holiday sensor to avoid ambiguity.

---

## 💡 Attribute Assignments
```hassl
light.brightness = 255
light.kelvin = 2700        # also emits color_temp fallback
```
- `brightness` uses `light.turn_on` with `brightness` data.
- `kelvin` uses `light.turn_on` with `kelvin` and a computed `color_temp` fallback.
- Other attributes default to domain-appropriate services or `homeassistant.turn_on` with data.

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
| Templates (`template` / `use template`) | v0.4.0 | Compile-time rule/sync/schedule expansion |
| Schedule **sensors** in codegen | v0.3.0 | Template `binary_sensor.hassl_schedule_*_active` |
| Window schedules (`on weekdays ...`) | v0.4.0 | `input_boolean.hassl_sched_*` gate + per-window automations |
| Holiday/Workday wiring | v0.3.1 | Requires UI Workday sensors; `hassl_holiday_<id>` derived |
| Inline schedule → conditions | v0.3.0 | No helpers; compiled to `condition:` blocks |
| Kelvin fallback | v0.2 | Emits `kelvin` + `color_temp` |
| `wait (...)` | v0.2 | Template wait triggers |
| `not_by` guards | v0.2 | Loop prevention |

---

## ℹ️ Notes & Limitations
- Semicolons are only significant in **action lists** and **schedule clause** lists.
- Schedule sensor IDs include the **declaring package** slug and the **base schedule name**; consumers should not hardcode the declaring package—use `rules_min` to resolve imported usage.
- Workday integration must be added via **UI** (not YAML). Give entities the exact names shown above.
- Future releases may add grouped attribute assignments and enhanced error reporting.
