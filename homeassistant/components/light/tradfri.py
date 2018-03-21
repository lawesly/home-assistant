"""
Support for the IKEA Tradfri platform.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/light.tradfri/
"""
import logging

from homeassistant.core import callback
import homeassistant.util.color as color_util
from homeassistant.components.light import (
    ATTR_BRIGHTNESS, ATTR_COLOR_TEMP, ATTR_HS_COLOR, ATTR_TRANSITION,
    SUPPORT_BRIGHTNESS, SUPPORT_TRANSITION, SUPPORT_COLOR_TEMP,
    SUPPORT_COLOR, Light)
from homeassistant.components.light import \
    PLATFORM_SCHEMA as LIGHT_PLATFORM_SCHEMA
from homeassistant.components.tradfri import KEY_GATEWAY, KEY_TRADFRI_GROUPS, \
    KEY_API

_LOGGER = logging.getLogger(__name__)

ATTR_TRANSITION_TIME = 'transition_time'
DEPENDENCIES = ['tradfri']
PLATFORM_SCHEMA = LIGHT_PLATFORM_SCHEMA
IKEA = 'IKEA of Sweden'
TRADFRI_LIGHT_MANAGER = 'Tradfri Light Manager'
SUPPORTED_FEATURES = (SUPPORT_BRIGHTNESS | SUPPORT_TRANSITION)


def normalize_xy(argx, argy, brightness=None):
    """Normalise XY to Tradfri scaling."""
    return (int(argx*65535+0.56), int(argy*65535+0.56))


def denormalize_xy(argx, argy, brightness=None):
    """Denormalise XY from Tradfri scaling."""
    return (argx/65535-0.56, argy/65535-0.56)


async def async_setup_platform(hass, config,
                               async_add_devices, discovery_info=None):
    """Set up the IKEA Tradfri Light platform."""
    if discovery_info is None:
        return

    gateway_id = discovery_info['gateway']
    api = hass.data[KEY_API][gateway_id]
    gateway = hass.data[KEY_GATEWAY][gateway_id]

    devices_command = gateway.get_devices()
    devices_commands = await api(devices_command)
    devices = await api(devices_commands)
    lights = [dev for dev in devices if dev.has_light_control]
    if lights:
        async_add_devices(TradfriLight(light, api) for light in lights)

    allow_tradfri_groups = hass.data[KEY_TRADFRI_GROUPS][gateway_id]
    if allow_tradfri_groups:
        groups_command = gateway.get_groups()
        groups_commands = await api(groups_command)
        groups = await api(groups_commands)
        if groups:
            async_add_devices(TradfriGroup(group, api) for group in groups)


class TradfriGroup(Light):
    """The platform class required by hass."""

    def __init__(self, light, api):
        """Initialize a Group."""
        self._api = api
        self._group = light
        self._name = light.name

        self._refresh(light)

    async def async_added_to_hass(self):
        """Start thread when added to hass."""
        self._async_start_observe()

    @property
    def should_poll(self):
        """No polling needed for tradfri group."""
        return False

    @property
    def supported_features(self):
        """Flag supported features."""
        return SUPPORTED_FEATURES

    @property
    def name(self):
        """Return the display name of this group."""
        return self._name

    @property
    def is_on(self):
        """Return true if group lights are on."""
        return self._group.state

    @property
    def brightness(self):
        """Return the brightness of the group lights."""
        return self._group.dimmer

    async def async_turn_off(self, **kwargs):
        """Instruct the group lights to turn off."""
        await self._api(self._group.set_state(0))

    async def async_turn_on(self, **kwargs):
        """Instruct the group lights to turn on, or dim."""
        keys = {}
        if ATTR_TRANSITION in kwargs:
            keys['transition_time'] = int(kwargs[ATTR_TRANSITION]) * 10

        if ATTR_BRIGHTNESS in kwargs:
            if kwargs[ATTR_BRIGHTNESS] == 255:
                kwargs[ATTR_BRIGHTNESS] = 254

            await self._api(
                self._group.set_dimmer(kwargs[ATTR_BRIGHTNESS], **keys))
        else:
            await self._api(self._group.set_state(1))

    @callback
    def _async_start_observe(self, exc=None):
        """Start observation of light."""
        # pylint: disable=import-error
        from pytradfri.error import PytradfriError
        if exc:
            _LOGGER.warning("Observation failed for %s", self._name,
                            exc_info=exc)

        try:
            cmd = self._group.observe(callback=self._observe_update,
                                      err_callback=self._async_start_observe,
                                      duration=0)
            self.hass.async_add_job(self._api(cmd))
        except PytradfriError as err:
            _LOGGER.warning("Observation failed, trying again", exc_info=err)
            self._async_start_observe()

    def _refresh(self, group):
        """Refresh the light data."""
        self._group = group
        self._name = group.name

    @callback
    def _observe_update(self, tradfri_device):
        """Receive new state data for this light."""
        self._refresh(tradfri_device)
        self.async_schedule_update_ha_state()


