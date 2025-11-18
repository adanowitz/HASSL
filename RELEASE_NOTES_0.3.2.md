HASSL v0.3.2

Fixes:
- Correct holiday handling for schedule windows (no more early motion lights on holidays).
- Parser stabilization for `except holidays <id>` and holiday-only windows.
- Codegen emits precise time + holiday gate conditions and minimizes extra triggers.

No breaking changes. Recommended update for anyone using schedule windows.
