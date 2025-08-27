"""Constants for Easun Inverter integration."""
DOMAIN = "easun_inverter"

CONF_INVERTER_IP = "inverter_ip"
CONF_LOCAL_IP = "local_ip"
CONF_MODEL = "model"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_SCAN_INTERVAL = 5

SIGNAL_COLLECTOR_UPDATED = f"{DOMAIN}_collector_updated"

PLATFORMS = ["sensor", "select", "number", "button"]
