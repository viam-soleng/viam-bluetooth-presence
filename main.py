import asyncio
import sys

from viam.module.module import Module
from viam.components.sensor import Sensor
from src.bluetooth import bluetooth
from viam.resource.registry import Registry, ResourceCreatorRegistration

from src.bluetooth import bluetooth


async def main():
    """This function creates and starts a new module, after adding all desired resources.
    Resources must be pre-registered. For an example, see the `__init__.py` file.
    """
    Registry.register_resource_creator(Sensor.SUBTYPE, bluetooth.MODEL, ResourceCreatorRegistration(bluetooth.new, bluetooth.validate))

    module = Module.from_args()
    module.add_model_from_registry(Sensor.SUBTYPE, bluetooth.MODEL)
    await module.start()

if __name__ == "__main__":
    asyncio.run(main())
