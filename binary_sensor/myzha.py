"""
Binary sensors on Zigbee Home Automation networks.

For more details on this platform, please refer to the documentation
at https://home-assistant.io/components/binary_sensor.zha/
"""
import asyncio
import logging

from homeassistant.components.binary_sensor import DOMAIN, BinarySensorDevice
from custom_components import myzha as zha

_LOGGER = logging.getLogger(__name__)

DEPENDENCIES = ['myzha']

# ZigBee Cluster Library Zone Type to Home Assistant device class
CLASS_MAPPING = {
    0x000d: 'motion',
    0x0015: 'opening',
    0x0028: 'smoke',
    0x002a: 'moisture',
    0x002b: 'gas',
    0x002d: 'vibration',
}

DEFAULT_DEVICE_CLASS = 'opening'


@asyncio.coroutine
def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    """Set up the Zigbee Home Automation binary sensors."""
    discovery_info = zha.get_discovery_info(hass, discovery_info)
    if discovery_info is None:
        return

    from bellows.zigbee.zcl.clusters.security import IasZone
    from bellows.zigbee.zcl.clusters.general import OnOff

    in_clusters = discovery_info['in_clusters']

    device_class = None
    if IasZone.cluster_id in in_clusters:
        cluster = in_clusters[IasZone.cluster_id]
        if discovery_info['new_join']:
            yield from cluster.bind()
            ieee = cluster.endpoint.device.application.ieee
            yield from cluster.write_attributes({'cie_addr': ieee})

        try:
            zone_type = yield from cluster['zone_type']
            device_class = CLASS_MAPPING.get(zone_type, None)
        except Exception:  # pylint: disable=broad-except
            # If we fail to read from the device, use a non-specific class
            pass
    elif OnOff.cluster_id in in_clusters:
        cluster = in_clusters[OnOff.cluster_id]
        if discovery_info['new_join']:
            yield from cluster.bind()
        device_class = DEFAULT_DEVICE_CLASS
    else:
        _LOGGER.info("No valid cluster found for binary_sensor.")

    sensor = BinarySensor(device_class, **discovery_info)
    async_add_devices([sensor])


class BinarySensor(zha.Entity, BinarySensorDevice):
    """THe ZHA Binary Sensor."""

    _domain = DOMAIN
    value_attribute = 0

    def __init__(self, device_class, **kwargs):
        """Initialize the ZHA binary sensor."""
        if device_class is not None:
            self.friendly_name = device_class.title()
        else:
            self.friendly_name = "binary"
        super().__init__(**kwargs)
        self._device_class = device_class
        from bellows.zigbee.zcl.clusters.security import IasZone
        if IasZone.cluster_id in self._in_clusters:
            self._ias_zone_cluster = self._in_clusters[IasZone.cluster_id]

    @property
    def is_on(self) -> bool:
        """Return True if entity is on."""
        if self._state == 'unknown':
            return False
        return bool(self._state)

    @property
    def device_class(self):
        """Return the class of this device, from component DEVICE_CLASSES."""
        return self._device_class

    def cluster_command(self, aps_frame, tsn, command_id, args):
        """Handle commands received to this cluster."""
        from bellows.zigbee.zcl.clusters.security import IasZone
        if command_id == 0 and IasZone.cluster_id in self._in_clusters:
            self._state = args[0] & 3
            _LOGGER.debug("Updated alarm state: %s", self._state)
            self.schedule_update_ha_state()
        elif command_id == 1 and IasZone.cluster_id in self._in_clusters:
            _LOGGER.debug("Enroll requested")
            self.hass.add_job(self._ias_zone_cluster.enroll_response(0, 0))
        node_entity = zha.get_node_entity(self.hass, self._ieee)
        node_entity.update_last_modified()

    def attribute_updated(self, attribute, value):
        """Handle attribute update from device."""
        _LOGGER.debug("Attribute updated: %s %s %s", self, attribute, value)
        if attribute == self.value_attribute:
            self._state = bool(value)
            self.schedule_update_ha_state()
        node_entity = zha.get_node_entity(self.hass, self._ieee)
        node_entity.update_last_modified()
