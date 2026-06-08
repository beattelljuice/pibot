#!/usr/bin/env python3
import signal
import sys
from gpio_controller import GPIOController
from motor_manager import MotorManager
from config import Config
from api import create_api


def main():
    """Initialize and run motor control system."""
    print("Initializing GPIO Controller...")
    gpio = GPIOController(mode="BCM")

    print("Loading configuration...")
    config = Config("config.json")

    print("Initializing Motor Manager...")
    manager = MotorManager(gpio)

    print("Registering DC motors...")
    for name, pins in config.get_dc_motors().items():
        manager.register_dc_motor(name, pins["enable"], pins["direction"])
        print(f"  Registered: {name}")

    print("Registering stepper motors...")
    for name, pins_config in config.get_stepper_motors().items():
        manager.register_stepper_motor(name, pins_config["pins"])
        print(f"  Registered: {name}")

    print("\nMotor Summary:")
    for name, state in manager.list_motors().items():
        print(f"  {name}: {state['type']}")

    api_config = config.get_api_config()
    print(f"\nStarting API server on {api_config['host']}:{api_config['port']}...")

    app = create_api(manager)

    def cleanup(signum, frame):
        print("\nShutting down...")
        manager.cleanup()
        gpio.cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    try:
        app.run(
            host=api_config["host"],
            port=api_config["port"],
            debug=api_config.get("debug", False),
        )
    finally:
        cleanup(None, None)


if __name__ == "__main__":
    main()
