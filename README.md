# bluetooth modular resource

This module implements the [rdk sensor API](https://github.com/rdk/sensor-api) in a mcvella:presence:bluetooth model.
With this model, you can...

## Requirements

_Add instructions here for any requirements._

``` bash
```

## Build and run

To use this module, follow the instructions to [add a module from the Viam Registry](https://docs.viam.com/registry/configure/#add-a-modular-resource-from-the-viam-registry) and select the `rdk:sensor:mcvella:presence:bluetooth` model from the [`mcvella:presence:bluetooth` module](https://app.viam.com/module/rdk/mcvella:presence:bluetooth).

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
  TODO: INSERT SAMPLE ATTRIBUTES
}
```

> [!NOTE]  
> For more information, see [Configure a Machine](https://docs.viam.com/manage/configuration/).

### Attributes

The following attributes are available for `rdk:sensor:mcvella:presence:bluetooth` sensors:

| Name | Type | Inclusion | Description |
| ---- | ---- | --------- | ----------- |
| `todo1` | string | **Required** |  TODO |
| `todo2` | string | Optional |  TODO |

### Example configuration

```json
{
  TODO: INSERT SAMPLE CONFIGURATION(S)
}
```

### Next steps

_Add any additional information you want readers to know and direct them towards what to do next with this module._
_For example:_ 

- To test your...
- To write code against your...

## Troubleshooting

_Add troubleshooting notes here._


Audio locking

/lib/systemd/system/bluetooth.service or /etc/systemd/system/dbus-org.bluez.service
edit /lib/systemd/system/bluetooth.service.d/nv-bluetooth-service.conf

ExecStart=/usr/lib/bluetooth/bluetoothd -P a2dp

sudo systemctl daemon-reload
sudo systemctl restart bluetooth