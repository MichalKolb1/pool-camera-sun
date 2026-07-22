"""Config flow for Pool Camera Sun."""

from typing import Any

import voluptuous as vol

from homeassistant.components.camera import DOMAIN as CAMERA_DOMAIN
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import selector

from .const import CONF_CAMERA_ENTITY_ID, DOMAIN, ENTITY_NAME


class PoolCameraSunConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Pool Camera Sun."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        if user_input is not None:
            camera_entity_id = user_input[CONF_CAMERA_ENTITY_ID]
            await self.async_set_unique_id(camera_entity_id)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=ENTITY_NAME,
                data={CONF_CAMERA_ENTITY_ID: camera_entity_id},
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_CAMERA_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=CAMERA_DOMAIN)
                )
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)
