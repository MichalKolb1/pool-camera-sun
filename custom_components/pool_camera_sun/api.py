"""Authenticated read-only API for retained labeled samples."""

from __future__ import annotations

import logging

from aiohttp import web

from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import (
    API_SAMPLE_IMAGE_PATH,
    API_SAMPLES_PATH,
    DATA_SAMPLE_STORE,
    DOMAIN,
)
from .sample_store import (
    InvalidSampleId,
    SampleNotFound,
    SampleStorageError,
    SampleStore,
)

_LOGGER = logging.getLogger(__name__)


def register_api_views(hass: HomeAssistant) -> None:
    """Register authenticated sample API views once per HA runtime."""
    hass.http.register_view(PoolCameraSunSamplesView)
    hass.http.register_view(PoolCameraSunSampleImageView)


def _sample_store(request: web.Request) -> SampleStore:
    """Return the shared private sample store."""
    hass: HomeAssistant = request.app[KEY_HASS]
    return hass.data[DOMAIN][DATA_SAMPLE_STORE]


class PoolCameraSunSamplesView(HomeAssistantView):
    """List retained labeled sample metadata."""

    url = API_SAMPLES_PATH
    name = "api:pool_camera_sun:samples"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        """Return retained metadata without image bytes."""
        hass: HomeAssistant = request.app[KEY_HASS]
        store = _sample_store(request)
        try:
            samples = await hass.async_add_executor_job(store.list_samples)
        except SampleStorageError as err:
            _LOGGER.error("Unable to list retained camera sample metadata: %s", err)
            raise web.HTTPInternalServerError(
                text="Unable to read retained sample metadata"
            ) from err
        return self.json(
            {
                "count": len(samples),
                "retention_per_label": store.max_samples_per_label,
                "samples": samples,
            }
        )


class PoolCameraSunSampleImageView(HomeAssistantView):
    """Retrieve one retained labeled sample image."""

    url = API_SAMPLE_IMAGE_PATH
    name = "api:pool_camera_sun:sample_image"
    requires_auth = True

    async def get(
        self, request: web.Request, sample_id: str
    ) -> web.Response:
        """Return one image only after strict sample ID validation."""
        hass: HomeAssistant = request.app[KEY_HASS]
        store = _sample_store(request)
        try:
            image, content_type = await hass.async_add_executor_job(
                store.get_image, sample_id
            )
        except InvalidSampleId as err:
            raise web.HTTPBadRequest(text=str(err)) from err
        except SampleNotFound as err:
            raise web.HTTPNotFound(text="Sample image not found") from err
        except SampleStorageError as err:
            _LOGGER.error("Unable to retrieve retained camera sample image: %s", err)
            raise web.HTTPInternalServerError(
                text="Unable to read retained sample image"
            ) from err
        return web.Response(
            body=image,
            content_type=content_type,
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
