import RPi.GPIO as GPIO
from typing import Literal
from threading import Thread, Event
import time
from datetime import datetime


def log(msg: str, motor_name: str = None):
    """Log message with timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    prefix = f"[{timestamp}]" + (f" [{motor_name}]" if motor_name else "")
    print(f"{prefix} {msg}")


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

        log(f"Initializing DC motor on pins enable={enable_pin}, direction={direction_pin}", self.name)
        self.gpio.setup_output(enable_pin)
        self.gpio.setup_output(direction_pin)

        self.pwm = GPIO.PWM(enable_pin, 1000)
        self.pwm.start(0)
        log(f"PWM started at 1000Hz, initial duty cycle 0%", self.name)

        self.speed = 0
        self.direction = "stopped"
        log(f"Initialized - speed={self.speed}%, direction={self.direction}", self.name)

    def set_speed(self, speed: int) -> None:
        """Set motor speed 0-100%."""
        speed = max(0, min(100, speed))
        log(f"set_speed() called with {speed}%", self.name)
        self.speed = speed
        try:
            self.pwm.ChangeDutyCycle(speed)
            log(f"PWM duty cycle set to {speed}%", self.name)
        except RuntimeError as e:
            log(f"PWM error: {e}, reinitializing...", self.name)
            self.pwm.stop()
            self.pwm = GPIO.PWM(self.enable_pin, 1000)
            self.pwm.start(0)
            self.pwm.ChangeDutyCycle(speed)
            log(f"PWM reinitialized and set to {speed}%", self.name)

    def forward(self) -> None:
        """Set motor direction to forward."""
        log(f"forward() called, pin {self.direction_pin} -> HIGH", self.name)
        self.gpio.write(self.direction_pin, True)
        self.direction = "forward"
        log(f"Direction set to forward", self.name)

    def backward(self) -> None:
        """Set motor direction to backward."""
        log(f"backward() called, pin {self.direction_pin} -> LOW", self.name)
        self.gpio.write(self.direction_pin, False)
        self.direction = "backward"
        log(f"Direction set to backward", self.name)

    def stop(self) -> None:
        """Stop motor."""
        log(f"stop() called, setting speed to 0", self.name)
        self.speed = 0
        self.direction = "stopped"
        try:
            self.pwm.ChangeDutyCycle(0)
            log(f"Motor stopped - speed=0%, direction=stopped", self.name)
        except RuntimeError as e:
            log(f"Error stopping motor: {e}", self.name)

    def get_state(self) -> dict:
        """Get current motor state."""
        state = {
            "name": self.name,
            "type": "DC",
            "speed": self.speed,
            "direction": self.direction,
        }
        log(f"get_state() -> {state}", self.name)
        return state

    def cleanup(self) -> None:
        """Clean up PWM."""
        log(f"cleanup() called, stopping PWM", self.name)
        self.pwm.stop()
        log(f"Motor cleaned up successfully", self.name)


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

        log(f"Initializing stepper motor on pins {pins}", self.name)
        for pin in pins:
            self.gpio.setup_output(pin)

        self.current_step = 0
        self.stepping = False
        self.stop_event = Event()
        self.step_delay = 0.01
        self._step_thread = None
        log(f"Stepper initialized with step_delay={self.step_delay}s", self.name)

    def set_speed(self, rpm: float) -> None:
        """
        Set stepper speed. 28BYJ-48 has 2048 steps per revolution.

        Args:
            rpm: Motor speed in revolutions per minute
        """
        log(f"set_speed() called with {rpm} RPM", self.name)
        if rpm <= 0:
            self.step_delay = float('inf')
            log(f"Speed set to 0 RPM, step_delay=inf", self.name)
        else:
            steps_per_second = (rpm * 2048) / 60
            self.step_delay = 1.0 / steps_per_second
            log(f"Speed set to {rpm} RPM, step_delay={self.step_delay:.6f}s", self.name)

    def step(self, steps: int, direction: Literal["forward", "backward"] = "forward") -> None:
        """
        Move stepper motor (non-blocking).

        Args:
            steps: Number of steps to move
            direction: "forward" or "backward"
        """
        log(f"step() called: steps={steps}, direction={direction}", self.name)
        if self.stepping:
            log(f"Already stepping, ignoring command", self.name)
            return

        log(f"Starting async stepping thread", self.name)
        self.stop_event.clear()
        self._step_thread = Thread(
            target=self._step_thread_worker,
            args=(steps, direction),
            daemon=True,
        )
        self._step_thread.start()

    def _step_thread_worker(self, steps: int, direction: str) -> None:
        """Internal worker for stepping."""
        log(f"Step thread started: {steps} steps {direction}", self.name)
        self.stepping = True
        sequences = self.SEQUENCES[direction]
        stepped = 0

        for i in range(steps):
            if self.stop_event.is_set():
                log(f"Stop event received, exiting step thread after {i} steps", self.name)
                break

            sequence = sequences[self.current_step]
            self._write_pins(sequence)
            self.current_step = (self.current_step + 1) % 4
            stepped += 1

            time.sleep(self.step_delay)

        self.stepping = False
        log(f"Step thread completed: {stepped}/{steps} steps executed", self.name)

    def _write_pins(self, sequence: int) -> None:
        """Write 4-bit sequence to stepper pins."""
        for i, pin in enumerate(self.pins):
            state = bool(sequence & (1 << (3 - i)))
            self.gpio.write(pin, state)

    def stop(self) -> None:
        """Stop stepper immediately."""
        log(f"stop() called", self.name)
        self.stop_event.set()
        if self._step_thread and self._step_thread.is_alive():
            log(f"Waiting for step thread to finish", self.name)
            self._step_thread.join(timeout=1)
        self._de_energize()
        log(f"Stepper stopped and de-energized", self.name)

    def _de_energize(self) -> None:
        """Turn off all coils."""
        log(f"De-energizing coils", self.name)
        for pin in self.pins:
            self.gpio.write(pin, False)

    def get_state(self) -> dict:
        """Get current stepper state."""
        state = {
            "name": self.name,
            "type": "Stepper",
            "stepping": self.stepping,
            "step_delay": self.step_delay,
            "rpm": (60 * 2048 / (self.step_delay * 2048)) if self.step_delay != float('inf') else 0,
        }
        log(f"get_state() -> stepping={state['stepping']}, rpm={state['rpm']:.1f}", self.name)
        return state

    def cleanup(self) -> None:
        """Clean up stepper."""
        log(f"cleanup() called", self.name)
        self.stop()
        log(f"Stepper cleaned up successfully", self.name)
