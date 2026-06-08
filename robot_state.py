from collections import deque
from copy import deepcopy
from datetime import datetime
from threading import RLock
from typing import Any, Dict, Optional
import time


VALID_MODES = {"manual", "ai", "paused", "estop"}


class RobotStateError(ValueError):
    """Raised when robot state cannot be updated."""


def state_log(msg: str) -> None:
    """Log robot state messages with timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{timestamp}] [STATE] {msg}")


def utcish_now() -> str:
    """Return a stable timestamp string without adding timezone dependencies."""
    return datetime.now().isoformat(timespec="milliseconds")


class RobotState:
    """Thread-safe state store for robot mode, goals, sensors, and recent events."""

    def __init__(self, recent_action_limit: int = 20):
        self._lock = RLock()
        self._mode = "manual"
        self._operator_goal = ""
        self._emergency_stop = False
        self._emergency_stop_reason = ""
        self._sensors: Dict[str, Dict[str, Any]] = {}
        self._last_action: Optional[Dict[str, Any]] = None
        self._last_ai_response: Optional[Dict[str, Any]] = None
        self._recent_actions = deque(maxlen=recent_action_limit)
        self._next_event_id = 1
        self._created_at = utcish_now()
        self._updated_at = self._created_at
        state_log("RobotState initialized")

    def set_mode(self, mode: str) -> Dict[str, Any]:
        """Set robot control mode."""
        if not isinstance(mode, str):
            raise RobotStateError("mode must be a string")

        mode = mode.lower()
        if mode not in VALID_MODES:
            allowed = ", ".join(sorted(VALID_MODES))
            raise RobotStateError(f"mode must be one of: {allowed}")

        with self._lock:
            if self._emergency_stop and mode != "estop":
                raise RobotStateError("clear emergency stop before changing mode")

            self._mode = mode
            if mode == "estop":
                self._emergency_stop = True
                if not self._emergency_stop_reason:
                    self._emergency_stop_reason = "mode set to estop"
            self._touch_locked()
            state_log(f"Mode set to {mode}")
            return self.get_status()

    def set_goal(self, goal: str) -> Dict[str, Any]:
        """Set the current operator goal for future AI decisions."""
        if not isinstance(goal, str):
            raise RobotStateError("goal must be a string")

        with self._lock:
            self._operator_goal = goal.strip()
            self._touch_locked()
            state_log(f"Operator goal updated: {self._operator_goal}")
            return {"status": "success", "goal": self._operator_goal}

    def set_emergency_stop(
        self,
        active: bool,
        reason: str = "",
    ) -> Dict[str, Any]:
        """Set or clear emergency stop state."""
        if not isinstance(active, bool):
            raise RobotStateError("active must be a boolean")
        if not isinstance(reason, str):
            raise RobotStateError("reason must be a string")

        with self._lock:
            self._emergency_stop = active
            if active:
                self._mode = "estop"
                self._emergency_stop_reason = reason.strip() or "manual emergency stop"
                state_log(f"Emergency stop active: {self._emergency_stop_reason}")
            else:
                self._emergency_stop_reason = ""
                if self._mode == "estop":
                    self._mode = "paused"
                state_log("Emergency stop cleared")
            self._touch_locked()
            return self.get_status()

    def update_sensor(
        self,
        name: str,
        value: Any,
        stale_after_ms: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Store the latest reading for a sensor."""
        if not isinstance(name, str) or not name.strip():
            raise RobotStateError("sensor name must be a non-empty string")
        if stale_after_ms is not None:
            if isinstance(stale_after_ms, bool) or not isinstance(stale_after_ms, int):
                raise RobotStateError("stale_after_ms must be an integer")
            if stale_after_ms < 1:
                raise RobotStateError("stale_after_ms must be positive")
        if metadata is not None and not isinstance(metadata, dict):
            raise RobotStateError("metadata must be a JSON object")

        with self._lock:
            sensor_name = name.strip()
            self._sensors[sensor_name] = {
                "value": value,
                "updated_at": utcish_now(),
                "updated_at_monotonic": time.monotonic(),
                "stale_after_ms": stale_after_ms,
                "metadata": metadata or {},
            }
            self._touch_locked()
            state_log(f"Sensor '{sensor_name}' updated")
            return {
                "status": "success",
                "sensor": sensor_name,
                "reading": self._public_sensor_locked(sensor_name),
            }

    def clear_sensor(self, name: str) -> Dict[str, Any]:
        """Remove one sensor reading."""
        if not isinstance(name, str) or not name.strip():
            raise RobotStateError("sensor name must be a non-empty string")

        with self._lock:
            sensor_name = name.strip()
            self._sensors.pop(sensor_name, None)
            self._touch_locked()
            state_log(f"Sensor '{sensor_name}' cleared")
            return {"status": "success", "sensor": sensor_name, "cleared": True}

    def record_action(
        self,
        action: Dict[str, Any],
        source: str = "system",
        result: str = "completed",
    ) -> Dict[str, Any]:
        """Record a robot action or rejected action proposal."""
        if not isinstance(action, dict):
            raise RobotStateError("action must be a JSON object")
        if not isinstance(source, str) or not source.strip():
            raise RobotStateError("source must be a non-empty string")
        if not isinstance(result, str) or not result.strip():
            raise RobotStateError("result must be a non-empty string")

        with self._lock:
            record = {
                "id": self._next_event_id,
                "source": source.strip(),
                "result": result.strip(),
                "action": deepcopy(action),
                "timestamp": utcish_now(),
            }
            self._next_event_id += 1
            self._last_action = record
            self._recent_actions.append(record)
            self._touch_locked()
            state_log(
                f"Action recorded source={record['source']} result={record['result']}"
            )
            return deepcopy(record)

    def record_ai_response(
        self,
        response: Optional[Dict[str, Any]],
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Store the latest AI response or AI error."""
        if response is not None and not isinstance(response, dict):
            raise RobotStateError("response must be a JSON object")
        if error is not None and not isinstance(error, str):
            raise RobotStateError("error must be a string")

        with self._lock:
            self._last_ai_response = {
                "response": deepcopy(response),
                "error": error,
                "timestamp": utcish_now(),
            }
            self._touch_locked()
            state_log("AI response recorded")
            return deepcopy(self._last_ai_response)

    def get_status(self) -> Dict[str, Any]:
        """Return compact state status."""
        with self._lock:
            return {
                "status": "success",
                "mode": self._mode,
                "goal": self._operator_goal,
                "emergency_stop": self._emergency_stop,
                "emergency_stop_reason": self._emergency_stop_reason,
                "updated_at": self._updated_at,
            }

    def snapshot(
        self,
        motor_states: Optional[Dict[str, Any]] = None,
        action_status: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Return the structured state snapshot future AI loops will consume."""
        with self._lock:
            return {
                "status": "success",
                "robot": {
                    "mode": self._mode,
                    "operator_goal": self._operator_goal,
                    "emergency_stop": self._emergency_stop,
                    "emergency_stop_reason": self._emergency_stop_reason,
                    "motors": deepcopy(motor_states or {}),
                    "executor": deepcopy(action_status or {}),
                },
                "sensors": {
                    name: self._public_sensor_locked(name)
                    for name in sorted(self._sensors)
                },
                "memory": {
                    "last_action": deepcopy(self._last_action),
                    "recent_actions": list(deepcopy(self._recent_actions)),
                    "last_ai_response": deepcopy(self._last_ai_response),
                },
                "timestamps": {
                    "created_at": self._created_at,
                    "updated_at": self._updated_at,
                },
            }

    def _public_sensor_locked(self, name: str) -> Dict[str, Any]:
        sensor = self._sensors[name]
        age_ms = int((time.monotonic() - sensor["updated_at_monotonic"]) * 1000)
        stale_after_ms = sensor["stale_after_ms"]
        stale = stale_after_ms is not None and age_ms > stale_after_ms
        return {
            "value": deepcopy(sensor["value"]),
            "updated_at": sensor["updated_at"],
            "age_ms": age_ms,
            "stale_after_ms": stale_after_ms,
            "stale": stale,
            "metadata": deepcopy(sensor["metadata"]),
        }

    def _touch_locked(self) -> None:
        self._updated_at = utcish_now()
