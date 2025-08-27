"""Config flow for Easun Inverter integration."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.core import callback
import logging
from datetime import timedelta

from . import DOMAIN
from .const import DEFAULT_SCAN_INTERVAL
from easunpy.discover import discover_device
from easunpy.utils import get_local_ip
from easunpy.models import MODEL_CONFIGS

_LOGGER = logging.getLogger(__name__)


class EasunInverterConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Easun Inverter."""

    VERSION = 4

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Handle a flow initialized by the user."""
        errors: dict[str, str] = {}

        if user_input is not None:
            inverter_ip = user_input["inverter_ip"]
            local_ip = user_input["local_ip"]
            scan_interval = user_input.get("scan_interval", DEFAULT_SCAN_INTERVAL)
            model = user_input["model"]

            _LOGGER.debug(f"Processing user input: IP={inverter_ip}, model={model}, interval={scan_interval}")

            if not inverter_ip or not local_ip:
                errors["base"] = "missing_ip"
            else:
                # Create the entry, storing only inverter settings in data,
                # and the polling interval in options so it can be reloaded cleanly.
                return self.async_create_entry(
                    title=f"Easun Inverter ({inverter_ip})",
                    data={
                        "inverter_ip": inverter_ip,
                        "local_ip": local_ip,
                        "model": model,
                    },
                    options={"scan_interval": scan_interval},
                )

        # First time, attempt discovery
        inverter_ip = discover_device()
        local_ip = get_local_ip()
        if not inverter_ip or not local_ip:
            errors["base"] = "discovery_failed"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("inverter_ip", default=inverter_ip): str,
                    vol.Required("local_ip", default=local_ip): str,
                    vol.Optional("scan_interval", default=DEFAULT_SCAN_INTERVAL): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=3600)
                    ),
                    vol.Required("model", default="ISOLAR_SMG_II_11K"): vol.In(list(MODEL_CONFIGS.keys())),
                }
            ),
            errors=errors,
        )


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            _LOGGER.debug(f"Updating scan_interval to {user_input['scan_interval']} seconds")

            # Update only the scan_interval option
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                options={"scan_interval": user_input["scan_interval"]},
            )

            # Reload the config entry so async_setup_entry re-runs with new interval
            self.hass.async_create_task(
                self.hass.config_entries.async_reload(self.config_entry.entry_id)
            )

            return self.async_create_entry(title="", data=user_input)

        # Show the form pre-filled with current values
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "inverter_ip",
                        default=self.config_entry.data["inverter_ip"],
                    ): str,
                    vol.Required(
                        "local_ip",
                        default=self.config_entry.data["local_ip"],
                    ): str,
                    vol.Required(
                        "model",
                        default=self.config_entry.data.get("model", "ISOLAR_SMG_II_11K"),
                    ): vol.In(list(MODEL_CONFIGS.keys())),
                    vol.Optional(
                        "scan_interval",
                        default=self.config_entry.options.get(
                            "scan_interval", DEFAULT_SCAN_INTERVAL
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=3600)),
                }
            ),
        )
