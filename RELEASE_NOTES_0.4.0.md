HASSL v0.4.0

Highlights
- Templates: `template` + `use template` for rules, syncs, and schedules, with defaults and named args.
- Schedule windows: `on weekdays 08:00-19:00;` with holiday modifiers, plus window gate helpers.
- Import aliasing: `import pkg as ns` and list imports with renames (`import pkg: a, b as c`).
- Schedule gating: rules gate on either legacy schedule binary_sensors or new window input_booleans.
- Semicolon relaxations: `schedule use <name>` allows an optional trailing semicolon.

Notes
- Existing schedule declarations and inline schedules continue to work unchanged.
- Window schedules emit `input_boolean.hassl_sched_<package>_<name>` plus per-window automations.
