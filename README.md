# Viam bluetooth presence modular resource

This module implements the [rdk sensor API](https://github.com/rdk/sensor-api) in a mcvella:presence:bluetooth model.
This model:

* Creates a Bluetooth Low Energy advertisement on bluetooth-equipped Linux systems.
* Shows pairing requests, and allows you to accept pairing requests.
* Checks for presence of previously paired devices.

## Requirements

A Linux system running bluetoothd.

*Note* that in order to not "take over" audio from paired devices, bluetoothd must be rub without the "a2dp" plugin.
This module will restart the system bluetoothd on start, passing in the "-P a2dp" flag.
Therefore, it is not recommended that you use this module on a linux system that you are using for other bluetooth functionality.

## Build and run

To use this module, follow the instructions to [add a module from the Viam Registry](https://docs.viam.com/registry/configure/#add-a-modular-resource-from-the-viam-registry) and select the `mcvella:presence:bluetooth` model from the [`mcvella:presence:bluetooth` module](https://app.viam.com/module/rdk/mcvella:presence:bluetooth).

## Configure your sensor

> [!NOTE]  
> Before configuring your sensor, you must [create a machine](https://docs.viam.com/manage/fleet/machines/#add-a-new-machine).

Navigate to the **Config** tab of your machine's page in [the Viam app](https://app.viam.com/).
Click on the **Components** subtab and click **Create component**.
Select the `sensor` type, then select the `mcvella:presence:bluetooth` model.
Click **Add module**, then enter a name for your sensor and click **Create**.

On the new component panel, copy and paste the following attribute template into your sensor’s **Attributes** box:

```json
{
  "advertisement_name" : "<name of advertisement>",
  "pairing_accept_timeout": <timeout_in_secs>,
  "device_present_linger": <timeout_in_secs>
}
```

> [!NOTE]  
> For more information, see [Configure a Machine](https://docs.viam.com/manage/configuration/).

### Attributes

The following attributes are available for `mcvella:presence:bluetooth` sensors:

| Name | Type | Inclusion | Description |
| ---- | ---- | --------- | ----------- |
| `advertisement_name` | string | Optional | The name that the device running this module will advertise itself as.  Default is "Viam Presence"  |
| `pairing_accept_timeout` | integer | Optional |  The duration in seconds for which a pairing request is valid and will show via get_readings. Default is 60. |
| `device_present_linger` | integer | Optional |  The duration in seconds for which a device is considered present after last seen. Default is 30. |

### Example configuration

```json
{
  "advertisement_name" : "My BT detector",
  "pairing_accept_timeout": 60,
  "device_present_linger": 30
}
```

## API

The Bluetooth presence sensor provides the [GetReadings](https://docs.viam.com/components/sensor/#getreadings) and [DoCommand](https://docs.viam.com/components/sensor/#docommand) methods from Viam's built-in [rdk:component:sensor API](https://docs.viam.com/components/sensor/)

### get_readings()

get_readings() will return a dictionary that looks like:

``` JSON
{
  "known_devices": {
      "b55a70ba-6830-5b26-a291-cbabd89d7b6d": {
        "address": "0D:21:6E:1C:72:30",
        "name": "My great phone",
        "uuid": "00000000-dace-dabb-aeaa-aeeadeffaade"
      }
  },
  "pairing_requests": [
    {
      "passkey": "012345",
      "device": "/path/device_id",
      "when": "2024-11-08T14:27:27Z"
    }
  ],
  "present_devices": {
      "b55a70ba-6830-5b26-a291-cbabd89d7b6d": {
        "address": "0D:21:6E:1C:72:30",
        "name": "My great phone",
        "uuid": "00000000-dace-dabb-aeaa-aeeadeffaade"
      }
  }
}
```

*known_devices* is a dictionary of all previously accepted paired devices.
Known devices can be removed with the do_command() *forget_device* command.

*pairing_requests* is a list of current pairing requests.
A pairing request is initiated when someone asks to pair from their bluetooth enabled device (phone, laptop, tablet etc) by choosing the advertisement name broadcast by this module as selected by the config setting *advertisement_name*.
A pairing request will expire after *pairing_accept_timeout* seconds, and can be accepted by calling the do_command() *accept_paring_request* command.

*present_devices* is a dictionary of the *known_devices* that are currently detected as being nearby by this module.
This is tested by attempting to periodically connect to any known devices.
A present device will be considered not present after last connected to it via bluetooth LE for *device_present_linger* seconds.

### do_command(*dictionary*)

In the dictionary passed as a parameter to do_command(), you must specify a *command* by passing a the key *command* with one of the following values.

#### accept_pairing_request

When *accept_pairing_request* is passed as the command, a matching pairing request that has not expired will be accepted and that device will be considered a "known device".
The following are attributes to be passed with *accept_pairing_request*:

| Key | Type | Inclusion | Description |
| ---- | ---- | --------- | ----------- |
| `device` | string | **Required** |  The device path seen as 'device' in an existing pairing request. |

Example:

```python
sms.do_command({"command": "accept_pairing_request", "device": "/your/device/path"})
```

#### forget_device

When *forget_device* is passed as the command, a known device is removed from *known_devices* and will not longer appear as present in *present_devices* unless re-paired.
The following are attributes to be passed with *forget_device*:

| Key | Type | Inclusion | Description |
| ---- | ---- | --------- | ----------- |
| `device` | string | **Required** |  The device id seen as the key in *known_devices*. |

Example:

```python
sms.do_command({"command": "forget_device", "device": "b55a70ba-6830-5b26-a291-cbabd89d7b6d"})
```

## Notes

You shouldn't need to modify your bluetoothd configuration on most systems to run this module, but if you do, it is likely located at:

/lib/systemd/system/bluetooth.service or /etc/systemd/system/dbus-org.bluez.service

However, on some Nvidia systems like the Orin Nano, there is also a config file at:

/lib/systemd/system/bluetooth.service.d/nv-bluetooth-service.conf
