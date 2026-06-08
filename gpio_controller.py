import RPi.GPIO as GPIO
from typing import Dict, Literal
import atexit


class GPIOController:
    """Seamless GPIO pin control for Raspberry Pi 3B+."""

    def __init__(self, mode: Literal["BCM", "BOARD"] = "BCM"):
        """
        Initialize GPIO controller.

        Args:
            mode: Pin numbering mode - "BCM" (GPIO numbers) or "BOARD" (physical pins)
        """
        self.mode = mode
        GPIO.setmode(GPIO.BCM if mode == "BCM" else GPIO.BOARD)
        GPIO.setwarnings(False)
        self._pins: Dict[int, str] = {}
        atexit.register(self.cleanup)

    def setup_output(self, pin: int, initial_state: bool = False) -> None:
        """Set pin as output with optional initial state."""
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH if initial_state else GPIO.LOW)
        self._pins[pin] = "OUT"

    def setup_input(self, pin: int, pull_up_down: Literal["UP", "DOWN", "NONE"] = "NONE") -> None:
        """Set pin as input with optional pull-up/pull-down."""
        pud = {"UP": GPIO.PUD_UP, "DOWN": GPIO.PUD_DOWN, "NONE": GPIO.PUD_OFF}[pull_up_down]
        GPIO.setup(pin, GPIO.IN, pull_up_down=pud)
        self._pins[pin] = "IN"

    def write(self, pin: int, state: bool) -> None:
        """Set pin HIGH (True) or LOW (False)."""
        GPIO.output(pin, GPIO.HIGH if state else GPIO.LOW)

    def read(self, pin: int) -> bool:
        """Read pin state, returns True for HIGH, False for LOW."""
        return GPIO.input(pin) == GPIO.HIGH

    def toggle(self, pin: int) -> None:
        """Toggle pin state."""
        current = self.read(pin)
        self.write(pin, not current)

    def blink(self, pin: int, times: int = 1, delay: float = 0.5) -> None:
        """Blink pin on/off."""
        import time
        for _ in range(times):
            self.write(pin, True)
            time.sleep(delay)
            self.write(pin, False)
            time.sleep(delay)

    def cleanup(self) -> None:
        """Clean up GPIO resources."""
        GPIO.cleanup()
        self._pins.clear()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
