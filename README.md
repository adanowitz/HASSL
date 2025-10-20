# HASSL

> **Home Assistant Simple Scripting Language**

![Version](https://img.shields.io/badge/version-v0.3.0-blue)

HASSL is a human-friendly domain-specific language (DSL) for building **loop-safe**, **deterministic**, and **composable** automations for [Home Assistant](https://www.home-assistant.io/).

It compiles lightweight `.hassl` scripts into fully functional YAML packages that plug directly into Home Assistant, replacing complex automations with a clean, readable syntax.

---

## 🚀 Features

- **Readable DSL** → write logic like natural language (`if motion && lux < 50 then light = on`)
- **Sync devices** → keep switches, dimmers, and fans perfectly in sync
- **Schedules** → declare time-based gates (`enable from 08:00 until 19:00`)
- **Loop-safe** → context ID tracking prevents feedback loops
- **Per-rule enable gates** → `disable rule` or `enable rule` dynamically
- **Inline waits** → `wait (!motion for 10m)` works like native HA triggers
- **Color temperature in Kelvin** → `light.kelvin = 2700`
- **Modular packages/imports** → split automations across files with public/private exports (v0.3.0)
- **Auto-reload resilience** → schedules re-evaluate automatically on HA restart

---

## 🧰 Example

### Basic standalone script

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

Produces a complete Home Assistant package with:

- Helpers (`input_boolean`, `input_text`, `input_number`)
- Context-aware writer scripts
- Sync automations for linked devices
- Rule-based automations with schedules and `not_by` guards

### Using imports across packages

```hassl
# packages/std/shared.hassl
package std.shared

alias light  = light.wesley_lamp
alias motion = binary_sensor.wesley_motion_motion
alias lux    = sensor.wesley_motion_illuminance

schedule wake_hours:
  enable from 08:00 until 19:00;
```

```hassl
# packages/home/landing.hassl
package home.landing
import std.shared.*

rule wesley_motion_light:
  schedule use wake_hours;
  if (motion && lux < 50)
  then light = on;
  wait (!motion for 10m) light = off

rule landing_manual_off:
  if (light == off) not_by any_hassl
  then disable rule wesley_motion_light for 3m
```

This setup produces:
- One **shared package** defining reusable aliases and schedules  
- A **landing package** importing and reusing those exports  

Together, they generate:
- ✅ Shared schedule sensor (`binary_sensor.hassl_schedule_std_shared_wake_hours_active`)  
- ✅ Cross-package rule automations gated by that schedule  
- ✅ Context-safe helpers and syncs for both packages  

---

## 🏗 Installation

```bash
git clone https://github.com/adanowitz/hassl.git
cd hassl
pip install -e .
```

Verify:

```bash
hasslc --help
```

---

## ⚙️ Usage

1. Create a `.hassl` script (e.g., `living_room.hassl`).
2. Compile it into a Home Assistant package:
   ```bash
   hasslc living_room.hassl -o ./packages/living_room/
   ```
3. Copy the package into `/config/packages/` and reload automations.

Each `.hassl` file compiles into an isolated package — no naming collisions, no shared helpers.

---

## 📦 Output Files

| File                       | Description                                   |
| -------------------------- | --------------------------------------------- |
| `helpers_<pkg>.yaml`       | Defines all helpers (booleans, text, numbers) |
| `scripts_<pkg>.yaml`       | Writer scripts with context stamping          |
| `sync_<pkg>_*.yaml`        | Sync automations for each property            |
| `rules_bundled_<pkg>.yaml` | Rule logic automations + schedules            |
| `schedules_<pkg>.yaml`     | Time/sun-based schedule sensors (v0.3.0)      |

---

## 🧠 Concepts

| Concept      | Description                                                     |
| ------------ | --------------------------------------------------------------- |
| **Alias**    | Maps short names to HA entities (`alias light = light.kitchen`) |
| **Sync**     | Keeps multiple devices aligned across on/off, brightness, etc.  |
| **Rule**     | Defines reactive logic with guards, waits, and control flow.    |
| **Schedule** | Defines active time windows, reusable across rules.             |
| **Tag**      | Lightweight metadata stored in `input_text` helpers.            |

---

## 🔒 Loop Safety & Context Tracking

HASSL automatically writes the **parent context ID** into helper entities before performing actions.  
This ensures `not_by any_hassl` and `not_by rule("name")` guards work flawlessly, preventing infinite feedback.

---

## 🕒 Schedules That Survive Restarts

All schedules are restart-safe:

- `binary_sensor.hassl_schedule_<package>_<name>_active` automatically re-evaluates on startup.
- Clock and sun-based windows update continuously through HA’s template engine.
- Missed events (like mid-day restarts) are recovered automatically.

---

## 📚 Documentation

- [Quickstart Guide](./quickstart_v1.4_2025_v0.3.0.md)
- [Language Specification](./hassl_language_spec_v1.4_2025_updated_v0.3.0.md)

---

## 🧩 Contributing

Contributions, tests, and ideas welcome!  
To run tests locally:

```bash
pytest tests/
```

Please open pull requests for grammar improvements, new device domains, or scheduling logic.

---

## 📄 License

MIT License © 2025  
Created and maintained by [@adanowitz](https://github.com/adanowitz)

---

**HASSL** — simple, reliable, human-readable automations for Home Assistant.
