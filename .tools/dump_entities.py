# ha_dump_entities.py
import requests, json

# adjust these:
BASE_URL = "http://homeassistant.local:8123"
TOKEN = "YOUR_LONG_LIVED_ACCESS_TOKEN"

headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}

resp = requests.get(f"{BASE_URL}/api/states", headers=headers, timeout=10)
resp.raise_for_status()

entities = [
    {
        "entity_id": e["entity_id"],
        "name": e["attributes"].get("friendly_name", ""),
        "domain": e["entity_id"].split(".")[0],
    }
    for e in resp.json()
]

with open("ha_entities.json", "w", encoding="utf-8") as f:
    json.dump(entities, f, indent=2)

print(f"Wrote {len(entities)} entities to ha_entities.json")
