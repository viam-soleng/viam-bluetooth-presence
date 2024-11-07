from typing import ClassVar, Mapping, Sequence, Any, Dict, Optional, Tuple, Final, List, cast
from typing_extensions import Self

from typing import Any, Final, Mapping, Optional


from viam.utils import SensorReading

from viam.module.types import Reconfigurable
from viam.proto.app.robot import ComponentConfig
from viam.proto.common import ResourceName, Vector3
from viam.resource.base import ResourceBase
from viam.resource.types import Model, ModelFamily
from viam.utils import ValueTypes, struct_to_dict

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
import os
import signal

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

PID_FILE = "/tmp/bluetoothd_program.pid"

# the plugin a2dp seems to "take over" device audio, so we take over the bluetoothd
# to disable this from happening.  
def restart_bluetooth_without_a2dp():
    stop_bluetoothd_if_running()

    # Stop the Bluetooth service
    subprocess.run([ "systemctl", "stop", "bluetooth"], check=True)
    
    # Start bluetoothd with the -P a2dp option to disable the A2DP plugin
    bluetoothd_process = subprocess.Popen(["bluetoothd", "-P", "a2dp"])
    with open(PID_FILE, "w") as f:
        f.write(str(bluetoothd_process.pid))
    time.sleep(5)

def stop_bluetoothd_if_running():
    # Check if the PID file exists
    if os.path.exists(PID_FILE):
        # Read the PID and try to terminate the process
        with open(PID_FILE, "r") as f:
            pid = int(f.read().strip())
            try:
                os.kill(pid, signal.SIGTERM)  # Try to terminate gracefully
                os.remove(PID_FILE)  # Clean up PID file
            except ProcessLookupError:
                # Process doesn't exist, remove stale PID file
                os.remove(PID_FILE)

