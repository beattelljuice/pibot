#!/usr/bin/env python3
import signal
import sys
from datetime import datetime
from gpio_controller import GPIOController
from motor_manager import MotorManager
from config import Config
from api import create_api


def main_log(msg: str):
    """Log main application messages with timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{timestamp}] [MAIN] {msg}")


def main():
    """Initialize and run motor control system."""
    main_log("=" * 60)
    main_log("MOTOR CONTROL SYSTEM STARTING")
    main_log("=" * 60)

    try:
        main_log("Initializing GPIO Controller...")
        gpio = GPIOController(mode="BCM")
        main_log("✓ GPIO Controller initialized")

        main_log("Loading configuration from config.json...")
        config = Config("config.json")
        main_log("✓ Configuration loaded")

        main_log("Initializing Motor Manager...")
        manager = MotorManager(gpio)
        main_log("✓ Motor Manager initialized")

        dc_motors = config.get_dc_motors()
        main_log(f"Registering {len(dc_motors)} DC motors...")
        for name, pins in dc_motors.items():
            manager.register_dc_motor(name, pins["pin1"], pins["pin2"])
        main_log(f"✓ {len(dc_motors)} DC motors registered")

        stepper_motors = config.get_stepper_motors()
        main_log(f"Registering {len(stepper_motors)} stepper motors...")
        for name, pins_config in stepper_motors.items():
            manager.register_stepper_motor(name, pins_config["pins"])
        main_log(f"✓ {len(stepper_motors)} stepper motors registered")

        main_log("\n" + "=" * 60)
        main_log("MOTOR SUMMARY")
        main_log("=" * 60)
        for name, state in manager.list_motors().items():
            main_log(f"  {name}: {state['type']} motor")

        api_config = config.get_api_config()
        main_log("\n" + "=" * 60)
        main_log("STARTING API SERVER")
        main_log("=" * 60)
        main_log(f"Host: {api_config['host']}")
        main_log(f"Port: {api_config['port']}")
        main_log(f"Debug: {api_config.get('debug', False)}")
        main_log("Open browser to http://localhost:5000")
        main_log("=" * 60 + "\n")

        app = create_api(manager)

        def cleanup(signum, frame):
            main_log("\n" + "=" * 60)
            main_log("SHUTTING DOWN")
            main_log("=" * 60)
            main_log("Cleaning up GPIO and motors...")
            manager.cleanup()
            main_log("Cleaning up GPIO controller...")
            gpio.cleanup()
            main_log("✓ Cleanup complete")
            main_log("=" * 60)
            sys.exit(0)

        signal.signal(signal.SIGINT, cleanup)
        signal.signal(signal.SIGTERM, cleanup)

        main_log("Starting Flask development server...")
        app.run(
            host=api_config["host"],
            port=api_config["port"],
            debug=api_config.get("debug", False),
            use_reloader=False,
        )
    except Exception as e:
        main_log(f"✗ FATAL ERROR: {e}")
        main_log("Traceback:")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        cleanup(None, None)


if __name__ == "__main__":
    main()
