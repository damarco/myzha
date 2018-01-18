"""
Sensors on Zigbee Home Automation networks.

For more details on this platform, please refer to the documentation
at https://home-assistant.io/components/sensor.zha/
"""
import asyncio
import logging
from datetime import timedelta
import datetime

from homeassistant.components.sensor import DOMAIN
from custom_components import myzha as zha
from homeassistant.const import TEMP_CELSIUS
from homeassistant.util.temperature import convert as convert_temperature

_LOGGER = logging.getLogger(__name__)

DEPENDENCIES = ['myzha']

SCAN_INTERVAL = timedelta(seconds=120)

@asyncio.coroutine
def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    """Set up Zigbee Home Automation sensors."""
    discovery_info = zha.get_discovery_info(hass, discovery_info)
    if discovery_info is None:
        return

    sensor = yield from make_sensor(discovery_info)
    async_add_devices([sensor])


@asyncio.coroutine
def make_sensor(discovery_info):
    """Create ZHA sensors factory."""
    from bellows.zigbee.zcl.clusters.measurement import TemperatureMeasurement
    from bellows.zigbee.zcl.clusters.measurement import PressureMeasurement
    from bellows.zigbee.zcl.clusters.measurement import RelativeHumidity
    from bellows.zigbee.zcl.clusters.manufacturer_specific import SmartthingsRelativeHumidity
    from bellows.zigbee.zcl.clusters.general import PowerConfiguration
    in_clusters = discovery_info['in_clusters']
    if TemperatureMeasurement.cluster_id in in_clusters:
        sensor = TemperatureSensor(**discovery_info)
    elif PressureMeasurement.cluster_id in in_clusters:
        sensor = PressureSensor(**discovery_info)
    elif RelativeHumidity.cluster_id in in_clusters:
        sensor = RelativeHumiditySensor(**discovery_info)
    elif SmartthingsRelativeHumidity.cluster_id in in_clusters:
        sensor = RelativeHumiditySensor(**discovery_info)
#    elif PowerConfiguration.cluster_id in in_clusters:
#        sensor = BatteryVoltageSensor(**discovery_info)
#    elif PowerConfiguration.cluster_id in in_clusters \
#            and discovery_info['manufacturer'] == 'CentraLite':
#        sensor = CentraliteBatterySensor(**discovery_info)
    else:
        sensor = Sensor(**discovery_info)

    attr = sensor.value_attribute
    cluster = list(in_clusters.values())[0]
    if discovery_info['new_join']:
        yield from cluster.bind()
        yield from cluster.configure_reporting(
            attr, 10, 600, sensor.min_reportable_change,
        )

    return sensor


class Sensor(zha.Entity):
    """Base ZHA sensor."""

    _domain = DOMAIN
    friendly_name = "Sensor"
    value_attribute = 0
    min_reportable_change = 1

    @property
    def state(self) -> str:
        """Return the state of the entity."""
        if isinstance(self._state, float):
            return str(round(self._state, 2))
        return self._state

    def attribute_updated(self, attribute, value):
        """Handle attribute update from device."""
        _LOGGER.debug("Attribute updated: %s %s %s", self, attribute, value)
        if attribute == self.value_attribute:
            self._state = value
            self.schedule_update_ha_state()
        node_entity = zha.get_node_entity(self.hass, self._ieee)
        node_entity.update_last_modified()

    @asyncio.coroutine
    def async_update(self):
        """Handle polling."""
        if self._state == 'unknown':
            cluster = list(self._in_clusters.values())[0]
            try:
                yield from cluster.read_attributes([self.value_attribute])
            except:
                _LOGGER.info("Failed to read attribute: %s %s %s", self, cluster, self.value_attribute) 


class TemperatureSensor(Sensor):
    """ZHA temperature sensor."""

    friendly_name = "Temperature"
    min_reportable_change = 10  # 0.1'C

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement of this entity."""
        return self.hass.config.units.temperature_unit

    @property
    def state(self):
        """Return the state of the entity."""
        if self._state == 'unknown':
            return 'unknown'
        celsius = round(float(self._state) / 100, 1)
        return convert_temperature(
            celsius, TEMP_CELSIUS, self.unit_of_measurement)


class PressureSensor(Sensor):
    """ZHA pressure sensor."""

    friendly_name = "Pressure"

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement of this entity."""
        return 'hPa'

    @property
    def state(self):
        """Return the state of the entity."""
        if self._state == 'unknown':
            return 'unknown'
        return round(float(self._state))

    @asyncio.coroutine
    def async_update(self):
        """No response for read attribute."""


class RelativeHumiditySensor(Sensor):
    """ZHA relative humidity sensor."""

    friendly_name = "Humidity"
    min_reportable_change = 100  # 1%

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement of this entity."""
        return '%'

    @property
    def state(self):
        """Return the state of the entity."""
        if self._state == 'unknown':
            return 'unknown'
        return round(float(self._state) / 100)

    @asyncio.coroutine
    def async_update(self):
        """No response for read attribute."""


class BatteryVoltageSensor(Sensor):
    """ZHA battery voltage sensor."""

    friendly_name = "Battery"
    value_attribute = 0x0020
    min_reportable_change = 1  # 100mV

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement of this entity."""
        return 'V'

    @property
    def hidden(self) -> bool:
        """Hide by default."""
        return True

    @property
    def state(self):
        """Return the state of the entity."""
        if self._state == 'unknown':
            return 'unknown'
        return round(float(self._state) / 10, 1)

    def attribute_updated(self, attribute, value):
        super().attribute_updated(attribute, value)
        node_entity = zha.get_node_entity(self.hass, self._ieee)
        node_entity.update_battery_percent(self.state)


class CentraliteBatterySensor(Sensor):
    """ZHA battery sensor."""

    # currently restricted to centralite sensors because the value
    # conversion is specific to centralite sensors.

    friendly_name = "Battery"
    value_attribute = 32
    minVolts = 15
    maxVolts = 28
    values = {
        28: 100,
        27: 100,
        26: 100,
        25: 90,
        24: 90,
        23: 70,
        22: 70,
        21: 50,
        20: 50,
        19: 30,
        18: 30,
        17: 15,
        16: 1,
        15: 0
    }

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement of this entity."""
        return '%'

    @property
    def state(self):
        """Return the state of the entity."""
        if self._state == 'unknown':
            return 'unknown'

        if self._state < self.minVolts:
            self._state = self.minVolts
        elif self._state > self.maxVolts:
            self._state = self.maxVolts

        return self.values.get(self._state, 'unknown')

    def attribute_updated(self, attribute, value):
        super().attribute_updated(attribute, value)
        node_entity = zha.get_node_entity(self.hass, self._ieee)
        node_entity.update_battery_percent(self.state)