class TradfriLight(Light):
    """The platform class required by Home Assistant."""

    def __init__(self, light, api):
        """Initialize a Light."""
        self._api = api
        self._light = None
        self._light_control = None
        self._light_data = None
        self._name = None
        self._hs_color = None
        self._features = SUPPORTED_FEATURES
        self._available = True

        self._refresh(light)

    @property
    def min_mireds(self):
        """Return the coldest color_temp that this light supports."""
        return self._light_control.min_mireds

    @property
    def max_mireds(self):
        """Return the warmest color_temp that this light supports."""
        return self._light_control.max_mireds

    async def async_added_to_hass(self):
        """Start thread when added to hass."""
        self._async_start_observe()

    @property
    def available(self):
        """Return True if entity is available."""
        return self._available

    @property
    def should_poll(self):
        """No polling needed for tradfri light."""
        return False

    @property
    def supported_features(self):
        """Flag supported features."""
        return self._features

    @property
    def name(self):
        """Return the display name of this light."""
        return self._name

    @property
    def is_on(self):
        """Return true if light is on."""
        return self._light_data.state

    @property
    def brightness(self):
        """Return the brightness of the light."""
        return self._light_data.dimmer

    @property
    def color_temp(self):
        """Return the CT color value in mireds."""
        return self._light_data.color_temp

    @property
    def xy_color(self):
        """XY colour of the light."""
        if self._light_data.xy_color and self._light_control.can_set_color:
            return denormalize_xy(*self._light_data.xy_color)

    @property
    def hs_color(self):
        """HS color of the light."""
        return self._hs_color

    async def async_turn_off(self, **kwargs):
        """Instruct the light to turn off."""
        await self._api(self._light_control.set_state(False))

    async def async_turn_on(self, **kwargs):
        """
        Instruct the light to turn on.

        After adding "self._light_data.hexcolor is not None"
        for ATTR_HS_COLOR, this also supports Philips Hue bulbs.
        """
        if ATTR_HS_COLOR in kwargs and self._light_data.hex_color is not None:
            rgb = color_util.color_hs_to_RGB(*kwargs[ATTR_HS_COLOR])
            yield from self._api(
                self._light.light_control.set_rgb_color(*rgb))

        elif ATTR_COLOR_TEMP in kwargs and \
                self._light_data.hex_color is not None and \
                self._temp_supported:
            kelvin = color_util.color_temperature_mired_to_kelvin(
                kwargs[ATTR_COLOR_TEMP])
            yield from self._api(
                self._light_control.set_kelvin_color(kelvin))

        params = {}
        if ATTR_TRANSITION in kwargs:
            params[ATTR_TRANSITION_TIME] = int(kwargs[ATTR_TRANSITION]) * 10

        brightness = kwargs.get(ATTR_BRIGHTNESS)

        if ATTR_XY_COLOR in kwargs:
            if brightness is not None:
                params.pop(ATTR_TRANSITION_TIME, None)
            await self._api(
                self._light_control.set_xy_color(
                    *normalize_xy(*kwargs[ATTR_XY_COLOR]), **params))
        elif ATTR_RGB_COLOR in kwargs:
            if brightness is not None:
                params.pop(ATTR_TRANSITION_TIME, None)
            argxy = color_util.color_RGB_to_xy(*kwargs[ATTR_RGB_COLOR])
            await self._api(
                self._light_control.set_xy_color(*normalize_xy(argxy[0],
                                                               argxy[1]),
                                                 **params))
        elif ATTR_COLOR_TEMP in kwargs:
            if brightness is not None:
                params.pop(ATTR_TRANSITION_TIME, None)
            await self._api(
                self._light_control.set_color_temp(kwargs[ATTR_COLOR_TEMP],
                                                   **params))

        if brightness is not None:
            if brightness == 255:
                brightness = 254

            await self._api(
                self._light_control.set_dimmer(brightness,
                                               **params))
        else:
            await self._api(
                self._light_control.set_state(True))

    @callback
    def _async_start_observe(self, exc=None):
        """Start observation of light."""
        # pylint: disable=import-error
        from pytradfri.error import PytradfriError
        if exc:
            _LOGGER.warning("Observation failed for %s", self._name,
                            exc_info=exc)

        try:
            cmd = self._light.observe(callback=self._observe_update,
                                      err_callback=self._async_start_observe,
                                      duration=0)
            self.hass.async_add_job(self._api(cmd))
        except PytradfriError as err:
            _LOGGER.warning("Observation failed, trying again", exc_info=err)
            self._async_start_observe()

    def _refresh(self, light):
        """Refresh the light data."""
        self._light = light

        # Caching of LightControl and light object
        self._available = light.reachable
        self._light_control = light.light_control
        self._light_data = light.light_control.lights[0]
        self._name = light.name
        self._hs_color = None
        self._features = SUPPORTED_FEATURES

        if self._light_control.can_set_mireds:
            self._features |= SUPPORT_COLOR_TEMP
        if self._light_control.can_set_color:
            self._features |= SUPPORT_COLOR

    @callback
    def _observe_update(self, tradfri_device):
        """Receive new state data for this light."""
        self._refresh(tradfri_device)
        self.async_schedule_update_ha_state()
