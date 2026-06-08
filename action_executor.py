from datetime import datetime
from threading import RLock, Timer
from typing import Any, Dict, Optional
import time

from motor import DCMotor, StepperMotor
from motor_manager import MotorManager
from robot_state import RobotState


class ActionExecutorError(ValueError):
    """Raised when an action cannot be executed."""


def executor_log(msg: str) -> None:
    """Log action executor messages with timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{timestamp}] [EXECUTOR] {msg}")


class ActionExecutor:
    """Executes bounded robot actions through MotorManager."""

    def __init__(
        self,
        motor_manager: MotorManager,
        left_motor_name: str = "left_motor",
        right_motor_name: str = "right_motor",
        robot_state: Optional[RobotState] = None,
    ):
        self.motor_manager = motor_manager
        self.left_motor_name = left_motor_name
        self.right_motor_name = right_motor_name
        self.robot_state = robot_state
        self._lock = RLock()
        self._timer: Optional[Timer] = None
        self._active_action: Optional[Dict[str, Any]] = None
        self._next_action_id = 1
        executor_log(
            "Initialized with drive motors "
            f"left='{left_motor_name}', right='{right_motor_name}'"
        )

    def stop_all(self, source: str = "executor") -> Dict[str, Any]:
        """Stop every registered motor and clear any active timed action."""
        with self._lock:
            executor_log("stop_all() called")
            self._cancel_timer_locked()
            stopped = []
            for name, motor in list(self.motor_manager.motors.items()):
                executor_log(f"Stopping motor '{name}'")
                motor.stop()
                stopped.append(name)

            self._active_action = None
            result = {
                "status": "success",
                "action": "stop_all",
                "stopped_motors": stopped,
                "active_action": None,
            }
            self._record_action(
                {"type": "stop_all", "stopped_motors": stopped},
                source,
                "completed",
            )
            return result

    def drive_tank(
        self,
        left_power: int | float,
        right_power: int | float,
        duration_ms: int | float,
        source: str = "executor",
        record: bool = True,
    ) -> Dict[str, Any]:
        """Drive left and right DC motors for a bounded duration."""
        left_motor = self._get_dc_motor(self.left_motor_name)
        right_motor = self._get_dc_motor(self.right_motor_name)
        left_power_int = self._validate_power(left_power, "left_power")
        right_power_int = self._validate_power(right_power, "right_power")
        duration_ms_int = self._validate_duration_ms(duration_ms)

        with self._lock:
            action_id = self._begin_timed_action_locked(
                {
                    "type": "drive_tank",
                    "left_power": left_power_int,
                    "right_power": right_power_int,
                    "duration_ms": duration_ms_int,
                }
            )
            executor_log(
                "Driving tank "
                f"left={left_power_int}% right={right_power_int}% "
                f"duration={duration_ms_int}ms"
            )
            left_motor.set_power(left_power_int)
            right_motor.set_power(right_power_int)
            self._schedule_expiry_locked(action_id, duration_ms_int)

            active_action = self._public_active_action_locked()
            if record:
                self._record_action(active_action or {}, source, "started")
            return {
                "status": "success",
                "action": "drive_tank",
                "active_action": active_action,
            }

    def rotate(
        self,
        power: int | float,
        direction: str,
        duration_ms: int | float,
        source: str = "executor",
    ) -> Dict[str, Any]:
        """Rotate the chassis in place for a bounded duration."""
        power_int = self._validate_positive_power(power)
        direction_normalized = self._validate_rotation_direction(direction)

        if direction_normalized in ("left", "counterclockwise"):
            left_power = -power_int
            right_power = power_int
        else:
            left_power = power_int
            right_power = -power_int

        result = self.drive_tank(
            left_power,
            right_power,
            duration_ms,
            source,
            record=False,
        )
        with self._lock:
            if self._active_action:
                self._active_action["type"] = "rotate"
                self._active_action["power"] = power_int
                self._active_action["direction"] = direction_normalized
                self._active_action["left_power"] = left_power
                self._active_action["right_power"] = right_power
                result["action"] = "rotate"
                result["active_action"] = self._public_active_action_locked()
                self._record_action(result["active_action"], source, "started")
        return result

    def stepper_move(
        self,
        name: str,
        steps: int,
        direction: str = "forward",
        source: str = "executor",
    ) -> Dict[str, Any]:
        """Move a named stepper motor by a bounded number of steps."""
        motor = self._get_stepper_motor(name)
        steps_int = self._validate_steps(steps)
        direction_normalized = self._validate_stepper_direction(direction)

        executor_log(
            f"Moving stepper '{name}' steps={steps_int} direction={direction_normalized}"
        )
        motor.step(steps_int, direction_normalized)
        result = {
            "status": "success",
            "action": "stepper_move",
            "motor": name,
            "steps": steps_int,
            "direction": direction_normalized,
            "active_action": self.get_status()["active_action"],
        }
        self._record_action(
            {
                "type": "stepper_move",
                "motor": name,
                "steps": steps_int,
                "direction": direction_normalized,
            },
            source,
            "started",
        )
        return result

    def get_status(self) -> Dict[str, Any]:
        """Return executor state for API/status views."""
        with self._lock:
            return {
                "status": "success",
                "drive_motors": {
                    "left": self.left_motor_name,
                    "right": self.right_motor_name,
                },
                "active_action": self._public_active_action_locked(),
            }

    def _begin_timed_action_locked(self, action: Dict[str, Any]) -> int:
        self._cancel_timer_locked()
        action_id = self._next_action_id
        self._next_action_id += 1
        now = time.monotonic()
        action["id"] = action_id
        action["started_at"] = datetime.now().isoformat(timespec="milliseconds")
        action["expires_at_monotonic"] = now + (action["duration_ms"] / 1000)
        self._active_action = action
        return action_id

    def _schedule_expiry_locked(self, action_id: int, duration_ms: int) -> None:
        self._timer = Timer(duration_ms / 1000, self._expire_action, args=(action_id,))
        self._timer.daemon = True
        self._timer.start()

    def _expire_action(self, action_id: int) -> None:
        with self._lock:
            if not self._active_action or self._active_action["id"] != action_id:
                return

            executor_log(
                f"Timed action {action_id} expired; stopping drive motors"
            )
            expired_action = self._public_active_action_locked()
            self._stop_drive_motors_locked()
            self._active_action = None
            self._timer = None
            if expired_action:
                self._record_action(expired_action, "executor", "expired")

    def _stop_drive_motors_locked(self) -> None:
        names = {self.left_motor_name, self.right_motor_name}
        for name in names:
            motor = self._get_dc_motor(name)
            motor.stop()

    def _cancel_timer_locked(self) -> None:
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def _record_action(
        self,
        action: Dict[str, Any],
        source: str,
        result: str,
    ) -> None:
        if not self.robot_state:
            return

        try:
            self.robot_state.record_action(action, source=source, result=result)
        except Exception as e:
            executor_log(f"Robot state action record failed: {e}")

    def _public_active_action_locked(self) -> Optional[Dict[str, Any]]:
        if not self._active_action:
            return None

        action = {
            key: value
            for key, value in self._active_action.items()
            if key != "expires_at_monotonic"
        }
        remaining_ms = int(
            max(0, (self._active_action["expires_at_monotonic"] - time.monotonic()) * 1000)
        )
        action["remaining_ms"] = remaining_ms
        return action

    def _get_dc_motor(self, name: str) -> DCMotor:
        motor = self.motor_manager.get_motor(name)
        if not motor:
            raise ActionExecutorError(f"Motor '{name}' not found")
        if not isinstance(motor, DCMotor):
            raise ActionExecutorError(f"Motor '{name}' is not a DC motor")
        return motor

    def _get_stepper_motor(self, name: str) -> StepperMotor:
        motor = self.motor_manager.get_motor(name)
        if not motor:
            raise ActionExecutorError(f"Motor '{name}' not found")
        if not isinstance(motor, StepperMotor):
            raise ActionExecutorError(f"Motor '{name}' is not a stepper motor")
        return motor

    def _validate_power(self, value: int | float, field_name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ActionExecutorError(f"{field_name} must be a number")
        if value < -100 or value > 100:
            raise ActionExecutorError(f"{field_name} must be between -100 and 100")
        return int(value)

    def _validate_positive_power(self, value: int | float) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ActionExecutorError("power must be a number")
        if value <= 0 or value > 100:
            raise ActionExecutorError("power must be between 1 and 100")
        return int(value)

    def _validate_duration_ms(self, value: int | float) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ActionExecutorError("duration_ms must be a number")
        if value <= 0:
            raise ActionExecutorError("duration_ms must be positive")
        return int(value)

    def _validate_steps(self, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ActionExecutorError("steps must be a positive integer")
        if value < 1:
            raise ActionExecutorError("steps must be a positive integer")
        return value

    def _validate_rotation_direction(self, value: str) -> str:
        if not isinstance(value, str):
            raise ActionExecutorError("direction must be a string")
        direction = value.lower()
        allowed = {"left", "right", "clockwise", "counterclockwise"}
        if direction not in allowed:
            raise ActionExecutorError(
                "direction must be 'left', 'right', 'clockwise', or 'counterclockwise'"
            )
        return direction

    def _validate_stepper_direction(self, value: str) -> str:
        if not isinstance(value, str):
            raise ActionExecutorError("direction must be a string")
        direction = value.lower()
        if direction not in {"forward", "backward"}:
            raise ActionExecutorError("direction must be 'forward' or 'backward'")
        return direction
