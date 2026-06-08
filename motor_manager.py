from typing import Dict, Optional
from motor import DCMotor, StepperMotor
from datetime import datetime


def log(msg: str):
    """Log message with timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{timestamp}] [MANAGER] {msg}")


class MotorManager:
    """Centralized registry for managing all motors."""

    def __init__(self, gpio_controller):
        """
        Initialize motor manager.

        Args:
            gpio_controller: GPIOController instance
        """
        log("MotorManager initialized")
        self.gpio = gpio_controller
        self.motors: Dict[str, DCMotor | StepperMotor] = {}

    def register_dc_motor(self, name: str, pin1: int, pin2: int) -> DCMotor:
        """Register a new DC motor (L298N H-bridge with 2 pins)."""
        log(f"Registering DC motor '{name}' on pins pin1={pin1}, pin2={pin2}")
        motor = DCMotor(self.gpio, pin1, pin2, name)
        self.motors[name] = motor
        log(f"✓ DC motor '{name}' registered successfully")
        return motor

    def register_stepper_motor(self, name: str, pins: list) -> StepperMotor:
        """Register a new stepper motor."""
        log(f"Registering stepper motor '{name}' on pins {pins}")
        if len(pins) != 4:
            log(f"✗ ERROR: Stepper motor requires exactly 4 pins, got {len(pins)}")
            raise ValueError("Stepper motor requires exactly 4 pins")
        motor = StepperMotor(self.gpio, pins, name)
        self.motors[name] = motor
        log(f"✓ Stepper motor '{name}' registered successfully")
        return motor

    def get_motor(self, name: str) -> Optional[DCMotor | StepperMotor]:
        """Get motor by name."""
        motor = self.motors.get(name)
        if motor:
            log(f"Retrieved motor '{name}'")
        else:
            log(f"✗ Motor '{name}' not found in registry")
        return motor

    def list_motors(self) -> Dict[str, dict]:
        """Get state of all motors."""
        states = {name: motor.get_state() for name, motor in self.motors.items()}
        log(f"Listed {len(self.motors)} motors")
        return states

    def cleanup(self) -> None:
        """Clean up all motors."""
        log(f"Cleaning up {len(self.motors)} motors...")
        for name, motor in self.motors.items():
            log(f"Cleaning up motor '{name}'")
            motor.cleanup()
        self.motors.clear()
        log(f"All motors cleaned up successfully")
