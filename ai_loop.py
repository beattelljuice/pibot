from copy import deepcopy
from datetime import datetime
from threading import Event, RLock, Thread, current_thread
from typing import Any, Callable, Dict, Optional, Tuple
import time

from action_executor import ActionExecutor
from camera_controller import CameraControllerError
from ollama_client import OllamaClient, OllamaClientError
from robot_state import RobotState, RobotStateError
from safety_supervisor import SafetySupervisor, SafetySupervisorError


class AILoopError(RuntimeError):
    """Raised when the autonomous AI loop cannot start or run."""


def ai_loop_log(msg: str) -> None:
    """Log autonomous AI loop messages with timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{timestamp}] [AI_LOOP] {msg}")


class AILoopController:
    """Run repeated Ollama decisions through the safety supervisor."""

    def __init__(
        self,
        robot_state: RobotState,
        action_executor: ActionExecutor,
        safety_supervisor: SafetySupervisor,
        ollama_client: OllamaClient,
        snapshot_provider: Callable[[], Dict[str, Any]],
        camera_frame_provider: Optional[Callable[[], Tuple[str, Dict[str, Any]]]] = None,
        enabled_on_start: bool = False,
        decision_interval_ms: int = 1000,
        idle_interval_ms: int = 250,
        error_backoff_ms: int = 3000,
        include_camera: bool = False,
        execute_actions: bool = True,
        require_ai_mode: bool = True,
        stop_on_error: bool = True,
        max_consecutive_errors: int = 3,
    ):
        self.robot_state = robot_state
        self.action_executor = action_executor
        self.safety_supervisor = safety_supervisor
        self.ollama_client = ollama_client
        self.snapshot_provider = snapshot_provider
        self.camera_frame_provider = camera_frame_provider
        self.enabled_on_start = bool(enabled_on_start)
        self.default_decision_interval_ms = self._positive_int(
            decision_interval_ms,
            "decision_interval_ms",
        )
        self.default_idle_interval_ms = self._positive_int(
            idle_interval_ms,
            "idle_interval_ms",
        )
        self.default_error_backoff_ms = self._positive_int(
            error_backoff_ms,
            "error_backoff_ms",
        )
        self.default_include_camera = bool(include_camera)
        self.default_execute_actions = bool(execute_actions)
        self.default_require_ai_mode = bool(require_ai_mode)
        self.default_stop_on_error = bool(stop_on_error)
        self.default_max_consecutive_errors = max(1, int(max_consecutive_errors))

        self._lock = RLock()
        self._stop_event = Event()
        self._thread: Optional[Thread] = None
        self._running = False
        self._started_at: Optional[str] = None
        self._stopped_at: Optional[str] = None
        self._last_tick_at: Optional[str] = None
        self._last_decision: Optional[Dict[str, Any]] = None
        self._last_safety_result: Optional[Dict[str, Any]] = None
        self._last_camera_frame: Optional[Dict[str, Any]] = None
        self._last_error: Optional[str] = None
        self._iteration = 0
        self._consecutive_errors = 0
        self._runtime_config = self._default_runtime_config()
        ai_loop_log(
            "Initialized "
            f"enabled_on_start={self.enabled_on_start} "
            f"decision_interval_ms={self.default_decision_interval_ms} "
            f"include_camera={self.default_include_camera}"
        )

    def start(
        self,
        goal: Optional[str] = None,
        include_camera: Optional[bool] = None,
        execute_actions: Optional[bool] = None,
        decision_interval_ms: Optional[int] = None,
        set_ai_mode: bool = False,
    ) -> Dict[str, Any]:
        """Start the autonomous decision loop."""
        with self._lock:
            if self._running:
                return self.get_status()

            if goal is not None:
                self.robot_state.set_goal(goal)

            if set_ai_mode:
                self.robot_state.set_mode("ai")

            state_status = self.robot_state.get_status()
            if state_status["emergency_stop"]:
                raise AILoopError("clear emergency stop before starting AI loop")
            if self.default_require_ai_mode and state_status["mode"] != "ai":
                raise AILoopError("robot mode must be 'ai' before starting AI loop")
            if not state_status.get("goal", "").strip():
                raise AILoopError("operator goal is required before starting AI loop")

            self._runtime_config = self._default_runtime_config()
            if include_camera is not None:
                self._runtime_config["include_camera"] = bool(include_camera)
            if execute_actions is not None:
                self._runtime_config["execute_actions"] = bool(execute_actions)
            if decision_interval_ms is not None:
                self._runtime_config["decision_interval_ms"] = self._positive_int(
                    decision_interval_ms,
                    "decision_interval_ms",
                )

            self._stop_event.clear()
            self._running = True
            self._started_at = self._now()
            self._stopped_at = None
            self._last_error = None
            self._consecutive_errors = 0
            self._thread = Thread(target=self._run, name="pibot-ai-loop", daemon=True)
            self._thread.start()
            ai_loop_log("AI loop started")
            return self.get_status()

    def stop(self, stop_motors: bool = True) -> Dict[str, Any]:
        """Stop the autonomous decision loop."""
        with self._lock:
            self._stop_event.set()
            self._running = False
            self._stopped_at = self._now()

        if stop_motors:
            try:
                self.action_executor.stop_all(source="ai_loop_stop")
            except Exception as e:
                ai_loop_log(f"Stop all failed while stopping AI loop: {e}")

        thread = self._thread
        if thread and thread.is_alive() and thread is not current_thread():
            thread.join(timeout=0.2)

        ai_loop_log("AI loop stopped")
        return self.get_status()

    def get_status(self) -> Dict[str, Any]:
        """Return current loop status and last decisions."""
        with self._lock:
            thread_alive = bool(self._thread and self._thread.is_alive())
            return {
                "status": "success",
                "running": self._running and thread_alive,
                "thread_alive": thread_alive,
                "started_at": self._started_at,
                "stopped_at": self._stopped_at,
                "last_tick_at": self._last_tick_at,
                "iteration": self._iteration,
                "consecutive_errors": self._consecutive_errors,
                "last_error": self._last_error,
                "last_decision": deepcopy(self._last_decision),
                "last_safety_result": deepcopy(self._last_safety_result),
                "last_camera_frame": deepcopy(self._last_camera_frame),
                "config": deepcopy(self._runtime_config),
                "defaults": {
                    "enabled_on_start": self.enabled_on_start,
                    "decision_interval_ms": self.default_decision_interval_ms,
                    "idle_interval_ms": self.default_idle_interval_ms,
                    "error_backoff_ms": self.default_error_backoff_ms,
                    "include_camera": self.default_include_camera,
                    "execute_actions": self.default_execute_actions,
                    "require_ai_mode": self.default_require_ai_mode,
                    "stop_on_error": self.default_stop_on_error,
                    "max_consecutive_errors": self.default_max_consecutive_errors,
                },
            }

    def _run(self) -> None:
        while not self._stop_event.is_set():
            wait_ms = self._run_once()
            self._stop_event.wait(wait_ms / 1000.0)

        with self._lock:
            self._running = False
            self._stopped_at = self._now()

    def _run_once(self) -> int:
        with self._lock:
            config = deepcopy(self._runtime_config)
            self._last_tick_at = self._now()

        try:
            state_status = self.robot_state.get_status()
            mode = state_status["mode"]
            if state_status["emergency_stop"]:
                self._record_idle("emergency stop active")
                self.action_executor.stop_all(source="ai_loop_estop")
                return config["idle_interval_ms"]
            if config["require_ai_mode"] and mode != "ai":
                self._record_idle(f"robot mode is '{mode}', waiting for ai mode")
                self.action_executor.stop_all(source="ai_loop_mode_wait")
                return config["idle_interval_ms"]

            goal = state_status.get("goal", "").strip()
            if not goal:
                self._record_idle("operator goal is empty")
                return config["idle_interval_ms"]

            image_b64 = None
            camera_frame = None
            if config["include_camera"]:
                if not self.camera_frame_provider:
                    raise AILoopError("camera frame provider is not configured")
                image_b64, camera_frame = self.camera_frame_provider()

            snapshot = self.snapshot_provider()
            decision = self.ollama_client.decide(
                snapshot,
                operator_goal=goal,
                image_b64=image_b64,
            )

            if self._stop_event.is_set():
                return config["idle_interval_ms"]

            safety_result = None
            if config["execute_actions"]:
                safety_result = self.safety_supervisor.propose(
                    decision["proposal"]["actions"],
                    source="ai",
                )

            with self._lock:
                self._iteration += 1
                self._consecutive_errors = 0
                self._last_error = None
                self._last_decision = deepcopy(decision)
                self._last_safety_result = deepcopy(safety_result)
                self._last_camera_frame = deepcopy(camera_frame)

            next_check_ms = decision.get("proposal", {}).get("next_check_ms")
            return self._next_wait_ms(next_check_ms, config)
        except (
            AILoopError,
            CameraControllerError,
            OllamaClientError,
            RobotStateError,
            SafetySupervisorError,
        ) as e:
            return self._handle_iteration_error(str(e), config)
        except Exception as e:
            return self._handle_iteration_error(f"AI loop failed: {e}", config)

    def _handle_iteration_error(self, message: str, config: Dict[str, Any]) -> int:
        ai_loop_log(message)
        with self._lock:
            self._iteration += 1
            self._consecutive_errors += 1
            self._last_error = message

        if config["stop_on_error"]:
            try:
                self.action_executor.stop_all(source="ai_loop_error")
            except Exception as e:
                ai_loop_log(f"Stop all failed after AI loop error: {e}")

        if self._consecutive_errors >= config["max_consecutive_errors"]:
            ai_loop_log("AI loop stopping after repeated errors")
            with self._lock:
                self._running = False
                self._stopped_at = self._now()
                self._stop_event.set()

        return config["error_backoff_ms"]

    def _record_idle(self, reason: str) -> None:
        with self._lock:
            self._last_error = reason

    def _next_wait_ms(self, requested_ms: Any, config: Dict[str, Any]) -> int:
        base_ms = config["decision_interval_ms"]
        if isinstance(requested_ms, bool) or not isinstance(requested_ms, (int, float)):
            return base_ms
        return max(base_ms, int(requested_ms))

    def _default_runtime_config(self) -> Dict[str, Any]:
        return {
            "decision_interval_ms": self.default_decision_interval_ms,
            "idle_interval_ms": self.default_idle_interval_ms,
            "error_backoff_ms": self.default_error_backoff_ms,
            "include_camera": self.default_include_camera,
            "execute_actions": self.default_execute_actions,
            "require_ai_mode": self.default_require_ai_mode,
            "stop_on_error": self.default_stop_on_error,
            "max_consecutive_errors": self.default_max_consecutive_errors,
        }

    def _positive_int(self, value: Any, field_name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise AILoopError(f"{field_name} must be a positive integer")
        value = int(value)
        if value < 1:
            raise AILoopError(f"{field_name} must be positive")
        return value

    def _now(self) -> str:
        return datetime.now().isoformat(timespec="milliseconds")
