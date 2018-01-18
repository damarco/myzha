"""
Support for ZigBee Home Automation devices.

For more details about this component, please refer to the documentation at
https://home-assistant.io/components/zha/
"""
import asyncio
import logging
import datetime

import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant import const as ha_const
from homeassistant.helpers import discovery, entity
from homeassistant.util import slugify
from homeassistant.helpers.entity_component import EntityComponent

REQUIREMENTS = ['http://github.com/damarco/bellows'
                '/archive/master.zip'
                '#bellows==0.5.0']

DOMAIN = 'myzha'

CONF_BAUDRATE = 'baudrate'
CONF_DATABASE = 'database_path'
CONF_CHANNEL = 'channel'
CONF_DEVICE_CONFIG = 'device_config'
CONF_USB_PATH = 'usb_path'
DATA_DEVICE_CONFIG = 'zha_device_config'

DEVICE_CONFIG_SCHEMA_ENTRY = vol.Schema({
    vol.Optional(ha_const.CONF_TYPE): cv.string,
})

CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        CONF_USB_PATH: cv.string,
        vol.Optional(CONF_BAUDRATE, default=57600): cv.positive_int,
        vol.Optional(CONF_CHANNEL, default=15): 
            vol.All(vol.Coerce(int), vol.Range(11, 26)),
        CONF_DATABASE: cv.string,
        vol.Optional(CONF_DEVICE_CONFIG, default={}):
            vol.Schema({cv.string: DEVICE_CONFIG_SCHEMA_ENTRY}),
    })
}, extra=vol.ALLOW_EXTRA)

ATTR_DURATION = 'duration'

SERVICE_PERMIT = 'permit'
SERVICE_DESCRIPTIONS = {
    SERVICE_PERMIT: {
        "description": "Allow nodes to join the ZigBee network",
        "fields": {
            ATTR_DURATION: {
                "description": "Time to permit joins, in seconds",
                "example": "60",
            },
        },
    },
}
SERVICE_SCHEMAS = {
    SERVICE_PERMIT: vol.Schema({
        vol.Optional(ATTR_DURATION, default=60):
            vol.All(vol.Coerce(int), vol.Range(1, 254)),
    }),
}


# ZigBee definitions
CENTICELSIUS = 'C-100'
# Key in hass.data dict containing discovery info
DISCOVERY_KEY = 'zha_discovery_info'
NODE_ENTITY_KEY = 'zha_node_entity'

# Manufacturer
CENTRALITE = 'CentraLite'
XIAOMI = 'LUMI'

# Internal definitions
APPLICATION_CONTROLLER = None
_LOGGER = logging.getLogger(__name__)


@asyncio.coroutine
def async_setup(hass, config):
    """Set up ZHA.

    Will automatically load components to support devices found on the network.
    """
    global APPLICATION_CONTROLLER

    import bellows.ezsp
    from bellows.zigbee.application import ControllerApplication

    ezsp_ = bellows.ezsp.EZSP()
    usb_path = config[DOMAIN].get(CONF_USB_PATH)
    baudrate = config[DOMAIN].get(CONF_BAUDRATE)
    channel = config[DOMAIN].get(CONF_CHANNEL)
    yield from ezsp_.connect(usb_path, baudrate)

    database = config[DOMAIN].get(CONF_DATABASE)
    APPLICATION_CONTROLLER = ControllerApplication(ezsp_, database)
    listener = ApplicationListener(hass, config)
    APPLICATION_CONTROLLER.add_listener(listener)
    yield from APPLICATION_CONTROLLER.startup(auto_form=True, channel=channel)

    for device in APPLICATION_CONTROLLER.devices.values():
        hass.async_add_job(listener.async_device_initialized(device, False))

    @asyncio.coroutine
    def permit(service):
        """Allow devices to join this network."""
        duration = service.data.get(ATTR_DURATION)
        _LOGGER.info("Permitting joins for %ss", duration)
        yield from APPLICATION_CONTROLLER.permit(duration)

    hass.services.async_register(DOMAIN, SERVICE_PERMIT, permit,
                                 SERVICE_DESCRIPTIONS[SERVICE_PERMIT],
                                 SERVICE_SCHEMAS[SERVICE_PERMIT])

    return True


