DOMAIN_PROPS = {
    "light": {"onoff", "brightness", "color_temp", "hs_color"},
    "switch": {"onoff"},
    "fan": {"onoff", "percentage", "preset_mode"},
    "media_player": {"onoff", "volume", "mute", "source", "play_state"},
}
def domain_of(entity_id: str) -> str:
    return entity_id.split(".", 1)[0]
