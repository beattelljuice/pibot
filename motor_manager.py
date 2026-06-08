from typing import Dict, Optional
from motor import DCMotor, StepperMotor


class MotorManager:
    """Centralized registry for managing all motors."""

    def __init__(self, gpio_controller):
        """
        Initialize motor manager.

        Args:
            gpio_controller: GPIOController instance
        """
        self.gpio = gpio_controller
        self.motors: Dict[str, DCMotor | StepperMotor] = {}

    def register_dc_motor(self, name: str, enable_pin: int, direction_pin: int) -> DCMotor:
        """Register a new DC motor."""
        motor = DCMotor(self.gpio, enable_pin, direction_pin, name)
        self.motors[name] = motor
        return motor

    def register_stepper_motor(self, name: str, pins: list) -> StepperMotor:
        """Register a new stepper motor."""
        if len(pins) != 4:
            raise ValueError("Stepper motor requires exactly 4 pins")
        motor = StepperMotor(self.gpio, pins, name)
        self.motors[name] = motor
        return motor

    def get_motor(self, name: str) -> Optional[DCMotor | StepperMotor]:
        """Get motor by name."""
        return self.motors.get(name)

    def list_motors(self) -> Dict[str, dict]:
        """Get state of all motors."""
        return {name: motor.get_state() for name, motor in self.motors.items()}

    def cleanup(self) -> None:
        """Clean up all motors."""
        for motor in self.motors.values():
            motor.cleanup()
        self.motors.clear()