class ApplicationListener:
    """All handlers for events that happen on the ZigBee application."""

    def __init__(self, hass, config):
        """Initialize the listener."""
        self._hass = hass
        self._config = config
        hass.data[DISCOVERY_KEY] = hass.data.get(DISCOVERY_KEY, {})
        hass.data[NODE_ENTITY_KEY] = hass.data.get(NODE_ENTITY_KEY, {})

    def device_joined(self, device):
        """Handle device joined.

        At this point, no information about the device is known other than its
        address
        """
        # Wait for device_initialized, instead
        pass

    def device_initialized(self, device):
        """Handle device joined and basic information discovered."""
        self._hass.async_add_job(self.async_device_initialized(device, True))

    def device_left(self, device):
        """Handle device leaving the network."""
        pass

    def device_removed(self, device):
        """Handle device being removed from the network."""
        pass

    @asyncio.coroutine
    def async_device_initialized(self, device, join):
        """Handle device joined and basic information discovered (async)."""
        import bellows.zigbee.profiles
        import custom_components.myzha.const as zha_const
        zha_const.populate_data()

        for endpoint_id, endpoint in device.endpoints.items():
            if endpoint_id == 0:  # ZDO
                continue

            discovered_info = yield from _discover_endpoint_info(endpoint)

            component = None
            profile_clusters = ([], [])
            device_key = '%s-%s' % (str(device.ieee), endpoint_id)
            node_config = self._config[DOMAIN][CONF_DEVICE_CONFIG].get(
                device_key, {})

            if endpoint.profile_id in bellows.zigbee.profiles.PROFILES:
                profile = bellows.zigbee.profiles.PROFILES[endpoint.profile_id]
                if zha_const.DEVICE_CLASS.get(endpoint.profile_id,
                                              {}).get(endpoint.device_type,
                                                      None):
                    profile_clusters = profile.CLUSTERS[endpoint.device_type]
                    profile_info = zha_const.DEVICE_CLASS[endpoint.profile_id]
                    component = profile_info[endpoint.device_type]
            else:
                # These may be manufacturer specific profiles and they
                # will need special handling if they are to be supported
                # correctly.
                _LOGGER.info("Skipping endpoint with profile_id: %s",
                             endpoint.profile_id)
                continue

            if ha_const.CONF_TYPE in node_config:
                component = node_config[ha_const.CONF_TYPE]
                profile_clusters = zha_const.COMPONENT_CLUSTERS[component]

            if component:
                in_clusters = [endpoint.in_clusters[c]
                               for c in profile_clusters[0]
                               if c in endpoint.in_clusters]
                out_clusters = [endpoint.out_clusters[c]
                                for c in profile_clusters[1]
                                if c in endpoint.out_clusters]
                discovery_info = {
                    'endpoint': endpoint,
                    'in_clusters': {c.cluster_id: c for c in in_clusters},
                    'out_clusters': {c.cluster_id: c for c in out_clusters},
                    'new_join': join,
                }
                discovery_info.update(discovered_info)
                self._hass.data[DISCOVERY_KEY][device_key] = discovery_info

                yield from discovery.async_load_platform(
                    self._hass,
                    component,
                    DOMAIN,
                    {'discovery_key': device_key},
                    self._config,
                )

            for cluster_id, cluster in endpoint.in_clusters.items():
                cluster_type = type(cluster)
                if cluster_id in profile_clusters[0]:
                    continue
                if cluster_type not in zha_const.SINGLE_CLUSTER_DEVICE_CLASS:
                    continue

                component = zha_const.SINGLE_CLUSTER_DEVICE_CLASS[cluster_type]
                discovery_info = {
                    'endpoint': endpoint,
                    'in_clusters': {cluster.cluster_id: cluster},
                    'out_clusters': {},
                    'new_join': join,
                }
                discovery_info.update(discovered_info)
                cluster_key = '%s-%s' % (device_key, cluster_id)
                self._hass.data[DISCOVERY_KEY][cluster_key] = discovery_info

                yield from discovery.async_load_platform(
                    self._hass,
                    component,
                    DOMAIN,
                    {'discovery_key': cluster_key},
                    self._config,
                )

            component = EntityComponent(_LOGGER, DOMAIN, self._hass)
            in_cluster = {}
            out_cluster = {}
            # Add cluster 0 to node entity for xiaomi device (heartbeat)
            if discovered_info['manufacturer'] == XIAOMI:
                in_cluster[0] = endpoint.in_clusters[0]
            # Add cluster 1 to node entity for centralite device (power)
            if discovered_info['manufacturer'] == CENTRALITE:
                in_cluster[1] = endpoint.in_clusters[1]
            node_entity = ZhaNodeEntity(endpoint, in_cluster, out_cluster, discovered_info['manufacturer'], discovered_info['model'])
            yield from component.async_add_entity(node_entity)
            self._hass.data[NODE_ENTITY_KEY][endpoint.device.ieee] = node_entity


