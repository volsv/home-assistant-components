import asyncio
import logging
import voluptuous as vol
import homeassistant.helpers.config_validation as cv
import homeassistant.components.remote as remote

from homeassistant.components.climate import (ClimateDevice, PLATFORM_SCHEMA,
                                              STATE_ON, STATE_OFF, STATE_IDLE, STATE_HEAT, STATE_COOL, STATE_AUTO,
                                              ATTR_OPERATION_MODE, ATTR_OPERATION_LIST, ATTR_MAX_TEMP, ATTR_MIN_TEMP,
                                              ATTR_CURRENT_TEMPERATURE, ATTR_TARGET_TEMP_STEP, ATTR_FAN_MODE,
                                              ATTR_FAN_LIST, ATTR_AWAY_MODE,
                                              SUPPORT_OPERATION_MODE, SUPPORT_TARGET_TEMPERATURE, SUPPORT_FAN_MODE,
                                              SUPPORT_ON_OFF, SUPPORT_AWAY_MODE)
from homeassistant.const import (ATTR_UNIT_OF_MEASUREMENT, ATTR_TEMPERATURE, CONF_NAME, CONF_CUSTOMIZE)
from homeassistant.helpers.event import async_track_state_change
from homeassistant.helpers.restore_state import async_get_last_state
from homeassistant.core import callback
from voluptuous import ALLOW_EXTRA

_LOGGER = logging.getLogger(__name__)

DEPENDENCIES = ['remote']

CONF_REMOTE = 'remote'
CONF_TEMP_SENSOR = 'temp_sensor'
CONF_POWER_TEMPLATE = 'power_template'
CONF_TARGET_TEMP = 'target_temp'
CONF_COMMANDS = 'commands'

DEFAULT_NAME = 'Xiaomi Remote Climate'
DEFAULT_MIN_TEMP = 16
DEFAULT_MAX_TEMP = 32
DEFAULT_TARGET_TEMP = 24
DEFAULT_TARGET_TEMP_STEP = 1
DEFAULT_OPERATION_LIST = [STATE_HEAT, STATE_COOL, STATE_AUTO]
DEFAULT_FAN_MODE_LIST = ['low', 'medium', 'high', 'auto']
DEFAULT_OPERATION = STATE_COOL
DEFAULT_FAN_MODE = 'auto'

ATTR_POWER = 'power'
ATTR_SUPPORTED_FEATURES = 'supported_features'

COMMAND_POWER_OFF = 'off'
COMMAND_IDLE = 'idle'

CUSTOMIZE_SCHEMA = vol.Schema({
    vol.Optional(ATTR_OPERATION_LIST): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(ATTR_FAN_LIST): vol.All(cv.ensure_list, [cv.string])
})

COMMANDS_SCHEMA = vol.Schema({
    vol.Required(COMMAND_POWER_OFF): cv.string,
    vol.Optional(COMMAND_IDLE): cv.string
}, extra=ALLOW_EXTRA)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Required(CONF_REMOTE): cv.entity_id,
    vol.Optional(CONF_TEMP_SENSOR): cv.entity_id,
    vol.Optional(CONF_POWER_TEMPLATE): cv.template,
    vol.Optional(ATTR_MIN_TEMP, default=DEFAULT_MIN_TEMP): cv.positive_int,
    vol.Optional(ATTR_MAX_TEMP, default=DEFAULT_MAX_TEMP): cv.positive_int,
    vol.Optional(CONF_TARGET_TEMP, default=DEFAULT_TARGET_TEMP): cv.positive_int,
    vol.Optional(ATTR_TARGET_TEMP_STEP, default=DEFAULT_TARGET_TEMP_STEP): cv.positive_int,
    vol.Optional(ATTR_OPERATION_MODE, default=DEFAULT_OPERATION): cv.string,
    vol.Optional(ATTR_FAN_MODE, default=DEFAULT_FAN_MODE): cv.string,
    vol.Optional(CONF_CUSTOMIZE, default={}): CUSTOMIZE_SCHEMA,
    vol.Required(CONF_COMMANDS): COMMANDS_SCHEMA
})