class bluetooth(Sensor, Reconfigurable):
    MODEL: ClassVar[Model] = Model(ModelFamily("mcvella", "presence"), "bluetooth")
    
    advertisement_name: str
    advertisement = None
    agent = None
    discovery_active = False
    manager = None
    bus = None
    pairing_accept_timeout = int

    # Constructor
    @classmethod
    def new(cls, config: ComponentConfig, dependencies: Mapping[ResourceName, ResourceBase]) -> Self:
        restart_bluetooth_without_a2dp()
        my_class = cls(config.name)
        my_class.reconfigure(config, dependencies)
        return my_class

    # Validates JSON Configuration
    @classmethod
    def validate(cls, config: ComponentConfig):
        return

    # Handles attribute reconfiguration
    def reconfigure(self, config: ComponentConfig, dependencies: Mapping[ResourceName, ResourceBase]):
        if self.manager:
            self.manager.running = False
            self.manager.stop()

        self.advertisement_name = config.attributes.fields["advertisement_name"].string_value or "Viam Presence"
        self.pairing_accept_timeout = int(config.attributes.fields["pairing_accept_timeout"].number_value) or 60

        try:
            asyncio.ensure_future(self.start_btmanager())
        except Exception as e:
            LOGGER.error(f"Error initializing or running BluetoothManager: {e}")
        finally:
            if self.manager:
                self.manager.stop()
        return
    
    async def close(self):
        self.manager.stop()
        return await super().close()
    
    async def start_btmanager(self):
        self.manager = BluetoothManager(auto_accept=False, custom_name=self.advertisement_name, pairing_accept_timeout=self.pairing_accept_timeout)
        self.bus = dbus.SystemBus()
        await self.manager.start()

    async def get_readings(
        self, *, extra: Optional[Mapping[str, Any]] = None, timeout: Optional[float] = None, **kwargs
    ) -> Mapping[str, SensorReading]:
        ret = { 
            "present_devices": self.manager.present_devices,
            "known_devices": self.manager.paired_devices,
            "pairing_requests": self.manager.current_pairing_requests()
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
        if 'command' in command:
            if command['command'] == 'accept_pairing_request':
                self.manager.accept_pairing_request(command["device"])
            if command['command'] == 'forget_device':
                self.manager.forget_device(command["device"])  
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
    def __init__(self, auto_accept=False, custom_name="Viam Presence", pairing_accept_timeout=60):
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
        # we could make this configurable but it should be stable here
        self.db_conn = sqlite3.connect( str(Path.home()) + '/.viam/paired_devices.db')        
        self.create_db_table()
        self.advertisement = None
        self.agent = None
        self.auto_accept = auto_accept
        self.discovery_active = False
        self.custom_name = custom_name
        self.pairing_accept_timeout = pairing_accept_timeout

        self.bus.add_signal_receiver(
                    self.properties_changed,
                    dbus_interface="org.freedesktop.DBus.Properties",
                    signal_name="PropertiesChanged",
                    path_keyword="path"
                )
        
    def properties_changed(self, interface, changed, invalidated, path):
        if interface != DEVICE_IFACE:
            return
        if "Connected" in changed:            
            for i, request in enumerate(self.agent.pairing_requests):
                if path == request["device"]:
                    LOGGER.info("PAIRING")
                    return
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
                self.advertisement = None
                LOGGER.info("Advertisement stopped")
            except dbus.exceptions.DBusException as e:
                LOGGER.error(f"Error unregistering advertisement: {e}")
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
        current_time = time.time()
        for i, request in enumerate(self.agent.pairing_requests):
            if current_time - request["when"] < self.pairing_accept_timeout:
                pairing_requests.append ({
                    'passkey': request["passkey"],
                    'device': str(request["device"]),
                    'when': datetime.datetime.fromtimestamp(request["when"]).isoformat()
                })
            else:
                del self.agent.pairing_requests[i]
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

    def remove_all_physical_pairings(self):
        objects = self.om.GetManagedObjects()
        removed_count = 0
        for path, interfaces in objects.items():
            if DEVICE_IFACE in interfaces:
                if self.remove_physical_pairing(path):
                    removed_count += 1
        LOGGER.info(f"Removed {removed_count} paired devices")
        return removed_count

    def accept_pairing_request(self, device):
        if self.agent:
            paired = False
            for i, request in enumerate(self.agent.pairing_requests):
                if request["device"] == device:
                    del self.agent.pairing_requests[i]
                    self.add_paired_device(device)
                    self.remove_all_physical_pairings()
                    paired = True
            if not paired:
                LOGGER.warning(f"No pairing request found for device: {device}")
        else:
            LOGGER.error("Agent not initialized")

    def forget_device(self, device):
        if self.agent:
            forgot = False
            if device in self.paired_devices:
                self.remove_device_from_db(device)
                del self.paired_devices[device]
                LOGGER.info(f"Known device forgotten: {device}")
            else:
                LOGGER.warning(f"Known device not found: {device}")
        else:
            LOGGER.error("Agent not initialized")    

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

        self.present_devices[device_id] = {
            'address': address,
            'name': name,
            'uuid': device_uuid,
            'when': time.time()
        }       

    def add_paired_device(self, device_path):
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

        self.paired_devices[device_id] = {
            'address': address,
            'name': name,
            'uuid': device_uuid
        }
        self.update_device_in_db(device_id, address, name, device_uuid)
        LOGGER.info(f"Added paired device to database: {name} ({address})")


    async def start(self):
        LOGGER.info("Starting Bluetooth Manager...")

        self.adapter_props.Set(ADAPTER_IFACE, "Powered", dbus.Boolean(True))
        self.adapter_props.Set(ADAPTER_IFACE, "Discoverable", dbus.Boolean(True))
        self.adapter_props.Set(ADAPTER_IFACE, "DiscoverableTimeout", dbus.UInt32(0))
        self.adapter_props.Set(ADAPTER_IFACE, "Pairable", dbus.Boolean(True))

        # Set the custom name for the adapter
        self.adapter_props.Set(ADAPTER_IFACE, "Alias", self.custom_name)

        self.start_advertising()

        self.agent = Agent(self.bus, "/test/agent", self.auto_accept)
        self.agent.manager = self 
        self.agent_manager.RegisterAgent(self.agent.get_path(), "KeyboardDisplay")
        self.agent_manager.RequestDefaultAgent(self.agent.get_path())

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
        self.stop_advertising()

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
                if not self.is_device_present(address):
                    LOGGER.debug(f"Attempting to automatically connect to known device: {name} ({address})")
                    self.auto_connect_device(address)

        # update present device list, removing devices not seen recently
        updated_present_devices = {}
        # Check for devices that are no longer present
        for device_id, device_info in list(self.paired_devices.items()):
            if device_id in self.present_devices:
                # TODO - make this interval adjustable
                if time.time() - self.present_devices[device_id]["when"] < 15:
                    updated_present_devices[device_id] = self.present_devices[device_id]
        self.present_devices = updated_present_devices

    def auto_connect_device(self, address):
        try:
            device_path = self.find_device_by_address(address)
            if device_path:
                device = dbus.Interface(self.bus.get_object(BLUEZ_SERVICE_NAME, device_path), DBUS_PROP_IFACE)
                props = device.GetAll(DEVICE_IFACE)

                if not props.get("Connected", False):
                    connect_method = dbus.Interface(self.bus.get_object(BLUEZ_SERVICE_NAME, device_path), DEVICE_IFACE)
                    connect_method.Connect()
                    LOGGER.info(f"Successfully initiated connection to device: {address}")
                else:
                    LOGGER.debug(f"Device {address} is already connected")
                    return True
            else:
                LOGGER.debug(f"Device {address} not found for auto-connection")
        except dbus.exceptions.DBusException as e:
            LOGGER.debug(f"Error auto-connecting to device {address}: {e}")
        return False

    def is_device_present(self, address):
        try:
            device_path = self.find_device_by_address(address)
            if device_path:
                device = dbus.Interface(self.bus.get_object(BLUEZ_SERVICE_NAME, device_path), DBUS_PROP_IFACE)
                props = device.GetAll(DEVICE_IFACE)

                connected = props.get("Connected", False)
                rssi = props.get("RSSI")
                timestamp = props.get("Timestamp")

                LOGGER.debug(f"Device {address} present check: Connected={connected}, RSSI={rssi}, Timestamp available: {timestamp is not None}")

                # Consider the device present if it's connected or has a valid RSSI
                is_present = connected or (rssi is not None and rssi <= 0)

                if timestamp is not None:
                    current_time = int(time.time() * 1000)  # Convert to milliseconds
                    time_difference = current_time - int(timestamp)
                    LOGGER.debug(f"Device {address} last seen {time_difference} ms ago")
                    is_present = is_present and time_difference < 30000

                return is_present
            else:
                LOGGER.debug(f"Device {address} not found in object manager")
                return False
        except dbus.exceptions.DBusException as e:
            LOGGER.error(f"Error checking device presence for {address}: {e}")
            return False

    def is_known_device(self, device_id, address, name, device_uuid):
        if device_id in self.paired_devices:
            LOGGER.debug(f"Device {name} ({address}) found in paired_devices by ID")
            return True
    
        for stored_id, stored_info in self.paired_devices.items():
            if (stored_info['address'] == address or 
                (name != "<unknown>" and stored_info['name'] == name) or 
                (device_uuid and stored_info['uuid'] == device_uuid)):
                LOGGER.debug(f"Device {name} ({address}) matched with stored device {stored_info['name']} ({stored_info['address']})")
                updated_name = name if name != "<unknown>" else f"Unknown Device ({address[-6:]})"
                self.paired_devices[device_id] = {
                    'address': address,
                    'name': updated_name,
                    'uuid': device_uuid
                }
                self.update_device_in_db(device_id, address, updated_name, device_uuid)
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