class Entity(entity.Entity):
    """A base class for ZHA entities."""

    _domain = None  # Must be overridden by subclasses
    friendly_name = "ZHA"

    def __init__(self, endpoint, in_clusters, out_clusters, manufacturer,
                 model, **kwargs):
        """Init ZHA entity."""
        self._device_state_attributes = {}
        ieeetail = ''.join([
            '%02x' % (o, ) for o in endpoint.device.ieee[-4:]
        ])
        self._ieee = endpoint.device.ieee
        self._ieeetail = ieeetail
        if manufacturer and model is not None:
            self._manufacturer = manufacturer
            self._model = model
            self.entity_id = '%s.%s_%s_%s_%s_%s' % (
                self._domain,
                slugify(manufacturer),
                slugify(model),
                ieeetail,
                endpoint.endpoint_id,
                slugify(self.friendly_name),
            )
            self._device_state_attributes['friendly_name'] = '%s %s %s' % (
                manufacturer,
                model,
                self.friendly_name,
            )
            self.node_entity = 'zha.%s_%s_%s' % (
                slugify(manufacturer),
                slugify(model),
                ieeetail,
            )
        else:
            self.entity_id = "%s.zha_%s_%s_%s" % (
                self._domain,
                ieeetail,
                endpoint.endpoint_id,
                slugify(self.friendly_name),
            )
            self.node_entity = "zha.zha_%s" % (
                ieeetail,
            )
        for cluster in in_clusters.values():
            cluster.add_listener(self)
        for cluster in out_clusters.values():
            cluster.add_listener(self)
        self._endpoint = endpoint
        self._in_clusters = in_clusters
        self._out_clusters = out_clusters
        self._state = ha_const.STATE_UNKNOWN


    def attribute_updated(self, attribute, value):
        """Handle an attribute updated on this cluster."""
        pass

    def zdo_command(self, aps_frame, tsn, command_id, args):
        """Handle a ZDO command received on this cluster."""
        pass

    @property
    def device_state_attributes(self):
        """Return device specific state attributes."""
        return self._device_state_attributes

