#!/usr/bin/env python3
import signal
import sys
from datetime import datetime

from action_executor import ActionExecutor
from api import create_api
from camera_controller import CameraController
from config import Config
from display_controller import DisplayController
from gpio_controller import GPIOController
from motor_manager import MotorManager
from ollama_client import OllamaClient
from robot_state import RobotState
from safety_supervisor import SafetySupervisor


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
    display = None
    camera = None
    safety = None
    ollama = None
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

        if camera:
            main_log("Releasing camera...")
            try:
                camera.release()
            except Exception as e:
                main_log(f"Camera cleanup error: {e}")

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

        display_config = config.get_display_config()
        main_log("Initializing OLED Display Controller...")
        display = DisplayController(
            enabled=display_config.get("enabled", False),
            driver=display_config.get("driver", "sh1106"),
            width=display_config.get("width", 128),
            height=display_config.get("height", 64),
            port=display_config.get("i2c_port", 1),
            address=display_config.get("i2c_address", "0x3C"),
            rotate=display_config.get("rotate", 0),
        )
        display_status = display.get_status()
        if display_status["available"]:
            main_log("OLED Display Controller initialized")
        else:
            main_log(f"OLED Display unavailable: {display_status['error']}")

        camera_config = config.get_camera_config()
        main_log("Initializing USB Camera Controller...")
        camera = CameraController(
            enabled=camera_config.get("enabled", False),
            device_index=camera_config.get("device_index", 0),
            width=camera_config.get("width", "auto"),
            height=camera_config.get("height", "auto"),
            fps=camera_config.get("fps", "auto"),
            auto_resolution=camera_config.get("auto_resolution", True),
            prefer_max_resolution=camera_config.get("prefer_max_resolution", True),
            jpeg_quality=camera_config.get("jpeg_quality", 85),
            warmup_frames=camera_config.get("warmup_frames", 2),
            stale_after_ms=camera_config.get("stale_after_ms", 2000),
        )
        camera_status = camera.get_status()
        if camera_status["available"]:
            main_log("USB Camera Controller initialized")
        else:
            main_log(f"USB Camera unavailable: {camera_status['error']}")

        main_log("Initializing Action Executor...")
        executor = ActionExecutor(manager, robot_state=robot_state)
        main_log("Action Executor initialized")

        safety_config = config.get_safety_config()
        main_log("Initializing Safety Supervisor...")
        safety = SafetySupervisor(
            robot_state=robot_state,
            action_executor=executor,
            display_controller=display,
            camera_controller=camera,
            manual_enforcement=safety_config.get("manual_enforcement", False),
            obstacle_enforcement=safety_config.get("obstacle_enforcement", False),
            max_drive_power=safety_config.get("max_drive_power", 100),
            max_action_ms=safety_config.get("max_action_ms", 1500),
            max_stepper_steps=safety_config.get("max_stepper_steps", 500),
        )
        main_log("Safety Supervisor initialized")

        ollama_config = config.get_ollama_config()
        main_log("Initializing Ollama Client...")
        ollama = OllamaClient(
            enabled=ollama_config.get("enabled", False),
            url=ollama_config.get("url", "http://localhost:11434"),
            model=ollama_config.get("model", "llava:latest"),
            timeout_ms=ollama_config.get("timeout_ms", 1500),
            include_camera=ollama_config.get("include_camera", False),
            execute_actions=ollama_config.get("execute_actions", False),
            robot_state=robot_state,
        )
        if ollama.enabled:
            main_log(f"Ollama Client configured for {ollama.model} at {ollama.url}")
        else:
            main_log("Ollama Client disabled")

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

        app = create_api(manager, executor, robot_state, display, camera, safety, ollama)

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
