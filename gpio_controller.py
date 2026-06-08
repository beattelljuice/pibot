import RPi.GPIO as GPIO
from typing import Dict, Literal
import atexit
from datetime import datetime


def gpio_log(msg: str):
    """Log GPIO message with timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{timestamp}] [GPIO] {msg}")


class GPIOController:
    """Seamless GPIO pin control for Raspberry Pi 3B+."""

    def __init__(self, mode: Literal["BCM", "BOARD"] = "BCM"):
        """
        Initialize GPIO controller.

        Args:
            mode: Pin numbering mode - "BCM" (GPIO numbers) or "BOARD" (physical pins)
        """
        gpio_log(f"Initializing GPIO Controller in {mode} mode")
        self.mode = mode
        GPIO.setmode(GPIO.BCM if mode == "BCM" else GPIO.BOARD)
        GPIO.setwarnings(False)
        self._pins: Dict[int, str] = {}
        gpio_log(f"✓ GPIO mode set to {mode}")
        atexit.register(self.cleanup)

    def setup_output(self, pin: int, initial_state: bool = False) -> None:
        """Set pin as output with optional initial state."""
        state_name = "HIGH" if initial_state else "LOW"
        gpio_log(f"Setting pin {pin} as OUTPUT, initial state: {state_name}")
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH if initial_state else GPIO.LOW)
        self._pins[pin] = "OUT"

    def setup_input(self, pin: int, pull_up_down: Literal["UP", "DOWN", "NONE"] = "NONE") -> None:
        """Set pin as input with optional pull-up/pull-down."""
        gpio_log(f"Setting pin {pin} as INPUT, pull: {pull_up_down}")
        pud = {"UP": GPIO.PUD_UP, "DOWN": GPIO.PUD_DOWN, "NONE": GPIO.PUD_OFF}[pull_up_down]
        GPIO.setup(pin, GPIO.IN, pull_up_down=pud)
        self._pins[pin] = "IN"

    def write(self, pin: int, state: bool) -> None:
        """Set pin HIGH (True) or LOW (False)."""
        state_name = "HIGH" if state else "LOW"
        gpio_log(f"Writing to pin {pin}: {state_name}")
        GPIO.output(pin, GPIO.HIGH if state else GPIO.LOW)

    def read(self, pin: int) -> bool:
        """Read pin state, returns True for HIGH, False for LOW."""
        state = GPIO.input(pin) == GPIO.HIGH
        state_name = "HIGH" if state else "LOW"
        gpio_log(f"Read pin {pin}: {state_name}")
        return state

    def toggle(self, pin: int) -> None:
        """Toggle pin state."""
        current = self.read(pin)
        new_state = not current
        gpio_log(f"Toggling pin {pin}")
        self.write(pin, new_state)

    def blink(self, pin: int, times: int = 1, delay: float = 0.5) -> None:
        """Blink pin on/off."""
        gpio_log(f"Starting blink on pin {pin}: {times} times, {delay}s delay")
        import time
        for _ in range(times):
            self.write(pin, True)
            time.sleep(delay)
            self.write(pin, False)
            time.sleep(delay)
        gpio_log(f"Blink complete on pin {pin}")

    def cleanup(self) -> None:
        """Clean up GPIO resources."""
        gpio_log(f"Cleaning up GPIO resources ({len(self._pins)} pins)")
        GPIO.cleanup()
        self._pins.clear()
        gpio_log(f"✓ GPIO cleanup complete")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