class ZhaNodeEntity(entity.Entity):
    """A base class for ZHA nodes."""

    _domain = "zha"
    friendly_name = "Node"

    def __init__(self, endpoint, in_clusters, out_clusters, manufacturer, model, **kwargs):
        """Init ZHA node entity."""
        self._device_state_attributes = {}
        ieeetail = ''.join([
            '%02x' % (o, ) for o in endpoint.device.ieee[-4:]
        ])
        self._ieee = endpoint.device.ieee
        self._ieeetail = ieeetail
        self._manufacturer = manufacturer
        self._model = model
        if manufacturer and model is not None:
            self.entity_id = '%s.%s_%s_%s' % (
                self._domain,
                slugify(manufacturer),
                slugify(model),
                ieeetail,
            )
            self._device_state_attributes['friendly_name'] = '%s %s %s' % (
                manufacturer,
                model,
                self.friendly_name,
            )
        else:
            self.entity_id = "%s.zha_%s" % (
                self._domain,
                ieeetail,
            )
        for cluster in in_clusters.values():
            cluster.add_listener(self)
            cluster.bind()
        for cluster in out_clusters.values():
            cluster.add_listener(self)
        self._device_state_attributes['ieee'] = '%s' % (endpoint.device.ieee, )
        self._device_state_attributes['battery'] = 'unknown'
        self._device_state_attributes['last_update'] = datetime.datetime.now()

        self._endpoint = endpoint
        self._in_clusters = in_clusters
        self._out_clusters = out_clusters
        self._state = ha_const.STATE_UNKNOWN

    def attribute_updated(self, attribute, value):
        """Handle an attribute updated on this cluster."""
        _LOGGER.info("Received node entity attribute: %s, %s", attribute, value)
        # xiaomi manufacturer specific attribute
        if attribute == 0xFF01 and self._manufacturer == XIAOMI:
            self._decode_xiaomi_heartbeat(value)
        # battery voltage
        elif attribute == 0x20 and self._manufacturer == CENTRALITE:
            self._decode_centralite_battery(value)
        self.update_last_modified()

    def _decode_xiaomi_heartbeat(self, data):
        from bellows.zigbee.zcl import foundation
        heartbeat = {}
        unknown = {}
        while data:
            xiaomi_type = data[0]
            value, data = foundation.TypeValue.deserialize(data[1:])
            value = value.value

            if xiaomi_type == 0x01:
                # I'm sure there's a more accurate way to do this
                heartbeat['battery_percentage'] = round((value - 2600) / (3200 - 2600) * 100)
                self.update_battery_percent(heartbeat['battery_percentage'])
                heartbeat['battery_voltage'] = value / 1000
            elif xiaomi_type == 0x03:
                heartbeat['soc_temperature'] = value
            elif xiaomi_type == 0x64:
                heartbeat['state'] = value
            else:
                unknown[xiaomi_type] = value
    
    def _decode_centralite_battery(self, value):
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

        if value < minVolts:
            value = minVolts
        elif value > maxVolts:
            value = maxVolts

        self.update_battery_percent(values.get(value, 'unknown'))

    def zdo_command(self, aps_frame, tsn, command_id, args):
        """Handle a ZDO command received on this cluster."""
        self.update_last_modified()

    def update_last_modified(self):
        self._device_state_attributes['last_update'] = datetime.datetime.now()
        self.schedule_update_ha_state()

    def update_battery_percent(self, percent):
        self._device_state_attributes['battery'] = '%s%%' % (percent, )

    @property
    def device_state_attributes(self):
        """Return device specific state attributes."""
        return self._device_state_attributes


@asyncio.coroutine
def _discover_endpoint_info(endpoint):
    """Find some basic information about an endpoint."""
    extra_info = {
        'manufacturer': None,
        'model': None,
    }
    if 0 not in endpoint.in_clusters:
        return extra_info

    @asyncio.coroutine
    def read(attributes):
        """Read attributes and update extra_info convenience function."""
        result, _ = yield from endpoint.in_clusters[0].read_attributes(
            attributes,
            allow_cache=True,
        )
        extra_info.update(result)

    yield from read(['manufacturer', 'model'])
    if extra_info['manufacturer'] is None or extra_info['model'] is None:
        # Some devices fail at returning multiple results. Attempt separately.
        yield from read(['manufacturer'])
        yield from read(['model'])

    for key, value in extra_info.items():
        if isinstance(value, bytes):
            try:
                extra_info[key] = value.decode('ascii').strip()
            except UnicodeDecodeError:
                # Unsure what the best behaviour here is. Unset the key?
                pass

    return extra_info


def get_discovery_info(hass, discovery_info):
    """Get the full discovery info for a device.

    Some of the info that needs to be passed to platforms is not JSON
    serializable, so it cannot be put in the discovery_info dictionary. This
    component places that info we need to pass to the platform in hass.data,
    and this function is a helper for platforms to retrieve the complete
    discovery info.
    """
    if discovery_info is None:
        return

    discovery_key = discovery_info.get('discovery_key', None)
    all_discovery_info = hass.data.get(DISCOVERY_KEY, {})
    discovery_info = all_discovery_info.get(discovery_key, None)
    return discovery_info


def get_node_entity(hass, ieee):
    if ieee is None:
        return

    all_node_entities = hass.data.get(NODE_ENTITY_KEY, {})
    node_entity = all_node_entities.get(ieee, None)
    return node_entity

