from typing import ClassVar, Mapping, Sequence, Any, Optional, Tuple
from typing_extensions import Self

from viam.utils import SensorReading

from viam.module.types import Reconfigurable
from viam.proto.app.robot import ComponentConfig
from viam.proto.common import ResourceName
from viam.resource.base import ResourceBase
from viam.resource.types import Model, ModelFamily
from viam.utils import ValueTypes

from viam.components.sensor import Sensor
from viam.logging import getLogger

import time
import sqlite3
import asyncio
import dbus
import dbus.exceptions
import dbus.mainloop.glib
import dbus.service
import uuid
from pathlib import Path
import datetime
import subprocess

try:
    from gi.repository import GLib
except ImportError:
    import glib as GLib

BLUEZ_SERVICE_NAME = 'org.bluez'
GATT_MANAGER_IFACE = 'org.bluez.GattManager1'
LE_ADVERTISING_MANAGER_IFACE = 'org.bluez.LEAdvertisingManager1'
LE_ADVERTISEMENT_IFACE = 'org.bluez.LEAdvertisement1'
GATT_SERVICE_IFACE = 'org.bluez.GattService1'
GATT_CHRC_IFACE = 'org.bluez.GattCharacteristic1'
DBUS_OM_IFACE = 'org.freedesktop.DBus.ObjectManager'
DBUS_PROP_IFACE = 'org.freedesktop.DBus.Properties'
DEVICE_IFACE = 'org.bluez.Device1'
ADAPTER_IFACE = 'org.bluez.Adapter1'
AGENT_IFACE = 'org.bluez.Agent1'
AGENT_MANAGER_IFACE = 'org.bluez.AgentManager1'

LOGGER = getLogger(__name__)

CONNECT_RETRY_SECONDS = 10