@asyncio.coroutine
def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    name = config.get(CONF_NAME)
    remote_entity_id = config.get(CONF_REMOTE)
    commands = config.get(CONF_COMMANDS)

    min_temp = config.get(ATTR_MIN_TEMP)
    max_temp = config.get(ATTR_MAX_TEMP)
    target_temp = config.get(CONF_TARGET_TEMP)
    target_temp_step = config.get(ATTR_TARGET_TEMP_STEP)
    operation_list = config.get(CONF_CUSTOMIZE).get(ATTR_OPERATION_LIST, []) or DEFAULT_OPERATION_LIST
    fan_list = config.get(CONF_CUSTOMIZE).get(ATTR_FAN_LIST, []) or DEFAULT_FAN_MODE_LIST
    default_operation = config.get(ATTR_OPERATION_MODE)
    default_fan_mode = config.get(ATTR_FAN_MODE)

    temp_entity_id = config.get(CONF_TEMP_SENSOR)
    power_template = config.get(CONF_POWER_TEMPLATE)

    async_add_devices([
        RemoteClimate(hass, name, remote_entity_id, commands, min_temp, max_temp, target_temp, target_temp_step,
                      operation_list, fan_list, default_operation, default_fan_mode, temp_entity_id, power_template)
    ])


class RemoteClimate(ClimateDevice):
    def __init__(self, hass, name, remote_entity_id, commands, min_temp, max_temp, target_temp, target_temp_step,
                 operation_list, fan_list, default_operation, default_fan_mode, temp_entity_id, power_template):

        self.hass = hass
        self._name = name
        self._remote_entity_id = remote_entity_id
        self._commands = commands

        self._min_temp = min_temp
        self._max_temp = max_temp
        self._target_temperature = target_temp
        self._target_temperature_step = target_temp_step
        self._unit_of_measurement = hass.config.units.temperature_unit

        self._current_temperature = None
        self._current_operation = default_operation
        self._current_fan_mode = default_fan_mode

        self._temp_entity_id = temp_entity_id
        self._power_template = power_template

        self._on = False
        self._away = False

        self._operation_list = operation_list
        self._fan_list = fan_list

        self._support_flags = SUPPORT_ON_OFF | SUPPORT_TARGET_TEMPERATURE | SUPPORT_OPERATION_MODE | SUPPORT_FAN_MODE
        self._enabled_flags = SUPPORT_ON_OFF

        if COMMAND_IDLE in commands:
            self._support_flags = self._support_flags | SUPPORT_AWAY_MODE

        if temp_entity_id:
            async_track_state_change(hass, temp_entity_id, self._async_temp_changed)

            temp_state = hass.states.get(temp_entity_id)
            if temp_state:
                self._async_update_current_temp(temp_state)

        if power_template:
            power_template.hass = hass
            power_entity_ids = power_template.extract_entities()
            async_track_state_change(hass, power_entity_ids, self._async_power_changed)
            self._async_update_current_power()

    @asyncio.coroutine
    def _async_temp_changed(self, entity_id, old_state, new_state):
        if new_state is None:
            return

        self._async_update_current_temp(new_state)
        yield from self.async_update_ha_state()

    @callback
    def _async_update_current_temp(self, state):
        unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        try:
            self._current_temperature = self.hass.config.units.temperature(float(state.state), unit)
        except ValueError as ex:
            self._current_temperature = None
            _LOGGER.warn('Unable to update temperature from sensor: %s', ex)

    @asyncio.coroutine
    def _async_power_changed(self, entity_id, old_state, new_state):
        if new_state is None:
            return

        self._async_update_current_power()
        self.schedule_update_ha_state()

    @callback
    def _async_update_current_power(self):
        try:
            self._on = self._power_template.async_render().lower() in ('true', 'on', '1')
            self.update_flags_get_command()
        except TemplateError as ex:
            _LOGGER.warn('Unable to update power from template: %s', ex)

    @property
    def should_poll(self):
        return False

    @property
    def name(self):
        return self._name

    @property
    def temperature_unit(self):
        return self._unit_of_measurement

    @property
    def current_temperature(self):
        return self._current_temperature

    @property
    def min_temp(self):
        return self._min_temp

    @property
    def max_temp(self):
        return self._max_temp

    @property
    def target_temperature(self):
        return self._target_temperature

    @property
    def target_temperature_step(self):
        return self._target_temperature_step

    @property
    def current_operation(self):
        return self._current_operation

    @property
    def operation_list(self):
        return self._operation_list

    @property
    def current_fan_mode(self):
        return self._current_fan_mode

    @property
    def fan_list(self):
        return self._fan_list

    @property
    def state_attributes(self):
        data = super().state_attributes
        data[ATTR_POWER] = STATE_ON if self._on else STATE_OFF
        return data

    @property
    def is_on(self):
        return self._on

    @property
    def is_away_mode_on(self):
        return self._away

    @property
    def supported_features(self):
        return self._enabled_flags

    def update_flags_get_command(self):
        if not self._on:
            command = self._commands[COMMAND_POWER_OFF]
            self._enabled_flags = SUPPORT_ON_OFF
        elif self._away:
            command = self._commands[COMMAND_IDLE]
            self._enabled_flags = SUPPORT_ON_OFF | SUPPORT_AWAY_MODE
        else:
            operation = self._current_operation.lower()
            fan_mode = self._current_fan_mode.lower()
            temp = int(self._target_temperature)

            try:
                if isinstance(self._commands[operation], str):
                    command = self._commands[operation]
                    self._enabled_flags = self._support_flags ^ SUPPORT_TARGET_TEMPERATURE ^ SUPPORT_FAN_MODE
                elif isinstance(self._commands[operation][fan_mode], str):
                    command = self._commands[operation][fan_mode]
                    self._enabled_flags = self._support_flags ^ SUPPORT_TARGET_TEMPERATURE
                else:
                    command = self._commands[operation][fan_mode][temp]
                    self._enabled_flags = self._support_flags
            except KeyError:
                command = None
                _LOGGER.error('Could not find command for %s/%s/%s', operation, fan_mode, temp)

        return command

    def send_command(self, command):
        remote.send_command(self.hass, 'raw:' + command, entity_id=self._remote_entity_id)

    def send_ir(self):
        command = self.update_flags_get_command()
        if command is not None:
            self.send_command(command)

    def set_temperature(self, **kwargs):
        if kwargs.get(ATTR_TEMPERATURE) is not None:
            self._target_temperature = kwargs.get(ATTR_TEMPERATURE)
            self.send_ir()
            self.schedule_update_ha_state()

    def set_fan_mode(self, fan):
        self._current_fan_mode = fan
        self.send_ir()
        self.schedule_update_ha_state()

    def set_operation_mode(self, operation_mode):
        self._current_operation = operation_mode
        self.send_ir()
        self.schedule_update_ha_state()

    def turn_on(self):
        self._on = True
        self._away = False
        self.send_ir()
        self.schedule_update_ha_state()

    def turn_off(self):
        self._on = False
        self._away = False
        self.send_ir()
        self.schedule_update_ha_state()

    def turn_away_mode_on(self):
        self._away = True
        self.send_ir()
        self.schedule_update_ha_state()

    def turn_away_mode_off(self):
        self._away = False
        self.send_ir()
        self.schedule_update_ha_state()

    @asyncio.coroutine
    def async_added_to_hass(self):
        state = yield from async_get_last_state(self.hass, self.entity_id)

        if state is not None:
            self._current_operation = state.attributes.get(ATTR_OPERATION_MODE, self._current_operation)
            self._target_temperature = state.attributes.get(ATTR_TEMPERATURE, self._target_temperature)
            self._enabled_flags = state.attributes.get(ATTR_SUPPORTED_FEATURES, self._enabled_flags)
            self._current_fan_mode = state.attributes.get(ATTR_FAN_MODE, self._current_fan_mode)
            self._on = state.attributes.get(ATTR_POWER, STATE_OFF) == STATE_ON
            self._away = state.attributes.get(ATTR_AWAY_MODE, STATE_OFF) == STATE_ON
