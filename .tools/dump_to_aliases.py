#!/usr/bin/env python3
"""
Convert a Home Assistant entity dump (ha_entities.json)
into a Hassl aliases.hassl file.
"""

import json
from pathlib import Path
import re

# Input / output paths
INPUT = Path("ha_entities.json")
OUTPUT = Path("aliases.hassl")

# Optional: skip noisy integration domains
SKIP_DOMAINS = {
    "zone", "persistent_notification", "group", "scene",
    "script", "automation", "update", "event", "sun",
}

def _slugify(name: str) -> str:
    """Turn a friendly name or entity_id into a valid Hassl alias name."""
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    # prevent alias starting with number
    if s and s[0].isdigit():
        s = "e_" + s
    return s

def main():
    data = json.loads(INPUT.read_text())
    lines = ["package home.aliases", ""]
    aliases = []

    for e in data:
        entity_id = e["entity_id"]
        domain = e.get("domain") or entity_id.split(".")[0]
        if domain in SKIP_DOMAINS:
            continue
        friendly = e.get("name") or entity_id.split(".")[-1]
        alias_name = _slugify(friendly)
        # guarantee uniqueness
        base = alias_name
        i = 2
        while alias_name in aliases:
            alias_name = f"{base}_{i}"
            i += 1
        aliases.append(alias_name)
        lines.append(f"alias {alias_name} = {entity_id}")

    OUTPUT.write_text("\n".join(lines) + "\n")
    print(f"[ok] Wrote {len(aliases)} aliases to {OUTPUT}")

if __name__ == "__main__":
    main()
