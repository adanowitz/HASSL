# 🧠 Introducing HASSL — Home Assistant Simple Scripting Language

Hi everyone,  

I started using Home Assistant about a year ago — mostly as a fun project for my Raspberry Pi 5 and to get all my smart devices playing nicely with HomeKit.  

Once I started writing automations, I realized how complex even “simple” tasks can be — like syncing multiple lights and switches without loops, or handling motion-based lighting with manual overrides.  

Blueprints were often too limited, and Node-RED looked like another learning curve.  
What I really wanted was an **Arduino-style scripting experience** — something where I could describe logic naturally, and let a compiler handle the YAML.  

---

## ✨ Enter HASSL
**HASSL** is a small domain-specific language (DSL) that compiles human-readable `.hassl` scripts into fully functional Home Assistant packages.

It automatically:
- Syncs lights, switches, and fans safely (no feedback loops)  
- Supports schedules like `enable from 08:00 until 19:00`  
- Adds `not_by` guards to prevent loops  
- Generates helpers, scripts, and automations automatically  
- Survives HA restarts (schedules re-evaluate automatically)

---

## 🧩 Example

```hassl
alias light  = light.wesley_lamp
alias motion = binary_sensor.wesley_motion_motion
alias lux    = sensor.wesley_motion_illuminance

schedule wake_hours:
  enable from 08:00 until 19:00;

rule motion_light:
  schedule use wake_hours;
  if (motion && lux < 50)
  then light = on;
  wait (!motion for 10m) light = off

rule manual_off:
  if (light == off) not_by any_hassl
  then disable rule motion_light for 3m
```

Compile it:
```bash
hasslc myroom.hassl -o ./packages/myroom/
```
Drop the package into `/config/packages/` — and it just works.

---

## 🖼️ Visual Overview

**Before (traditional YAML):**
```yaml
- id: "motion_light"
  trigger:
    - platform: state
      entity_id: binary_sensor.hall_motion
  condition:
    - condition: template
      value_template: "{{ states('sensor.hall_lux') | float < 50 }}"
  action:
    - service: light.turn_on
      target:
        entity_id: light.hall
```

**After (HASSL):**
```hassl
if (motion && lux < 50) then light = on
```

---

## 💡 Try it out

📦 **GitHub:** [https://github.com/adanowitz/hassl](https://github.com/adanowitz/hassl)  
🐍 **PyPI:** `pip install hassl`

This is an early release (v0.2.0), but it’s already powering my lighting and motion automations reliably.  
I’d love feedback from anyone who wants a simpler, developer-friendly way to write Home Assistant logic!

---

### ❤️ Feedback Welcome
If you try HASSL, please share what kinds of automations you build — motion lighting, sync groups, energy-saving, etc.  
Contributions and suggestions are always welcome!
