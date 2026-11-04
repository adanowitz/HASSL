HASSL v0.6.0

Highlights
- Directory builds now emit namespaced package fragments into one requested output directory, making room-by-room Home Assistant package layouts predictable.
- Short imports such as `common.schedules.*` and `hall.aliases.*` resolve within the importing package's top-level namespace.
- Schedule windows handle clock, sunrise, sunset, offsets, overnight rollover, weekdays, weekends, and holidays more reliably.
- On/off synchronization ignores `unknown`, `unavailable`, and attribute-only state changes, preventing one unavailable light from turning off its peers.

Package builds and imports
- Added recursive directory compilation with flat, collision-safe generated filenames.
- Package names may be declared explicitly or derived from paths under `--module-root`.
- Imported dependencies are analyzed for symbol resolution without being emitted into every consumer package.
- Added `--include-dependencies` for builds that intentionally need dependency output.
- Added optional namespaced IR and export diagnostics through `--debug-ir`.
- Top-level namespace shorthand imports now autoload the correct module while fully qualified imports remain supported.

Schedules
- Unified legacy and structured-window schedule handling.
- Improved mixed clock/sun and overnight window evaluation.
- Added rollover-safe local-time calculations for sunrise and sunset offsets.
- Prevented duplicate canonical schedule sensors when legacy and structured definitions coexist.
- Schedule edge automations now receive independent condition structures.
- Schedule consumers retain references to the declaring package's helpers during isolated room builds.

Synchronization safety
- On/off sync triggers are now limited to explicit transitions to `on` or `off`.
- `unknown` and `unavailable` states no longer lower the shared on/off proxy.
- Scene-driven brightness and color attribute changes no longer retrigger on/off synchronization.
- This prevents unavailable members from shutting down the rest of a synchronized light group and avoids unnecessary bare `light.turn_on` calls during scene changes.

Rules and templates
- Improved typed time and schedule values passed through templates.
- Preserved package namespaces consistently across package and rule generation.
- Continued support for timed rules, schedule transitions, event gestures, and first-activation rule arming introduced in v0.5.0.

Documentation and validation
- Updated the README, quickstart, and language specification for the current package and schedule behavior.
- Added regression coverage for directory builds, dependency emission, namespace-relative imports, schedule rollover behavior, and unavailable on/off sync members.
- The complete test suite passes with 40 tests.

Compatibility
- Package version is now 0.6.0.
- Python 3.11 or newer remains required.
- Existing fully qualified imports and single-file compilation remain supported.
