HASSL v0.5.0

Highlights
- Timed rules can run actions at clock times, sunrise or sunset, and signed sun offsets.
- Rules can react directly to the start and stop of rich named schedules.
- Home Assistant button and event entities can be used as event-driven conditions.
- Schedule-aware rules can require an explicit first activation before normal rule actions run.
- The Emacs HASSL mode now covers the expanded v0.5 syntax.

Timed rules
- Added `at <time> then <actions>` rule clauses.
- Clock values compile to native Home Assistant time triggers:

  ```hassl
  at 23:15 then light = off
  ```

- Sunrise and sunset values, including signed offsets, compile to native sun triggers:

  ```hassl
  at sunset-30m then light = on
  at sunrise+20m then light = off
  ```

- Typed clock and sun values remain intact when passed through rule templates.
- Existing `schedule use` clauses continue to gate ordinary clock and sun triggers.

Schedule transitions
- Added transition triggers for the effective state of named schedules:

  ```hassl
  alias porch = light.porch

  rule porch_lighting:
    schedule use porch_hours;
    at schedule start then porch = on
    at schedule stop then porch = off
  ```

- `at schedule start` runs when the combined schedule changes from inactive to active.
- `at schedule stop` runs when the combined schedule changes from active to inactive.
- Multiple schedules in one `schedule use` clause retain AND semantics.
- Overlapping windows produce one start and one final stop instead of intermediate false transitions.
- Home Assistant startup reconciles the clause matching the current schedule state.
- Re-enabling a rule also reconciles the current schedule state.
- Schedule transition clauses require at least one named `schedule use` clause.

Button and event entities
- Bare `button.*` and `input_button.*` operands now match real button state transitions.
- Bare `event.*` operands match each emitted Home Assistant event-entity event.
- Event entities can be compared with a raw event type:

  ```hassl
  if (event.wall_switch == short_release) then light = on
  ```

- Added friendly event gesture syntax:

  ```hassl
  if (event.wall_switch is clicked) then light = on
  ```

- Friendly gestures cover pressed, clicked, held, hold released, ongoing multi-press, and completed multi-press events.
- Unknown gesture names remain usable as vendor-specific raw event types.
- Event conditions can be combined with persistent entity-state conditions.

First-activation rule arming
- Added `arm when` for rules that must observe an initial state change during an active schedule before normal actions are allowed:

  ```hassl
  rule hallway_motion:
    schedule use evening;
    arm when (light == on) not_by this;
    if (motion) then light = on
  ```

- Armed state is stored in a generated per-rule `input_boolean` latch.
- The latch resets when the effective schedule becomes inactive.
- `not_by this`, `not_by any_hassl`, and rule context tracking remain available for loop protection.
- `arm when` requires at least one named `schedule use` clause.

Code generation and reliability
- Schedule start and stop actions compile into normal gated rule automations.
- Rule-originated entity writes now stamp per-rule context consistently.
- Rich schedule window shutdown logic checks the combined schedule state before lowering its shared helper.
- Schedule gates continue to support legacy schedule sensors and structured-window input booleans.

Editor and documentation
- Updated `hassl-mode.el` to version 0.5.
- Added highlighting for timed clauses, event gestures, and contextual `schedule start` / `schedule stop` terms.
- Expanded indentation, comments, Imenu, clock, entity, template, holiday, and schedule syntax support.
- Updated the language specification and quickstart for the v0.5 toolchain.

Compatibility
- Package version is now 0.5.0.
- Python 3.11 or newer is required consistently across package metadata.
- Existing `if`, `wait`, named schedule, inline schedule, template, import, and sync syntax remains supported.

Validation
- The complete test suite passes with 32 tests.
- The source distribution and universal wheel build successfully.
- Both artifacts pass `twine check`.
- The wheel installs with all declared dependencies in a clean Python 3.11 environment.
- The installed `hasslc` console command loads successfully.
