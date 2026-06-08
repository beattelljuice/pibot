#!/usr/bin/env python3
import signal
import sys
from datetime import datetime

from action_executor import ActionExecutor
from api import create_api
from config import Config
from gpio_controller import GPIOController
from motor_manager import MotorManager
from robot_state import RobotState


def main_log(msg: str) -> None:
    """Log main application messages with timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{timestamp}] [MAIN] {msg}")


def main() -> None:
    """Initialize and run motor control system."""
    gpio = None
    manager = None
    executor = None
    robot_state = None
    cleanup_started = False

    def cleanup(signum=None, frame=None, exit_process: bool = False) -> None:
        nonlocal cleanup_started
        if cleanup_started:
            return

        cleanup_started = True
        main_log("")
        main_log("=" * 60)
        main_log("SHUTTING DOWN")
        main_log("=" * 60)

        if executor:
            main_log("Stopping active actions and motors...")
            try:
                executor.stop_all()
            except Exception as e:
                main_log(f"Action executor cleanup error: {e}")

        if manager:
            main_log("Cleaning up motor manager...")
            try:
                manager.cleanup()
            except Exception as e:
                main_log(f"Motor manager cleanup error: {e}")

        if gpio:
            main_log("Cleaning up GPIO controller...")
            try:
                gpio.cleanup()
            except Exception as e:
                main_log(f"GPIO cleanup error: {e}")

        main_log("Cleanup complete")
        main_log("=" * 60)

        if exit_process:
            sys.exit(0)

    def handle_signal(signum, frame) -> None:
        cleanup(signum, frame, exit_process=True)

    main_log("=" * 60)
    main_log("MOTOR CONTROL SYSTEM STARTING")
    main_log("=" * 60)

    try:
        main_log("Initializing GPIO Controller...")
        gpio = GPIOController(mode="BCM")
        main_log("GPIO Controller initialized")

        main_log("Loading configuration from config.json...")
        config = Config("config.json")
        main_log("Configuration loaded")

        main_log("Initializing Motor Manager...")
        manager = MotorManager(gpio)
        main_log("Motor Manager initialized")

        dc_motors = config.get_dc_motors()
        main_log(f"Registering {len(dc_motors)} DC motors...")
        for name, pins in dc_motors.items():
            manager.register_dc_motor(name, pins["pin1"], pins["pin2"])
        main_log(f"{len(dc_motors)} DC motors registered")

        stepper_motors = config.get_stepper_motors()
        main_log(f"Registering {len(stepper_motors)} stepper motors...")
        for name, pins_config in stepper_motors.items():
            manager.register_stepper_motor(name, pins_config["pins"])
        main_log(f"{len(stepper_motors)} stepper motors registered")

        main_log("")
        main_log("=" * 60)
        main_log("MOTOR SUMMARY")
        main_log("=" * 60)
        for name, state in manager.list_motors().items():
            main_log(f"  {name}: {state['type']} motor")

        main_log("Initializing Robot State...")
        robot_state = RobotState()
        main_log("Robot State initialized")

        main_log("Initializing Action Executor...")
        executor = ActionExecutor(manager, robot_state=robot_state)
        main_log("Action Executor initialized")

        api_config = config.get_api_config()
        main_log("")
        main_log("=" * 60)
        main_log("STARTING API SERVER")
        main_log("=" * 60)
        main_log(f"Host: {api_config['host']}")
        main_log(f"Port: {api_config['port']}")
        main_log(f"Debug: {api_config.get('debug', False)}")
        main_log("Open browser to http://localhost:5000")
        main_log("=" * 60)
        main_log("")

        app = create_api(manager, executor, robot_state)

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

        main_log("Starting Flask development server...")
        app.run(
            host=api_config["host"],
            port=api_config["port"],
            debug=api_config.get("debug", False),
            use_reloader=False,
        )
    except Exception as e:
        main_log(f"FATAL ERROR: {e}")
        main_log("Traceback:")
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        cleanup()


if __name__ == "__main__":
    main()
