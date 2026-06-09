from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Optional

from action_executor import ActionExecutor, ActionExecutorError
from camera_controller import CameraController, CameraControllerError
from display_controller import DisplayController, DisplayControllerError
from robot_state import RobotState


MOVEMENT_ACTIONS = {"drive_tank", "rotate", "stepper_move"}
STOP_ACTIONS = {"stop", "stop_all"}
SAFE_ACTIONS = {"display_text", "display_frame", "camera_capture"}
ALLOWED_ACTIONS = MOVEMENT_ACTIONS | STOP_ACTIONS | SAFE_ACTIONS


class SafetySupervisorError(ValueError):
    """Raised when a supervised action request is malformed."""


def safety_log(msg: str) -> None:
    """Log safety supervisor messages with timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{timestamp}] [SAFETY] {msg}")


class SafetySupervisor:
    """Validate and execute proposed robot actions."""

    def __init__(
        self,
        robot_state: RobotState,
        action_executor: ActionExecutor,
        display_controller: Optional[DisplayController] = None,
        camera_controller: Optional[CameraController] = None,
        manual_enforcement: bool = False,
        obstacle_enforcement: bool = False,
        max_drive_power: int = 100,
        max_action_ms: int = 1500,
        max_stepper_steps: int = 500,
    ):
        self.robot_state = robot_state
        self.action_executor = action_executor
        self.display_controller = display_controller or DisplayController(enabled=False)
        self.camera_controller = camera_controller or CameraController(enabled=False)
        self.manual_enforcement = bool(manual_enforcement)
        self.obstacle_enforcement = bool(obstacle_enforcement)
        self.max_drive_power = max(1, min(100, int(max_drive_power)))
        self.max_action_ms = max(1, int(max_action_ms))
        self.max_stepper_steps = max(1, int(max_stepper_steps))
        self._last_decision: Optional[Dict[str, Any]] = None
        safety_log(
            "Initialized "
            f"max_drive_power={self.max_drive_power} "
            f"max_action_ms={self.max_action_ms} "
            f"max_stepper_steps={self.max_stepper_steps}"
        )

    def get_status(self) -> Dict[str, Any]:
        """Return safety configuration and latest decision."""
        state = self.robot_state.get_status()
        return {
            "status": "success",
            "mode": state["mode"],
            "emergency_stop": state["emergency_stop"],
            "limits": {
                "manual_enforcement": self.manual_enforcement,
                "obstacle_enforcement": self.obstacle_enforcement,
                "max_drive_power": self.max_drive_power,
                "max_action_ms": self.max_action_ms,
                "max_stepper_steps": self.max_stepper_steps,
            },
            "allowed_actions": sorted(ALLOWED_ACTIONS),
            "last_decision": deepcopy(self._last_decision),
        }

    def propose(
        self,
        actions: Any,
        source: str = "ai",
    ) -> Dict[str, Any]:
        """Validate and execute a list of proposed actions."""
        source = self._validate_source(source)
        if isinstance(actions, dict):
            actions = [actions]
        if not isinstance(actions, list):
            raise SafetySupervisorError("actions must be a list")

        results = []
        for action in actions:
            results.append(self._handle_action(action, source))

        return {
            "status": "success",
            "source": source,
            "results": results,
            "safety": self.get_status(),
        }

    def _handle_action(self, action: Any, source: str) -> Dict[str, Any]:
        if not isinstance(action, dict):
            return self._decision(
                "rejected",
                {"type": "malformed"},
                source,
                "action must be a JSON object",
                executed=False,
            )

        original_action = deepcopy(action)
        action_type = action.get("type")
        if not isinstance(action_type, str) or not action_type:
            return self._decision(
                "rejected",
                original_action,
                source,
                "action.type is required",
                executed=False,
            )

        action_type = action_type.lower()
        action["type"] = action_type
        if action_type not in ALLOWED_ACTIONS:
            return self._decision(
                "rejected",
                original_action,
                source,
                f"unknown action type '{action_type}'",
                executed=False,
            )

        mode_status = self.robot_state.get_status()
        mode = mode_status["mode"]
        if action_type in MOVEMENT_ACTIONS and not self._movement_allowed(source, mode):
            return self._decision(
                "rejected",
                original_action,
                source,
                f"movement is not allowed in mode '{mode}' for source '{source}'",
                executed=False,
            )

        if action_type in STOP_ACTIONS:
            normalized = {"type": "stop_all"}
            normalize_result = {"clamped": False}
        else:
            try:
                normalize_result = self._normalize_action(action)
            except SafetySupervisorError as e:
                return self._decision(
                    "rejected",
                    original_action,
                    source,
                    str(e),
                    executed=False,
                )
            if normalize_result["decision"] == "rejected":
                return self._decision(
                    "rejected",
                    original_action,
                    source,
                    normalize_result["reason"],
                    executed=False,
                )
            normalized = normalize_result["action"]

        try:
            execution_result = self._execute_action(normalized, source)
            decision = (
                "clamped"
                if normalize_result.get("clamped", False) and action_type in MOVEMENT_ACTIONS
                else "approved"
            )
            return self._decision(
                decision,
                normalized,
                source,
                "executed",
                executed=True,
                result=execution_result,
                original_action=original_action,
            )
        except (
            ActionExecutorError,
            DisplayControllerError,
            CameraControllerError,
            SafetySupervisorError,
        ) as e:
            return self._decision(
                "rejected",
                normalized,
                source,
                str(e),
                executed=False,
                original_action=original_action,
            )

    def _normalize_action(self, action: Dict[str, Any]) -> Dict[str, Any]:
        action_type = action["type"]
        if action_type == "drive_tank":
            left_power = self._clamp_power(action.get("left_power"), "left_power")
            right_power = self._clamp_power(action.get("right_power"), "right_power")
            duration_ms = self._clamp_duration(action.get("duration_ms"))
            return {
                "decision": "ok",
                "clamped": (
                    left_power != action.get("left_power")
                    or right_power != action.get("right_power")
                    or duration_ms != action.get("duration_ms")
                ),
                "action": {
                    "type": "drive_tank",
                    "left_power": left_power,
                    "right_power": right_power,
                    "duration_ms": duration_ms,
                },
            }

        if action_type == "rotate":
            direction = action.get("direction")
            if not isinstance(direction, str):
                return {"decision": "rejected", "reason": "direction must be a string"}
            power = self._clamp_positive_power(action.get("power"))
            duration_ms = self._clamp_duration(action.get("duration_ms"))
            return {
                "decision": "ok",
                "clamped": (
                    power != action.get("power")
                    or duration_ms != action.get("duration_ms")
                ),
                "action": {
                    "type": "rotate",
                    "power": power,
                    "direction": direction.lower(),
                    "duration_ms": duration_ms,
                },
            }

        if action_type == "stepper_move":
            motor = action.get("motor") or action.get("name")
            direction = action.get("direction", "forward")
            if not isinstance(motor, str) or not motor:
                return {"decision": "rejected", "reason": "motor is required"}
            if not isinstance(direction, str):
                return {"decision": "rejected", "reason": "direction must be a string"}
            steps = self._clamp_steps(action.get("steps"))
            return {
                "decision": "ok",
                "clamped": steps != action.get("steps"),
                "action": {
                    "type": "stepper_move",
                    "motor": motor,
                    "steps": steps,
                    "direction": direction.lower(),
                },
            }

        if action_type == "display_text":
            return {
                "decision": "ok",
                "clamped": False,
                "action": {
                    "type": "display_text",
                    "text": str(action.get("text", "")),
                    "x": self._optional_int(action.get("x", 0), "x"),
                    "y": self._optional_int(action.get("y", 0), "y"),
                    "clear": bool(action.get("clear", True)),
                },
            }

        if action_type == "display_frame":
            payload = {key: value for key, value in action.items() if key != "type"}
            return {
                "decision": "ok",
                "clamped": False,
                "action": {"type": "display_frame", **payload},
            }

        if action_type == "camera_capture":
            return {
                "decision": "ok",
                "clamped": False,
                "action": {"type": "camera_capture"},
            }

        return {"decision": "rejected", "reason": f"unsupported action '{action_type}'"}

    def _execute_action(self, action: Dict[str, Any], source: str) -> Dict[str, Any]:
        action_type = action["type"]
        if action_type == "stop_all":
            return self.action_executor.stop_all(source=source)
        if action_type == "drive_tank":
            return self.action_executor.drive_tank(
                action["left_power"],
                action["right_power"],
                action["duration_ms"],
                source=source,
            )
        if action_type == "rotate":
            return self.action_executor.rotate(
                action["power"],
                action["direction"],
                action["duration_ms"],
                source=source,
            )
        if action_type == "stepper_move":
            return self.action_executor.stepper_move(
                action["motor"],
                action["steps"],
                action["direction"],
                source=source,
            )
        if action_type == "display_text":
            return self.display_controller.display_text(
                action["text"],
                action["x"],
                action["y"],
                action["clear"],
            )
        if action_type == "display_frame":
            payload = {key: value for key, value in action.items() if key != "type"}
            return self.display_controller.display_frame(payload)
        if action_type == "camera_capture":
            jpeg_bytes, meta = self.camera_controller.capture_jpeg()
            self._record_camera_sensor(meta)
            return {
                "status": "success",
                "action": "camera_capture",
                "frame": {**meta, "encoding": "jpeg", "bytes": len(jpeg_bytes)},
                "camera": self.camera_controller.get_status(),
            }
        raise SafetySupervisorError(f"cannot execute action '{action_type}'")

    def _record_camera_sensor(self, meta: Dict[str, Any]) -> None:
        try:
            self.robot_state.update_sensor(
                "camera_snapshot",
                {
                    "captured_at": meta["captured_at"],
                    "width": meta["width"],
                    "height": meta["height"],
                    "mime": meta["mime"],
                    "bytes": meta["bytes"],
                    "snapshot_url": "/camera/snapshot.jpg",
                },
                self.camera_controller.stale_after_ms,
            )
        except Exception as e:
            safety_log(f"Camera sensor state update failed: {e}")

    def _movement_allowed(self, source: str, mode: str) -> bool:
        if mode == "estop":
            return False
        if source == "ai":
            return mode == "ai"
        if self.manual_enforcement:
            return mode in {"manual", "ai"}
        return True

    def _decision(
        self,
        decision: str,
        action: Dict[str, Any],
        source: str,
        reason: str,
        executed: bool,
        result: Optional[Dict[str, Any]] = None,
        original_action: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        record = {
            "decision": decision,
            "source": source,
            "action": deepcopy(action),
            "reason": reason,
            "executed": executed,
            "result": deepcopy(result),
            "original_action": deepcopy(original_action),
            "timestamp": datetime.now().isoformat(timespec="milliseconds"),
        }
        self._last_decision = record
        try:
            self.robot_state.record_action(
                {
                    "type": "safety_decision",
                    "decision": decision,
                    "source": source,
                    "action": deepcopy(action),
                    "reason": reason,
                    "executed": executed,
                },
                source="safety",
                result=decision,
            )
        except Exception as e:
            safety_log(f"Robot state safety record failed: {e}")
        return record

    def _validate_source(self, source: Any) -> str:
        if not isinstance(source, str) or not source.strip():
            raise SafetySupervisorError("source must be a non-empty string")
        return source.strip().lower()

    def _clamp_power(self, value: Any, field_name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SafetySupervisorError(f"{field_name} must be a number")
        return int(max(-self.max_drive_power, min(self.max_drive_power, value)))

    def _clamp_positive_power(self, value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SafetySupervisorError("power must be a number")
        return int(max(1, min(self.max_drive_power, value)))

    def _clamp_duration(self, value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SafetySupervisorError("duration_ms must be a number")
        return int(max(1, min(self.max_action_ms, value)))

    def _clamp_steps(self, value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise SafetySupervisorError("steps must be an integer")
        return int(max(1, min(self.max_stepper_steps, value)))

    def _optional_int(self, value: Any, field_name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SafetySupervisorError(f"{field_name} must be a number")
        return int(value)
