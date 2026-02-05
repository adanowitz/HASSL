# HASSL

> **Home Assistant Simple Scripting Language**

![Version](https://img.shields.io/badge/version-v0.4.1-blue)

HASSL is a human-friendly domain-specific language (DSL) for building **loop-safe**, **deterministic**, and **composable** automations for [Home Assistant](https://www.home-assistant.io/).

It compiles lightweight `.hassl` scripts into fully functional YAML packages that plug directly into Home Assistant, replacing complex automations with a clean, readable syntax.

---

## 🚀 Features

- **Readable DSL** → write logic like natural language (`if motion && lux < 50 then light = on`)
- **Sync devices** → keep switches, dimmers, and fans perfectly in sync
- **Schedules** → declare time-based gates (`enable from 08:00 until 19:00`)
- **Weekday/weekend/holiday schedules** → full support for Home Assistant’s **Workday integration** (v0.4.0)
- **Loop-safe** → context ID tracking prevents feedback loops
- **Per-rule enable gates** → `disable rule` or `enable rule` dynamically
- **Inline waits** → `wait (!motion for 10m)` works like native HA triggers
- **Color temperature in Kelvin** → `light.kelvin = 2700`
- **Modular packages/imports** → split automations across files with public/private exports
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
| `schedules_<pkg>.yaml`     | Time/sun-based schedule sensors (v0.4.0)      |

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

## 🗓️ Holiday & Workday Integration (v0.4.0)

HASSL now supports `holidays <id>:` schedules tied to Home Assistant’s **Workday** integration.

To enable holiday and weekday/weekend-aware schedules:

### 1️⃣ Create two Workday sensors in Home Assistant

You must create **two Workday integrations** through the Home Assistant UI.

#### Sensor 1 — `binary_sensor.hassl_<id>_workday`
- **Workdays:** Mon–Fri  
- **Excludes:** `holiday`  
- **Meaning:** ON only on real workdays (Mon–Fri that are not holidays).

#### Sensor 2 — `binary_sensor.hassl_<id>_not_holiday`
- **Workdays:** Mon–Sun  
- **Excludes:** `holiday`  
- **Meaning:** ON every day except official holidays (including weekends).

> In both, set your **Country** and optional **Province/Region** as needed for your locale (e.g., `US`, `CA`, `GB`, etc.).  
> After setup, rename the entity IDs to exactly match:
> - `binary_sensor.hassl_<id>_workday`
> - `binary_sensor.hassl_<id>_not_holiday`  
> where `<id>` matches the identifier used in your `.hassl` file (e.g., `us_ca`).

HASSL derives:
- `binary_sensor.hassl_holiday_<id>` → ON on holidays (even when they fall on weekends).

### Truth table

| Day type                     | `hassl_<id>_workday` | `hassl_<id>_not_holiday` | `hassl_holiday_<id>` (derived) |
|------------------------------|-----------------------|---------------------------|---------------------------------|
| Tue (normal)                | on                    | on                        | off                             |
| Sat (normal weekend)        | off                   | on                        | off                             |
| Mon that’s an official holiday | off                 | off                       | on                              |
| Sat that’s an official holiday | off                 | off                       | on                              |

This distinction lets you build precise schedules like:
```hassl
holidays us_ca:
    country="US", province="CA"

schedule master_wake:
  on weekdays 06:00–22:00 except holidays us_ca;
  on weekends 08:00–22:00;
  on holidays us_ca 09:00–22:00;
```

🧩 **Note:**  
Both sensors must be created manually in the Home Assistant UI — integrations can’t be defined in YAML.  
Once created, HASSL automatically references them in generated automations.

---

## ⚗️ Experimental: Date & Month Range Schedules

HASSL v0.4.0 includes early support for:

```hassl
on months Jun–Aug 07:00–22:00;
on dates 12-24..01-02 06:00–20:00;
```

These may compile successfully but are **not yet validated in production**.  
They’re marked **experimental** and will be verified after template automation support (v0.4 milestone).

---

## 📚 Documentation

- [Quickstart Guide](./quickstart.md)
- [Language Specification](./Hassl_spec.md)

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