def enable_onboard_bluetooth():
    try:
        # Check if Bluetooth via GPIO pin PA.04 is already enabled (and is currently working)
        check_bluetooth = subprocess.run(
            ["hciconfig"], 
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True, 
            check=False
        )
        
        if "UP RUNNING" in check_bluetooth.stdout:
            LOGGER.info("Bluetooth is already up and running, skipping GPIO activation")
            return True

        # Check if the gpiofind command is available and can find GPIO pin PA.04 on the board
        find_result = subprocess.run(
            ["gpiofind", "PA.04"], 
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True, 
            check=False
        )
        
        if find_result.returncode != 0:
            LOGGER.info("GPIO pin PA.04 not found, skipping onboard Bluetooth activation")
            return False
            
        # If PA.04 exists, then activate it
        LOGGER.info("GPIO pin PA.04 detected (activating), enabling onboard Bluetooth...")
        
        # Formatting the GPIO line and value
        gpio_pin = find_result.stdout.strip()
        
        cmd = f"sudo gpioset --mode=signal {gpio_pin.split()[0]} {gpio_pin.split()[1]}=1"
        LOGGER.info(f"Running command: {cmd}")
        
        # Run the command
        subprocess.Popen(
            cmd,
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        # Debug (temporary)
        LOGGER.info("GPIO pin PA.04 initiated")
        return True
            
    except Exception as e:
        LOGGER.warning(f"Error attempting to enable onboard Bluetooth: {str(e)}")
        return False

# Activate GPIO for onboard Bluetooth at module load time (before adapter detection)
try:
    LOGGER.info("Attempting to enable onboard Bluetooth...")
    enable_onboard_bluetooth()
except Exception as e:
    LOGGER.error(f"Error during onboard Bluetooth initialization: {e}")

# JetPack's bluetooth.service already runs bluetoothd with --noplugin=audio,a2dp,avrcp,
# so a2dp can't take over device audio. Start it in case an older module version stopped it.
def ensure_bluetoothd_running():
    subprocess.run(["systemctl", "start", "bluetooth"], check=False)

class bluetooth(Sensor, Reconfigurable):
    MODEL: ClassVar[Model] = Model(ModelFamily("viam-soleng", "presence"), "bluetooth")
    
    advertisement_name: str
    advertisement = None
    agent = None
    discovery_active = False
    manager = None
    bus = None
    pairing_accept_timeout = int
    device_present_linger = int

    # Constructor
    @classmethod
    def new(cls, config: ComponentConfig, dependencies: Mapping[ResourceName, ResourceBase]) -> Self:
        ensure_bluetoothd_running()
        my_class = cls(config.name)
        my_class.reconfigure(config, dependencies)
        return my_class

    # Validates JSON Configuration
    @classmethod
    def validate(cls, config: ComponentConfig) -> Tuple[Sequence[str], Sequence[str]]:
        return [], []

    # Handles attribute reconfiguration
    def reconfigure(self, config: ComponentConfig, dependencies: Mapping[ResourceName, ResourceBase]):
        if self.manager:
            self.manager.stop()
            self.manager = None

        self.advertisement_name = config.attributes.fields["advertisement_name"].string_value or "Viam Presence"
        self.pairing_accept_timeout = int(config.attributes.fields["pairing_accept_timeout"].number_value) or 60
        self.device_present_linger = int(config.attributes.fields["device_present_linger"].number_value) or 30
        try:
            asyncio.ensure_future(self.start_btmanager())
        except Exception as e:
            LOGGER.error(f"Error initializing or running BluetoothManager: {e}")
        return
    
    async def close(self):
        if self.manager:
            self.manager.stop()
            self.manager = None
        return await super().close()

    async def start_btmanager(self):
        # runs as a background task, so log failures here or they are never seen
        manager = None
        try:
            manager = BluetoothManager(auto_accept=False, custom_name=self.advertisement_name,
                                       pairing_accept_timeout=self.pairing_accept_timeout, device_present_linger=self.device_present_linger)
            self.manager = manager
            self.bus = dbus.SystemBus()
            await manager.start()
        except Exception as e:
            LOGGER.error(f"Error initializing or running BluetoothManager: {e}")
            if manager:
                manager.stop()
            # a later reconfigure may have replaced the manager already
            if self.manager is manager:
                self.manager = None

    def require_manager(self):
        if not self.manager:
            raise RuntimeError("Bluetooth manager is not running; check the module logs")
        return self.manager

    async def get_readings(
        self, *, extra: Optional[Mapping[str, Any]] = None, timeout: Optional[float] = None, **kwargs
    ) -> Mapping[str, SensorReading]:
        manager = self.require_manager()
        ret = {
            "present_devices": manager.present_devices,
            "known_devices": manager.paired_devices,
            "pairing_requests": manager.current_pairing_requests()
        }
        return ret

    async def do_command(
                self,
                command: Mapping[str, ValueTypes],
                *,
                timeout: Optional[float] = None,
                **kwargs
            ) -> Mapping[str, ValueTypes]:
        result = {}
        manager = self.require_manager()
        if 'command' in command:
            if command['command'] == 'accept_pairing_request':
                label = ""
                if "label" in command:
                    label = command["label"]
                paired = manager.accept_pairing_request(command["device"], label)
                return { "paired": paired }
            if command['command'] == 'forget_device':
                forgot = manager.forget_device(command["device"])  
                return { "forgot": forgot }

class Advertisement(dbus.service.Object):
    PATH_BASE = '/org/bluez/example/advertisement'

    def __init__(self, bus, index, advertising_type):
        self.path = self.PATH_BASE + str(index)
        self.bus = bus
        self.ad_type = advertising_type
        self.service_uuids = None
        self.manufacturer_data = None
        self.solicit_uuids = None
        self.service_data = None
        self.local_name = None
        self.include_tx_power = None
        dbus.service.Object.__init__(self, bus, self.path)

    def get_properties(self):
        properties = dict()
        properties['Type'] = self.ad_type
        if self.service_uuids is not None:
            properties['ServiceUUIDs'] = dbus.Array(self.service_uuids, signature='s')
        if self.solicit_uuids is not None:
            properties['SolicitUUIDs'] = dbus.Array(self.solicit_uuids, signature='s')
        if self.manufacturer_data is not None:
            properties['ManufacturerData'] = dbus.Dictionary(self.manufacturer_data, signature='qv')
        if self.service_data is not None:
            properties['ServiceData'] = dbus.Dictionary(self.service_data, signature='sv')
        if self.local_name is not None:
            properties['LocalName'] = dbus.String(self.local_name)
        if self.include_tx_power is not None:
            properties['IncludeTxPower'] = dbus.Boolean(self.include_tx_power)
        return {LE_ADVERTISEMENT_IFACE: properties}

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def add_service_uuid(self, uuid):
        if not self.service_uuids:
            self.service_uuids = []
        if uuid not in self.service_uuids:
            self.service_uuids.append(uuid)

    def add_local_name(self, name):
        self.local_name = name

    @dbus.service.method(LE_ADVERTISEMENT_IFACE,
                         in_signature='',
                         out_signature='')
    def Release(self):
        LOGGER.info('%s: Released!', self.path)

class Agent(dbus.service.Object):
    def __init__(self, bus, path, auto_accept=False):
        self.bus = bus
        self.path = path
        self.auto_accept = auto_accept
        self.pairing_requests = []
        self.manager = None
        dbus.service.Object.__init__(self, bus, path)

    def get_path(self):
        return dbus.ObjectPath(self.path)

    @dbus.service.method(AGENT_IFACE, in_signature="os", out_signature="")
    def AuthorizeService(self, device, uuid):
        LOGGER.info(f"AuthorizeService ({device}, {uuid})")
        return

    @dbus.service.method(AGENT_IFACE, in_signature="o", out_signature="")
    def RequestAuthorization(self, device):
        LOGGER.info(f"RequestAuthorization ({device})")
        if self.auto_accept:
            self.add_paired_device(device)
            return
        return

    @dbus.service.method(AGENT_IFACE, in_signature="", out_signature="")
    def Cancel(self):
        LOGGER.info("Cancel")

    @dbus.service.method(AGENT_IFACE, in_signature="os", out_signature="")
    def DisplayPinCode(self, device, pincode):
        LOGGER.info(f"DisplayPinCode ({device}, {pincode})")

    @dbus.service.method(AGENT_IFACE, in_signature="ou", out_signature="")
    def DisplayPasskey(self, device, passkey):
        LOGGER.info(f"DisplayPasskey ({device}, {passkey})")

    @dbus.service.method(AGENT_IFACE, in_signature="ou", out_signature="")
    def RequestConfirmation(self, device, passkey):
        # ensure leading zero is cap
        passkey = f'{passkey:06}'

        LOGGER.info(f"RequestConfirmation ({device}, {passkey})")
        if self.auto_accept:
            self.add_paired_device(device)
            return
        self.pairing_requests.append({ "device": device, "passkey": passkey, "when": time.time() })

        return

    def add_paired_device(self, device):
        if self.manager:
            self.manager.add_paired_device(device)
        else:
            LOGGER.error("BluetoothManager reference not set in Agent")

class BluetoothManager:
    def __init__(self, auto_accept=False, custom_name="Viam Presence", pairing_accept_timeout=60, device_present_linger=30):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SystemBus()
        
        self.om = dbus.Interface(self.bus.get_object(BLUEZ_SERVICE_NAME, "/"), DBUS_OM_IFACE)
        self.adapter_path = self.find_adapter()
        
        if self.adapter_path:
            self.adapter = dbus.Interface(self.bus.get_object(BLUEZ_SERVICE_NAME, self.adapter_path), ADAPTER_IFACE)
            self.adapter_props = dbus.Interface(self.bus.get_object(BLUEZ_SERVICE_NAME, self.adapter_path), DBUS_PROP_IFACE)
            self.agent_manager = dbus.Interface(self.bus.get_object(BLUEZ_SERVICE_NAME, "/org/bluez"), AGENT_MANAGER_IFACE)
            self.ad_manager = dbus.Interface(self.bus.get_object(BLUEZ_SERVICE_NAME, self.adapter_path), LE_ADVERTISING_MANAGER_IFACE)
        else:
            LOGGER.error("No Bluetooth adapter found")
            raise RuntimeError("No Bluetooth adapter found")

        self.paired_devices = {}
        self.present_devices = {}
        self.connect_attempts = {}
        # we could make this configurable but it should be stable here
        self.db_conn = sqlite3.connect( str(Path.home()) + '/.viam/paired_devices.db')        
        self.create_db_table()
        self.advertisement = None
        self.agent = None
        self.auto_accept = auto_accept
        self.discovery_active = False
        self.custom_name = custom_name
        self.pairing_accept_timeout = pairing_accept_timeout
        self.device_present_linger = device_present_linger

        self.signal_match = self.bus.add_signal_receiver(
                    self.properties_changed,
                    dbus_interface="org.freedesktop.DBus.Properties",
                    signal_name="PropertiesChanged",
                    path_keyword="path"
                )
        
    def properties_changed(self, interface, changed, invalidated, path):
        if interface != DEVICE_IFACE:
            return
        # only a connect marks a device present; a disconnect means it is leaving.
        # check_for_devices keeps only known devices, so a device mid-pairing is harmless here.
        if changed.get("Connected"):
            self.update_present_device(path)
            
    def create_db_table(self):
        cursor = self.db_conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS paired_devices (
                id TEXT PRIMARY KEY,
                address TEXT,
                name TEXT,
                uuid TEXT,
                last_seen TIMESTAMP
            )
        ''')
        self.db_conn.commit()

    def find_adapter(self):
        objects = self.om.GetManagedObjects()
        for o, props in objects.items():
            if ADAPTER_IFACE in props:
                return o
        return None

    def start_advertising(self):
        if self.advertisement:
            LOGGER.warning("Advertisement already running")
            return

        self.advertisement = Advertisement(self.bus, 0, 'peripheral')

        generic_service_uuid = "00001000-0000-1000-8000-00805F9B34FB"
        self.advertisement.add_service_uuid(generic_service_uuid)
        self.advertisement.add_local_name(self.custom_name)

        self.advertisement.include_tx_power = True

        try:
            self.ad_manager.RegisterAdvertisement(self.advertisement.get_path(), {},
                                                  reply_handler=self.register_ad_cb,
                                                  error_handler=self.register_ad_error_cb)
            LOGGER.info("Advertisement started")
        except Exception as e:
            LOGGER.error(f"Error registering advertisement: {e}")
           
    def stop_advertising(self):
        if self.advertisement:
            try:
                self.ad_manager.UnregisterAdvertisement(self.advertisement)
                LOGGER.info("Advertisement stopped")
            except dbus.exceptions.DBusException as e:
                LOGGER.error(f"Error unregistering advertisement: {e}")
            # SystemBus() is shared, so the next manager re-exports this path
            self.advertisement.remove_from_connection()
            self.advertisement = None
        else:
            LOGGER.warning("No advertisement running")

    def register_ad_cb(self):
        LOGGER.debug("Advertisement registered")

    def register_ad_error_cb(self, error):
        LOGGER.error(f"Failed to register advertisement: {error}")

    def current_pairing_requests(self):
        if not self.agent or not isinstance(self.agent.pairing_requests, list):
            return []
        
        pairing_requests = []
        self.prune_pairing_requests()
        for request in self.agent.pairing_requests:
            pairing_requests.append ({
                'passkey': request["passkey"],
                'device': str(request["device"]),
                'when': datetime.datetime.fromtimestamp(request["when"]).isoformat()
            })
        return pairing_requests

    def remove_physical_pairing(self, device_path):
        try:
            adapter = dbus.Interface(self.bus.get_object(BLUEZ_SERVICE_NAME, self.adapter_path), ADAPTER_IFACE)
            adapter.RemoveDevice(device_path)
            LOGGER.info(f"Successfully removed pairing for device: {device_path}")            
            return True
        except dbus.exceptions.DBusException as e:
            LOGGER.error(f"Failed to remove pairing for device {device_path}: {e}")
            return False

    def remove_device_from_db(self, device_id):
        cursor = self.db_conn.cursor()
        cursor.execute('DELETE FROM paired_devices WHERE id = ?', (device_id,))
        self.db_conn.commit()
        LOGGER.info(f"Removed device {device_id} from database")

    def prune_pairing_requests(self):
        if not self.agent:
            return
        current_time = time.time()
        expired = [request for request in self.agent.pairing_requests
                   if current_time - request["when"] >= self.pairing_accept_timeout]
        # rebuild rather than del while iterating, which skips the next request
        self.agent.pairing_requests = [request for request in self.agent.pairing_requests
                                       if current_time - request["when"] < self.pairing_accept_timeout]
        self.unpair_expired_requests(expired)

    def unpair_expired_requests(self, expired):
        # the agent confirms every pairing, so unpair devices nobody accepted in time
        pending = {request["device"] for request in self.agent.pairing_requests}
        for device_path in {request["device"] for request in expired} - pending:
            address = self.device_address(device_path)
            if address and not any(info["address"] == address for info in self.paired_devices.values()):
                self.remove_physical_pairing(device_path)

    def device_address(self, device_path):
        try:
            device = dbus.Interface(self.bus.get_object(BLUEZ_SERVICE_NAME, device_path), DBUS_PROP_IFACE)
            return str(device.Get(DEVICE_IFACE, "Address"))
        except dbus.exceptions.DBusException:
            return None

    def accept_pairing_request(self, device, label):
        if self.agent:
            remaining = [request for request in self.agent.pairing_requests if request["device"] != device]
            if len(remaining) == len(self.agent.pairing_requests):
                LOGGER.warning(f"No pairing request found for device: {device}")
                return False
            # keep the request queued on failure so it can be retried until it expires
            if not self.add_paired_device(device, label):
                return False
            # a device can have several requests queued; drop them all and pair once
            self.agent.pairing_requests = remaining
            return True
        else:
            LOGGER.error("Agent not initialized")
            return False

    def forget_device(self, device):
        if self.agent:
            forgot = False
            if device in self.paired_devices:
                device_path = self.find_device_by_address(self.paired_devices[device]["address"])
                if device_path:
                    self.remove_physical_pairing(device_path)
                self.remove_device_from_db(device)
                del self.paired_devices[device]
                LOGGER.info(f"Known device forgotten: {device}")
                forgot = True
            else:
                LOGGER.warning(f"Known device not found: {device}")
            return forgot
        else:
            LOGGER.error("Agent not initialized")    
            return False

    def update_present_device(self, device_path):
        device = dbus.Interface(self.bus.get_object(BLUEZ_SERVICE_NAME, device_path), DBUS_PROP_IFACE)
        try:
            address = device.Get(DEVICE_IFACE, "Address")
            name = device.Get(DEVICE_IFACE, "Name")
        except dbus.exceptions.DBusException:
            LOGGER.error(f"Unable to get device properties for {device_path}")
            return
        
        uuids = device.Get(DEVICE_IFACE, "UUIDs")
        device_uuid = uuids[0] if uuids else ""
        device_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, name + address))

        # device ID can be user selected, try to match on address
        for stored_id, stored_info in self.paired_devices.items():
            if address == stored_info["address"]:
                device_id = stored_id

        self.present_devices[device_id] = {
            'address': address,
            'name': name,
            'uuid': device_uuid,
            'when': time.time()
        }       

    def add_paired_device(self, device_path, label):
        device = dbus.Interface(self.bus.get_object(BLUEZ_SERVICE_NAME, device_path), DBUS_PROP_IFACE)
        try:
            address = device.Get(DEVICE_IFACE, "Address")
            name = device.Get(DEVICE_IFACE, "Name")
        except dbus.exceptions.DBusException:
            LOGGER.error(f"Unable to get device properties for {device_path}")
            return False

        try:
            uuids = device.Get(DEVICE_IFACE, "UUIDs")
        except dbus.exceptions.DBusException:
            # BlueZ omits UUIDs for devices that advertise no services
            uuids = []
        device_uuid = uuids[0] if uuids else ""
        device_id = label or str(uuid.uuid5(uuid.NAMESPACE_DNS, name + address))

        self.paired_devices[device_id] = {
            'address': address,
            'name': name,
            'uuid': device_uuid
        }
        self.update_device_in_db(device_id, address, name, device_uuid)
        LOGGER.info(f"Added paired device to database: {name} ({address})")
        return True


    async def start(self):
        LOGGER.info("Starting Bluetooth Manager...")

        # Clean up any existing D-Bus resources
        try:
            # Stop any existing discovery
            if self.discovery_active:
                self.adapter.StopDiscovery()
                self.discovery_active = False
                LOGGER.info("Stopped existing discovery")

            # Unregister any existing agent
            if hasattr(self, 'agent') and self.agent is not None:
                try:
                    self.agent_manager.UnregisterAgent(self.agent.get_path())
                    self.agent.remove_from_connection(self.bus)
                    self.agent = None
                    LOGGER.info("Unregistered existing agent")
                except Exception as e:
                    LOGGER.info(f"No existing agent to unregister (or error): {e}")

            # Unregister any existing advertisement
            if self.advertisement:
                try:
                    self.ad_manager.UnregisterAdvertisement(self.advertisement.get_path())
                    self.advertisement = None
                    LOGGER.info("Unregistered existing advertisement")
                except Exception as e:
                    LOGGER.info(f"No existing advertisement to unregister (or error): {e}")

            # Add a small delay to let BlueZ settle
            await asyncio.sleep(1)

        except Exception as e:
            LOGGER.warning(f"Error during cleanup: {e}")

        # Now proceed with normal startup
        self.adapter_props.Set(ADAPTER_IFACE, "Powered", dbus.Boolean(True))
        self.adapter_props.Set(ADAPTER_IFACE, "Discoverable", dbus.Boolean(True))
        self.adapter_props.Set(ADAPTER_IFACE, "DiscoverableTimeout", dbus.UInt32(0))
        self.adapter_props.Set(ADAPTER_IFACE, "Pairable", dbus.Boolean(True))
        self.adapter_props.Set(ADAPTER_IFACE, "Alias", self.custom_name)

        self.start_advertising()

        self.agent = Agent(self.bus, "/org/bluez/agent", auto_accept=self.auto_accept)
        self.agent.manager = self
        try:
            self.agent_manager.RegisterAgent(self.agent.get_path(), "KeyboardDisplay")
            LOGGER.info("Agent registered with KeyboardDisplay")
            self.agent_manager.RequestDefaultAgent(self.agent.get_path())
            LOGGER.info("Agent set as default")
        except Exception as e:
            LOGGER.error(f"Failed to register agent: {e}")
            raise RuntimeError("Failed to register Bluetooth agent.")

        self.adapter.SetDiscoveryFilter({'Transport': 'le'})
        self.adapter.StartDiscovery()
        self.discovery_active = True

        LOGGER.info(f'Bluetooth Manager started with custom name "{self.custom_name}" and is now discoverable.')
        self.load_paired_devices()
        self.running = True
        await self.main_loop()


    async def main_loop(self):
        while self.running:
            context = GLib.MainContext.default()
            while context.pending():
                context.iteration(False)
            await self.periodic_scan()
            await asyncio.sleep(1)

    def stop(self):
        LOGGER.info("Stopping Bluetooth Manager...")
        self.running = False
        self.stop_advertising()

        if self.agent:
            try:
                self.agent_manager.UnregisterAgent(self.agent.get_path())
            except dbus.exceptions.DBusException as e:
                LOGGER.error(f"Error unregistering agent: {e}")
            self.agent.remove_from_connection()
            self.agent = None

        if self.signal_match:
            self.signal_match.remove()
            self.signal_match = None

        if self.discovery_active:
            try:
                self.adapter.StopDiscovery()
                LOGGER.info("Discovery stopped")
                self.discovery_active = False
            except dbus.exceptions.DBusException as e:
                LOGGER.error(f"Error stopping discovery: {e}")

        if hasattr(self, 'mainloop'):
            self.mainloop.quit()

        if hasattr(self, 'db_conn'):
            self.db_conn.close()

        LOGGER.info("Bluetooth Manager stopped")

    def load_paired_devices(self):
        LOGGER.info("Loading paired devices from database:")
        cursor = self.db_conn.cursor()
        cursor.execute('SELECT id, address, name, uuid FROM paired_devices')
        for row in cursor.fetchall():
            device_id, address, name, device_uuid = row
            LOGGER.info(f"Loaded paired device: {name} ({address})")
            self.paired_devices[device_id] = {
                'address': address,
                'name': name,
                'uuid': device_uuid
            }

    def update_device_in_db(self, device_id, address, name, device_uuid):
        cursor = self.db_conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO paired_devices (id, address, name, uuid, last_seen)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (device_id, address, name, device_uuid))
        self.db_conn.commit()

    async def periodic_scan(self):
        LOGGER.debug("Performing periodic scan...")
        try:
            if not self.discovery_active:
                self.adapter.StartDiscovery()
                self.discovery_active = True
                LOGGER.debug("Discovery started")
            else:
                LOGGER.debug("Discovery already active, skipping start")
            self.check_for_devices()
            self.prune_pairing_requests()
        except dbus.exceptions.DBusException as e:
            LOGGER.error(f"Error during periodic scan: {e}")
        return True


    def check_for_devices(self):
        objects = self.om.GetManagedObjects()
        for path, interfaces in objects.items():
            if DEVICE_IFACE not in interfaces:
                continue
            properties = interfaces[DEVICE_IFACE]
            if not properties:
                continue
            address = properties["Address"]
            name = properties.get("Name", "<unknown>")
            uuids = properties.get("UUIDs", [])
            device_uuid = uuids[0] if uuids else ""
            device_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, name + address))
            if self.is_known_device(device_id, address, name, device_uuid):
                # a device that stays connected sends no new Connected events, so refresh it here
                if properties.get("Connected", False):
                    self.update_present_device(path)
                else:
                    self.auto_connect_device(path, address)

        # update present device list, removing devices not seen recently
        updated_present_devices = {}
        # Check for devices that are no longer present
        for device_id, device_info in list(self.paired_devices.items()):
            if device_id in self.present_devices:
                if time.time() - self.present_devices[device_id]["when"] < self.device_present_linger:
                    updated_present_devices[device_id] = self.present_devices[device_id]
        self.present_devices = updated_present_devices

    def auto_connect_device(self, device_path, address):
        # Connect() blocks until BlueZ times out when the device is out of range,
        # so call it asynchronously and at most once per CONNECT_RETRY_SECONDS
        now = time.time()
        if now - self.connect_attempts.get(address, 0) < CONNECT_RETRY_SECONDS:
            return
        self.connect_attempts[address] = now
        LOGGER.debug(f"Attempting to automatically connect to known device: {address}")
        device = dbus.Interface(self.bus.get_object(BLUEZ_SERVICE_NAME, device_path), DEVICE_IFACE)
        device.Connect(reply_handler=lambda: LOGGER.debug(f"Connected to known device: {address}"),
                       error_handler=lambda e: LOGGER.debug(f"Error auto-connecting to device {address}: {e}"))

    def is_known_device(self, device_id, address, name, device_uuid):
        if device_id in self.paired_devices:
            LOGGER.debug(f"Device {name} ({address}) found in paired_devices by ID")
            return True
    
        # match on address only: names and the first service UUID are shared across many
        # devices. A bonded device keeps its address, because BlueZ resolves its rotating
        # private address to its identity address.
        for stored_id, stored_info in self.paired_devices.items():
            if stored_info['address'] == address:
                LOGGER.debug(f"Device {name} ({address}) matched with stored device {stored_info['name']} ({stored_info['address']})")
                updated_name = name if name != "<unknown>" else f"Unknown Device ({address[-6:]})"
                updated_info = {
                    'address': address,
                    'name': updated_name,
                    'uuid': device_uuid
                }
                # the scan runs every second, so only write to the database when something changed
                if updated_info != stored_info:
                    self.paired_devices[stored_id] = updated_info
                    self.update_device_in_db(stored_id, address, updated_name, device_uuid)
                return True
    
        LOGGER.debug(f"Device {name} ({address}) is not a known device")
        return False


    def find_device_by_address(self, address):
        objects = self.om.GetManagedObjects()
        for path, interfaces in objects.items():
            if DEVICE_IFACE not in interfaces:
                continue
            if interfaces[DEVICE_IFACE]["Address"] == address:
                return path
        return None