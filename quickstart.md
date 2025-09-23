# HASSL Quickstart

This guide walks you through installing the HASSL compiler, writing your first `.hassl` file, and running it in Home Assistant.

---

## 1. Install the compiler

HASSL will ship as a Python package. For now, clone the repo and install it locally:

```bash
git clone https://github.com/adanowitz/hassl.git
cd hassl
pip install -e .
```

This gives you the CLI tool:

```bash
hasslc --help
```

---

## 2. Write your first `.hassl` file

Create a file called `living.hassl`:

```hassl
alias light  = light.living
alias motion = binary_sensor.hall_motion
alias lux    = sensor.living_luminance

sync shared [light.living, switch.living_circuit] as living_sync

rule motion_on_light:
  if (light == off && motion && lux < 50)
  then light = on for 10m

rule switch_keep_on:
  if (light == on not_by any_hassl)
  then wait (!motion for 1h) light = off

rule switch_off_disable_motion:
  if (light transitions off not_by any_hassl)
  then disable rule motion_on_light for 3m
```

---

## 3. Compile to Home Assistant package

Run:

```bash
hasslc living.hassl -o ./packages/hassl_living/
```

This generates a package directory with:

- `helpers.yaml` â input_booleans, input_texts, etc.
- `scripts.yaml` â context-stamping writer scripts
- `sync__*.yaml` â automations for sync groups
- `rule__*.yaml` â automations for rules

---

## 4. Load into Home Assistant

1. Copy the `packages/hassl_living/` folder into your Home Assistant `config/packages/` directory.  
   If you don't have `packages` enabled, add this to your `configuration.yaml`:

   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```

2. Restart Home Assistant (or reload automations + scripts).

---

## 5. Verify and test

- Turn on the light via the switch â the `living_sync` group will stay in sync.  
- Motion in the room with lux < 50 â light turns on for 10 minutes.  
- Switch it off â motion automation is suppressed for 3 minutes.  

Check the **Logbook** or **Developer Tools â States** to see HASSL helpers and proxies working behind the scenes.

---

## 6. Next steps

- Try `sync all [light.desk, light.strip, light.lamp] as work_sync { invert: light.lamp }`
- Explore `not_by rule("name")` to separate causes between rules.
- Contribute more property mappings (fans, media players, covers).

---
