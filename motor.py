import RPi.GPIO as GPIO
from typing import Literal
from threading import Thread, Event
import time


class DCMotor:
    """DC motor with PWM speed control and direction."""

    def __init__(self, gpio_controller, enable_pin: int, direction_pin: int, name: str = "DC"):
        """
        Initialize DC motor.

        Args:
            gpio_controller: GPIOController instance
            enable_pin: PWM pin for speed control
            direction_pin: Pin for direction control (HIGH=forward, LOW=backward)
            name: Motor identifier
        """
        self.gpio = gpio_controller
        self.enable_pin = enable_pin
        self.direction_pin = direction_pin
        self.name = name

        self.gpio.setup_output(enable_pin)
        self.gpio.setup_output(direction_pin)

        self.pwm = GPIO.PWM(enable_pin, 1000)
        self.pwm.start(0)

        self.speed = 0
        self.direction = "stopped"

    def set_speed(self, speed: int) -> None:
        """Set motor speed 0-100%."""
        speed = max(0, min(100, speed))
        self.speed = speed
        self.pwm.ChangeDutyCycle(speed)

    def forward(self) -> None:
        """Set motor direction to forward."""
        self.gpio.write(self.direction_pin, True)
        self.direction = "forward"

    def backward(self) -> None:
        """Set motor direction to backward."""
        self.gpio.write(self.direction_pin, False)
        self.direction = "backward"

    def stop(self) -> None:
        """Stop motor."""
        self.set_speed(0)
        self.direction = "stopped"

    def get_state(self) -> dict:
        """Get current motor state."""
        return {
            "name": self.name,
            "type": "DC",
            "speed": self.speed,
            "direction": self.direction,
        }

    def cleanup(self) -> None:
        """Clean up PWM."""
        self.pwm.stop()


class StepperMotor:
    """28BYJ-48 stepper motor with ULN2003 driver (4-pin control)."""

    # Full-step sequences for 4-pin stepper
    SEQUENCES = {
        "forward": [0b1100, 0b0110, 0b0011, 0b1001],
        "backward": [0b1001, 0b0011, 0b0110, 0b1100],
    }

    def __init__(self, gpio_controller, pins: list, name: str = "Stepper"):
        """
        Initialize stepper motor.

        Args:
            gpio_controller: GPIOController instance
            pins: List of 4 GPIO pins [IN1, IN2, IN3, IN4]
            name: Motor identifier
        """
        self.gpio = gpio_controller
        self.pins = pins
        self.name = name

        for pin in pins:
            self.gpio.setup_output(pin)

        self.current_step = 0
        self.stepping = False
        self.stop_event = Event()
        self.step_delay = 0.01
        self._step_thread = None

    def set_speed(self, rpm: float) -> None:
        """
        Set stepper speed. 28BYJ-48 has 2048 steps per revolution.

        Args:
            rpm: Motor speed in revolutions per minute
        """
        if rpm <= 0:
            self.step_delay = float('inf')
        else:
            steps_per_second = (rpm * 2048) / 60
            self.step_delay = 1.0 / steps_per_second

    def step(self, steps: int, direction: Literal["forward", "backward"] = "forward") -> None:
        """
        Move stepper motor (non-blocking).

        Args:
            steps: Number of steps to move
            direction: "forward" or "backward"
        """
        if self.stepping:
            return

        self.stop_event.clear()
        self._step_thread = Thread(
            target=self._step_thread_worker,
            args=(steps, direction),
            daemon=True,
        )
        self._step_thread.start()

    def _step_thread_worker(self, steps: int, direction: str) -> None:
        """Internal worker for stepping."""
        self.stepping = True
        sequences = self.SEQUENCES[direction]

        for _ in range(steps):
            if self.stop_event.is_set():
                break

            sequence = sequences[self.current_step]
            self._write_pins(sequence)
            self.current_step = (self.current_step + 1) % 4

            time.sleep(self.step_delay)

        self.stepping = False

    def _write_pins(self, sequence: int) -> None:
        """Write 4-bit sequence to stepper pins."""
        for i, pin in enumerate(self.pins):
            state = bool(sequence & (1 << (3 - i)))
            self.gpio.write(pin, state)

    def stop(self) -> None:
        """Stop stepper immediately."""
        self.stop_event.set()
        if self._step_thread and self._step_thread.is_alive():
            self._step_thread.join(timeout=1)
        self._de_energize()

    def _de_energize(self) -> None:
        """Turn off all coils."""
        for pin in self.pins:
            self.gpio.write(pin, False)

    def get_state(self) -> dict:
        """Get current stepper state."""
        return {
            "name": self.name,
            "type": "Stepper",
            "stepping": self.stepping,
            "step_delay": self.step_delay,
            "rpm": (60 * 2048 / (self.step_delay * 2048)) if self.step_delay != float('inf') else 0,
        }

    def cleanup(self) -> None:
        """Clean up stepper."""
        self.stop()
