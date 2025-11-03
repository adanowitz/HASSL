# HASSL Quickstart (v1.4 – 2025 Edition, v0.3.1 Update)

Welcome to **HASSL**, the Home Assistant Simple Scripting Language — a compact, human-readable DSL that compiles into reliable, loop-safe Home Assistant automations.

This quickstart helps you:

- 🧰 Install the HASSL compiler
- ✍️ Write your first `.hassl` file
- ⚙️ Compile to a working Home Assistant package
- 🔍 Test and extend automations
- 📅 Configure Workday and Holiday-aware schedules (v0.3.1)

---

## 1. 🧩 Install the compiler

Clone and install locally:

```bash
git clone https://github.com/adanowitz/hassl.git
cd hassl
pip install -e .
```

Verify installation:

```bash
hasslc --help
```

---

## 2. ✍️ Write your first `.hassl` file

Create a file called `living.hassl`:

```hassl
alias light  = light.living
alias motion = binary_sensor.hall_motion
alias lux    = sensor.living_luminance

# Keep the wall switch and smart light in sync
sync shared [light.living, switch.living_circuit] as living_sync

# Turn on when motion detected in low light
rule motion_on_light:
  if (light == off && motion && lux < 50)
  then light = on for 10m

# Turn off if light stays on but no motion for 1 hour
rule switch_keep_on:
  if (light == on not_by any_hassl)
  then wait (!motion for 1h) light = off

# Disable motion automation after manual off
rule switch_off_disable_motion:
  if (light == off) not_by any_hassl
  then disable rule motion_on_light for 3m
```

---

## 3. 🏗 Compile to Home Assistant package

Run:

```bash
hasslc living.hassl -o ./packages/living_room/
```

This generates a complete package containing:

| File                             | Purpose                                                           |
| -------------------------------- | ----------------------------------------------------------------- |
| `helpers_living_room.yaml`       | Defines `input_boolean`, `input_text`, and `input_number` helpers |
| `scripts_living_room.yaml`       | Context-aware writer scripts                                      |
| `sync_living_room_*.yaml`        | Device and proxy synchronization automations                      |
| `rules_bundled_living_room.yaml` | Rule logic automations                                            |
| `schedules_living_room.yaml`     | Template schedule sensors and time/sun gating (v0.3.1)            |

All filenames include the package slug to avoid collisions between multiple HASSL integrations.

---

## 4. 🏡 Load into Home Assistant

Copy your new package into Home Assistant:

```bash
cp -r packages/living_room/ /config/packages/
```

If you haven’t enabled packages yet, add this to your `configuration.yaml`:

```yaml
homeassistant:
  packages: !include_dir_named packages
```

Then **restart Home Assistant** or reload automations + scripts.

---

## 5. ✅ Verify and test

- Flip the switch → the synced entities update together.
- Motion + lux < 50 → light turns on for 10 minutes.
- Manual off → disables the motion rule for 3 minutes.

Use **Developer Tools → States** or the **Logbook** to observe the HASSL-generated helpers and scripts in action.

---

## 6. 🕒 Add a schedule

Limit automation to daytime hours:

```hassl
schedule wake_hours:
  enable from 08:00 until 19:00;

rule motion_on_light:
  schedule use wake_hours;
  if (motion && lux < 50)
  then light = on for 10m
```

HASSL automatically:

- Creates a `binary_sensor.hassl_schedule_<package>_wake_hours_active`
- Adds ON/OFF gating logic in compiled automations
- Maintains correct state across restarts

---

## 7. 🗓️ Add Workday & Holiday-Aware Schedules (v0.3.1)

HASSL 0.3.1 adds full **weekday/weekend/holiday** support through the Home Assistant **Workday integration**.

### Step 1 — Create two Workday sensors in Home Assistant

You’ll define these via the **UI**, not YAML.

#### Sensor 1 — `binary_sensor.hassl_<id>_workday`
- **Workdays:** Mon–Fri  
- **Excludes:** `holiday`  
- Meaning: ON on *true workdays* (Mon–Fri that aren’t holidays)

#### Sensor 2 — `binary_sensor.hassl_<id>_not_holiday`
- **Workdays:** Mon–Sun  
- **Excludes:** `holiday`  
- Meaning: ON on all days that are *not holidays*, including weekends

Set your **Country** and (optional) **Province/Region** in each configuration, then rename the entities to match those IDs.

> Example:  
> If your HASSL script defines `holidays us_ca:`, name your sensors:  
> - `binary_sensor.hassl_us_ca_workday`  
> - `binary_sensor.hassl_us_ca_not_holiday`

HASSL automatically defines a third derived sensor in the compiled package:

| Derived Sensor | Description |
|----------------|-------------|
| `binary_sensor.hassl_holiday_<id>` | ON on official holidays (even if they fall on weekends) |

### Step 2 — Use in your `.hassl` schedules

```hassl
holidays us_ca:
    country="US", province="CA"

schedule master_wake:
  on weekdays 06:00–22:00 except holidays us_ca;
  on weekends 08:00–22:00;
  on holidays us_ca 09:00–22:00;
```

### Truth Table

| Day | `workday` | `not_holiday` | Derived `holiday` |
|-----|------------|----------------|-------------------|
| Tuesday (normal) | on | on | off |
| Saturday (normal) | off | on | off |
| Monday holiday | off | off | on |
| Saturday holiday | off | off | on |

These sensors ensure holidays and weekends are always distinct, letting your schedules behave as expected.

---

## 8. 💡 Extend your automations

Try these:

```hassl
# Sync everything in a workspace
sync all [light.desk, light.strip, light.lamp] as work_sync { invert: light.lamp }

# Adjust color temperature
rule warm_light_evening:
  if (time >= 18:00 && light == on)
  then light.kelvin = 2700
```

---

## 9. 🧠 Learn more

See the [HASSL Language Specification](./hassl_language_spec_v1.4_2025_updated_v0.3.1.md) for detailed grammar and semantics, including package imports, private exports, and schedule sensor logic.

---

### Happy automating with HASSL!

Effortless logic, clean YAML, and predictable automation — all from one simple script.
