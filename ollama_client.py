import json
import socket
import time
import urllib.error
import urllib.request
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Dict, Optional

from robot_state import RobotState


AVAILABLE_ACTIONS = [
    "stop_all",
    "drive_tank",
    "rotate",
    "stepper_move",
    "display_text",
    "display_frame",
    "camera_capture",
]

ACTION_REFERENCE = {
    "stop_all": {
        "purpose": "Stop all chassis and arm motors immediately.",
        "movement": "safety_stop",
        "schema": {"type": "stop_all"},
    },
    "drive_tank": {
        "purpose": "Move the chassis forward, backward, or along a gentle arc by powering the left and right drive motors.",
        "movement": "chassis",
        "schema": {
            "type": "drive_tank",
            "left_power": "number from -100 to 100",
            "right_power": "number from -100 to 100",
            "duration_ms": "short duration, max safety limit applies",
        },
        "example": {
            "type": "drive_tank",
            "left_power": 20,
            "right_power": 20,
            "duration_ms": 300,
        },
    },
    "rotate": {
        "purpose": "Turn the chassis in place to face another direction.",
        "movement": "chassis",
        "schema": {
            "type": "rotate",
            "power": "positive number",
            "direction": "left or right",
            "duration_ms": "short duration, max safety limit applies",
        },
        "example": {
            "type": "rotate",
            "power": 20,
            "direction": "left",
            "duration_ms": 250,
        },
    },
    "stepper_move": {
        "purpose": "Move one arm stepper motor only. This does not move the chassis toward a doorway or destination.",
        "movement": "arm_only",
        "schema": {
            "type": "stepper_move",
            "motor": "stepper_1 or stepper_2",
            "steps": "positive integer, max safety limit applies",
            "direction": "forward or backward",
        },
    },
    "display_text": {
        "purpose": "Write text on the OLED display.",
        "movement": "none",
        "schema": {"type": "display_text", "text": "string", "x": 0, "y": 0, "clear": True},
    },
    "display_frame": {
        "purpose": "Write a full 128x64 OLED pixel frame.",
        "movement": "none",
        "schema": {"type": "display_frame", "rows": "64 strings of 128 0/1 pixels"},
    },
    "camera_capture": {
        "purpose": "Capture a camera frame for observation.",
        "movement": "none",
        "schema": {"type": "camera_capture"},
    },
}

SYSTEM_PROMPT = """You are the decision layer for a physical robot chassis.
You must only return valid JSON.
You may only use the actions listed in available_actions.
All movement must be short-duration and cautious.
If sensor data is stale, unsafe, or missing, stop or wait by returning no movement actions.
Never invent sensors, motors, actions, or hardware limits.
Use drive_tank or rotate for chassis movement. Never use stepper_move for navigation; stepper_move is arm-only.
The robot runtime validates every action before execution.
Return exactly this JSON shape:
{"speech":"short status sentence","actions":[],"next_check_ms":500}
"""

PLANNER_PROMPT = """You are the high-level reasoning layer for a physical robot chassis.
Think from the robot's current state, camera image if present, sensors, and operator goal.
Write a concise plain-English intent for what should happen next.
Do not return JSON.
Do not invent sensors, motors, actions, or hardware limits.
If the goal is empty, ambiguous, unsafe, or impossible, say that no physical action should be taken.
Mention only actions that can be represented by available_actions.
Use drive_tank or rotate for chassis movement. Never use stepper_move for navigation; stepper_move is arm-only.
If the goal is to move toward a doorway or destination, describe cautious chassis movement, not arm movement.
For movement, specify cautious short-duration motion only.
"""

TRANSLATOR_PROMPT = """You translate a robot intent into strict robot action JSON.
You must only return valid JSON.
You may only use the actions listed in available_actions.
Do not invent sensors, motors, actions, or hardware limits.
If the intent is ambiguous, unsafe, impossible, or says no physical action, return an empty actions list.
Use drive_tank or rotate for chassis movement. Never use stepper_move for navigation; stepper_move is arm-only.
If an intent says to move the robot toward a doorway using stepper movement, correct that hardware mistake by using cautious chassis drive_tank or rotate actions instead of stepper_move.
All movement must be short-duration and cautious.
Return exactly this JSON shape:
{"speech":"short status sentence","actions":[],"next_check_ms":500}
"""


class OllamaClientError(RuntimeError):
    """Raised when the Ollama decision request cannot produce a valid proposal."""

    def __init__(self, message: str, status_code: int = 503):
        super().__init__(message)
        self.status_code = status_code


def ollama_log(msg: str) -> None:
    """Log Ollama client messages with timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{timestamp}] [OLLAMA] {msg}")


class OllamaClient:
    """Build one-shot robot decision requests for Ollama."""

    def __init__(
        self,
        enabled: bool = False,
        url: str = "http://localhost:11434",
        model: str = "llava:latest",
        two_stage: bool = True,
        translator_model: Optional[str] = None,
        translator_timeout_ms: Optional[int] = None,
        timeout_ms: int = 1500,
        include_camera: bool = False,
        execute_actions: bool = False,
        request_log_enabled: bool = True,
        request_log_path: str = "logs/ollama_requests.jsonl",
        request_log_include_images: bool = False,
        robot_state: Optional[RobotState] = None,
        transport: Optional[Callable[[Dict[str, Any], float], Dict[str, Any]]] = None,
    ):
        self.enabled = bool(enabled)
        self.url = str(url or "http://localhost:11434").rstrip("/")
        self.model = str(model or "llava:latest")
        self.two_stage = bool(two_stage)
        self.translator_model = str(translator_model or self.model)
        self.translator_timeout_ms = max(
            1,
            int(translator_timeout_ms if translator_timeout_ms is not None else timeout_ms),
        )
        self.timeout_ms = max(1, int(timeout_ms))
        self.include_camera = bool(include_camera)
        self.execute_actions = bool(execute_actions)
        self.request_log_enabled = bool(request_log_enabled)
        self.request_log_path = Path(request_log_path or "logs/ollama_requests.jsonl")
        self.request_log_include_images = bool(request_log_include_images)
        self.robot_state = robot_state
        self.transport = transport
        self._log_lock = RLock()
        self._next_log_id = 1
        self._last_request_at: Optional[str] = None
        self._last_response_at: Optional[str] = None
        self._last_duration_ms: Optional[int] = None
        self._last_error: Optional[str] = None
        self._last_log_error: Optional[str] = None
        self._last_intent: Optional[Dict[str, Any]] = None
        self._last_translation_context: Optional[Dict[str, Any]] = None
        self._last_proposal: Optional[Dict[str, Any]] = None
        ollama_log(
            "Initialized "
            f"enabled={self.enabled} url={self.url} model={self.model} "
            f"two_stage={self.two_stage} translator_model={self.translator_model} "
            f"timeout_ms={self.timeout_ms} log_path={self.request_log_path}"
        )

    def get_status(self) -> Dict[str, Any]:
        """Return Ollama configuration and latest one-shot decision status."""
        return {
            "status": "success",
            "enabled": self.enabled,
            "url": self.url,
            "model": self.model,
            "two_stage": self.two_stage,
            "planner_model": self.model,
            "translator_model": self.translator_model,
            "timeout_ms": self.timeout_ms,
            "translator_timeout_ms": self.translator_timeout_ms,
            "include_camera": self.include_camera,
            "execute_actions": self.execute_actions,
            "request_log": {
                "enabled": self.request_log_enabled,
                "path": str(self.request_log_path),
                "include_images": self.request_log_include_images,
                "last_error": self._last_log_error,
            },
            "available_actions": list(AVAILABLE_ACTIONS),
            "last_request_at": self._last_request_at,
            "last_response_at": self._last_response_at,
            "last_duration_ms": self._last_duration_ms,
            "last_error": self._last_error,
            "last_intent": deepcopy(self._last_intent),
            "last_proposal": deepcopy(self._last_proposal),
        }

    def build_payload(
        self,
        robot_snapshot: Dict[str, Any],
        operator_goal: Optional[str] = None,
        image_b64: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build the Ollama `/api/chat` payload for a single decision."""
        if not isinstance(robot_snapshot, dict):
            raise OllamaClientError("robot_snapshot must be a JSON object", 400)

        goal = operator_goal
        if goal is None:
            goal = robot_snapshot.get("robot", {}).get("operator_goal", "")
        if not isinstance(goal, str):
            raise OllamaClientError("operator goal must be a string", 400)

        user_payload = {
            "operator_goal": goal,
            "available_actions": list(AVAILABLE_ACTIONS),
            "action_reference": self._action_reference(),
            "robot_snapshot": self._build_model_snapshot(robot_snapshot),
            "output_schema": {
                "speech": "string",
                "actions": "array of allowed action objects",
                "next_check_ms": "integer milliseconds before next decision",
            },
        }

        user_message: Dict[str, Any] = {
            "role": "user",
            "content": json.dumps(user_payload, separators=(",", ":"), sort_keys=True),
        }
        if image_b64:
            user_message["images"] = [image_b64]

        return {
            "model": self.model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                user_message,
            ],
        }

    def build_planner_payload(
        self,
        robot_snapshot: Dict[str, Any],
        operator_goal: Optional[str] = None,
        image_b64: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build the first-stage planner payload for plain-English intent."""
        if not isinstance(robot_snapshot, dict):
            raise OllamaClientError("robot_snapshot must be a JSON object", 400)

        goal = operator_goal
        if goal is None:
            goal = robot_snapshot.get("robot", {}).get("operator_goal", "")
        if not isinstance(goal, str):
            raise OllamaClientError("operator goal must be a string", 400)

        user_payload = {
            "operator_goal": goal,
            "available_actions": list(AVAILABLE_ACTIONS),
            "action_reference": self._action_reference(),
            "robot_snapshot": self._build_model_snapshot(robot_snapshot),
            "instruction": (
                "Write a short plain-English intent describing what the robot "
                "should do next. Do not write JSON."
            ),
        }

        user_message: Dict[str, Any] = {
            "role": "user",
            "content": json.dumps(user_payload, separators=(",", ":"), sort_keys=True),
        }
        if image_b64:
            user_message["images"] = [image_b64]

        return {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": PLANNER_PROMPT},
                user_message,
            ],
        }

    def build_translator_payload(
        self,
        intent_text: str,
        operator_goal: str,
        model_snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build the second-stage translator payload for strict action JSON."""
        if not isinstance(intent_text, str) or not intent_text.strip():
            raise OllamaClientError("intent_text must be a non-empty string", 400)
        if not isinstance(operator_goal, str):
            raise OllamaClientError("operator goal must be a string", 400)
        if not isinstance(model_snapshot, dict):
            raise OllamaClientError("model_snapshot must be a JSON object", 400)

        user_payload = {
            "operator_goal": operator_goal,
            "available_actions": list(AVAILABLE_ACTIONS),
            "action_reference": self._action_reference(),
            "planner_intent": intent_text,
            "robot_snapshot": model_snapshot,
            "output_schema": {
                "speech": "string",
                "actions": "array of allowed action objects",
                "next_check_ms": "integer milliseconds before next decision",
            },
        }

        return {
            "model": self.translator_model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": TRANSLATOR_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        user_payload,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                },
            ],
        }

    def decide(
        self,
        robot_snapshot: Dict[str, Any],
        operator_goal: Optional[str] = None,
        image_b64: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Ask Ollama for one robot action proposal."""
        if not self.enabled:
            raise OllamaClientError("Ollama client is disabled")
        if self.two_stage:
            return self._decide_two_stage(robot_snapshot, operator_goal, image_b64)

        return self._decide_single_stage(robot_snapshot, operator_goal, image_b64)

    def translate_last_intent(self) -> Dict[str, Any]:
        """Retry only the JSON translation stage using the cached planner intent."""
        if not self.enabled:
            raise OllamaClientError("Ollama client is disabled")
        if not self._last_intent or not self._last_translation_context:
            raise OllamaClientError("No cached planner intent is available", 400)

        context = deepcopy(self._last_translation_context)
        return self._translate_intent(
            intent_text=self._last_intent["intent"],
            operator_goal=context["operator_goal"],
            model_snapshot=context["model_snapshot"],
            start_total=time.monotonic(),
            planning_result=None,
            source="retry",
        )

    def _decide_single_stage(
        self,
        robot_snapshot: Dict[str, Any],
        operator_goal: Optional[str] = None,
        image_b64: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = self.build_payload(robot_snapshot, operator_goal, image_b64)
        timeout_seconds = self.timeout_ms / 1000.0
        self._last_request_at = self._now()
        request_at = self._last_request_at
        self._last_error = None
        response = None
        start = time.monotonic()

        try:
            response = (
                self.transport(payload, timeout_seconds)
                if self.transport is not None
                else self._post_chat(payload, timeout_seconds, self.timeout_ms)
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            content = self._extract_content(response)
            parsed = self._parse_json_content(content)
            proposal = self._validate_proposal(parsed)
            self._last_response_at = self._now()
            self._last_duration_ms = duration_ms
            self._last_proposal = deepcopy(proposal)
            self._last_error = None
            if self.robot_state:
                self.robot_state.record_ai_response(proposal)
            result = {
                "status": "success",
                "model": self.model,
                "duration_ms": duration_ms,
                "proposal": proposal,
                "raw": response,
            }
            self._write_request_log(
                event="ollama_decision",
                stage="single_stage",
                model=self.model,
                request_at=request_at,
                response_at=self._last_response_at,
                duration_ms=duration_ms,
                payload=payload,
                response=response,
                proposal=proposal,
                error=None,
            )
            return result
        except OllamaClientError as e:
            self._record_error(str(e), start)
            if self.robot_state:
                self.robot_state.record_ai_response(None, error=str(e))
            self._write_request_log(
                event="ollama_decision",
                stage="single_stage",
                model=self.model,
                request_at=request_at,
                response_at=self._last_response_at,
                duration_ms=self._last_duration_ms,
                payload=payload,
                response=response,
                proposal=None,
                error=str(e),
            )
            raise
        except Exception as e:
            message = f"Ollama request failed: {e}"
            self._record_error(message, start)
            if self.robot_state:
                self.robot_state.record_ai_response(None, error=message)
            self._write_request_log(
                event="ollama_decision",
                stage="single_stage",
                model=self.model,
                request_at=request_at,
                response_at=self._last_response_at,
                duration_ms=self._last_duration_ms,
                payload=payload,
                response=response,
                proposal=None,
                error=message,
            )
            raise OllamaClientError(message) from e

    def _decide_two_stage(
        self,
        robot_snapshot: Dict[str, Any],
        operator_goal: Optional[str] = None,
        image_b64: Optional[str] = None,
    ) -> Dict[str, Any]:
        model_snapshot = self._build_model_snapshot(robot_snapshot)
        goal = operator_goal
        if goal is None:
            goal = robot_snapshot.get("robot", {}).get("operator_goal", "")
        if not isinstance(goal, str):
            raise OllamaClientError("operator goal must be a string", 400)

        payload = self.build_planner_payload(robot_snapshot, goal, image_b64)
        timeout_seconds = self.timeout_ms / 1000.0
        self._last_request_at = self._now()
        request_at = self._last_request_at
        self._last_error = None
        response = None
        start = time.monotonic()

        try:
            response = (
                self.transport(payload, timeout_seconds)
                if self.transport is not None
                else self._post_chat(payload, timeout_seconds, self.timeout_ms)
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            intent_text = self._extract_content(response).strip()
            if not intent_text:
                raise OllamaClientError("Ollama planner returned empty intent", 502)

            response_at = self._now()
            self._last_intent = {
                "intent": intent_text,
                "model": self.model,
                "operator_goal": goal,
                "created_at": response_at,
                "duration_ms": duration_ms,
                "image_attached": bool(image_b64),
            }
            self._last_translation_context = {
                "operator_goal": goal,
                "model_snapshot": deepcopy(model_snapshot),
            }
            planning_result = {
                "status": "success",
                "model": self.model,
                "duration_ms": duration_ms,
                "intent": intent_text,
                "raw": response,
            }
            self._write_request_log(
                event="ollama_planner",
                stage="planner",
                model=self.model,
                request_at=request_at,
                response_at=response_at,
                duration_ms=duration_ms,
                payload=payload,
                response=response,
                proposal=None,
                intent=intent_text,
                error=None,
            )
        except OllamaClientError as e:
            self._record_error(str(e), start)
            if self.robot_state:
                self.robot_state.record_ai_response(None, error=str(e))
            self._write_request_log(
                event="ollama_planner",
                stage="planner",
                model=self.model,
                request_at=request_at,
                response_at=self._last_response_at,
                duration_ms=self._last_duration_ms,
                payload=payload,
                response=response,
                proposal=None,
                intent=None,
                error=str(e),
            )
            raise
        except Exception as e:
            message = f"Ollama planner request failed: {e}"
            self._record_error(message, start)
            if self.robot_state:
                self.robot_state.record_ai_response(None, error=message)
            self._write_request_log(
                event="ollama_planner",
                stage="planner",
                model=self.model,
                request_at=request_at,
                response_at=self._last_response_at,
                duration_ms=self._last_duration_ms,
                payload=payload,
                response=response,
                proposal=None,
                intent=None,
                error=message,
            )
            raise OllamaClientError(message) from e

        return self._translate_intent(
            intent_text=intent_text,
            operator_goal=goal,
            model_snapshot=model_snapshot,
            start_total=start,
            planning_result=planning_result,
            source="fresh",
        )

    def _translate_intent(
        self,
        intent_text: str,
        operator_goal: str,
        model_snapshot: Dict[str, Any],
        start_total: float,
        planning_result: Optional[Dict[str, Any]],
        source: str,
    ) -> Dict[str, Any]:
        payload = self.build_translator_payload(intent_text, operator_goal, model_snapshot)
        timeout_seconds = self.translator_timeout_ms / 1000.0
        request_at = self._now()
        response = None
        start = time.monotonic()

        try:
            response = (
                self.transport(payload, timeout_seconds)
                if self.transport is not None
                else self._post_chat(payload, timeout_seconds, self.translator_timeout_ms)
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            content = self._extract_content(response)
            parsed = self._parse_json_content(content)
            proposal = self._validate_proposal(parsed)
            response_at = self._now()
            total_duration_ms = int((time.monotonic() - start_total) * 1000)
            self._last_response_at = response_at
            self._last_duration_ms = total_duration_ms
            self._last_proposal = deepcopy(proposal)
            self._last_error = None
            if self.robot_state:
                self.robot_state.record_ai_response(
                    {
                        "intent": intent_text,
                        "proposal": proposal,
                        "mode": "two_stage",
                    }
                )
            self._write_request_log(
                event="ollama_translator",
                stage="translator",
                model=self.translator_model,
                request_at=request_at,
                response_at=response_at,
                duration_ms=duration_ms,
                payload=payload,
                response=response,
                proposal=proposal,
                intent=intent_text,
                error=None,
            )
            return {
                "status": "success",
                "mode": "two_stage",
                "source": source,
                "model": self.model,
                "planner_model": self.model,
                "translator_model": self.translator_model,
                "duration_ms": total_duration_ms,
                "intent": intent_text,
                "proposal": proposal,
                "planning": planning_result,
                "translation": {
                    "status": "success",
                    "model": self.translator_model,
                    "duration_ms": duration_ms,
                    "raw": response,
                },
            }
        except OllamaClientError as e:
            message = f"Ollama translation failed: {e}"
            self._record_error(message, start_total)
            if self.robot_state:
                self.robot_state.record_ai_response(
                    {"intent": intent_text, "mode": "two_stage"},
                    error=message,
                )
            self._write_request_log(
                event="ollama_translator",
                stage="translator",
                model=self.translator_model,
                request_at=request_at,
                response_at=self._last_response_at,
                duration_ms=int((time.monotonic() - start) * 1000),
                payload=payload,
                response=response,
                proposal=None,
                intent=intent_text,
                error=message,
            )
            raise OllamaClientError(message, e.status_code) from e
        except Exception as e:
            message = f"Ollama translator request failed: {e}"
            self._record_error(message, start_total)
            if self.robot_state:
                self.robot_state.record_ai_response(
                    {"intent": intent_text, "mode": "two_stage"},
                    error=message,
                )
            self._write_request_log(
                event="ollama_translator",
                stage="translator",
                model=self.translator_model,
                request_at=request_at,
                response_at=self._last_response_at,
                duration_ms=int((time.monotonic() - start) * 1000),
                payload=payload,
                response=response,
                proposal=None,
                intent=intent_text,
                error=message,
            )
            raise OllamaClientError(message) from e

    def read_request_log(self, limit: int = 50) -> Dict[str, Any]:
        """Return recent Ollama request log entries from the JSONL file."""
        limit = max(1, min(500, int(limit)))
        if not self.request_log_path.exists():
            return {
                "status": "success",
                "path": str(self.request_log_path),
                "entries": [],
                "count": 0,
            }

        with self._log_lock:
            lines = self.request_log_path.read_text(encoding="utf-8").splitlines()

        entries = []
        for line in lines[-limit:]:
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                entries.append({"malformed_log_line": line})

        return {
            "status": "success",
            "path": str(self.request_log_path),
            "entries": entries,
            "count": len(entries),
        }

    def _post_chat(
        self,
        payload: Dict[str, Any],
        timeout_seconds: float,
        timeout_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        request = urllib.request.Request(
            f"{self.url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise OllamaClientError(
                f"Ollama HTTP {e.code}: {detail or e.reason}",
                502,
            ) from e
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", e)
            raise OllamaClientError(f"Ollama unavailable: {reason}") from e
        except socket.timeout as e:
            effective_timeout_ms = (
                int(timeout_ms) if timeout_ms is not None else int(timeout_seconds * 1000)
            )
            raise OllamaClientError(
                f"Ollama timed out after {effective_timeout_ms} ms",
                504,
            ) from e

        try:
            decoded = json.loads(body)
        except json.JSONDecodeError as e:
            raise OllamaClientError(
                f"Ollama returned non-JSON HTTP body: {e}",
                502,
            ) from e
        if not isinstance(decoded, dict):
            raise OllamaClientError("Ollama HTTP body must be a JSON object", 502)
        return decoded

    def _extract_content(self, response: Dict[str, Any]) -> str:
        if not isinstance(response, dict):
            raise OllamaClientError("Ollama response must be a JSON object", 502)

        message = response.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"]
        if isinstance(response.get("response"), str):
            return response["response"]
        raise OllamaClientError("Ollama response did not include message.content", 502)

    def _parse_json_content(self, content: str) -> Any:
        if not isinstance(content, str) or not content.strip():
            raise OllamaClientError("Ollama response content was empty", 502)

        stripped = content.strip()
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            extracted = self._extract_first_json_value(stripped)
            if extracted is None:
                raise OllamaClientError("Ollama response did not contain valid JSON", 502)
            try:
                return json.loads(extracted)
            except json.JSONDecodeError as e:
                raise OllamaClientError(f"Ollama response JSON parse failed: {e}", 502) from e

    def _extract_first_json_value(self, text: str) -> Optional[str]:
        for start_index, char in enumerate(text):
            if char not in "{[":
                continue
            close_char = "}" if char == "{" else "]"
            depth = 0
            in_string = False
            escaped = False
            for index in range(start_index, len(text)):
                current = text[index]
                if in_string:
                    if escaped:
                        escaped = False
                    elif current == "\\":
                        escaped = True
                    elif current == '"':
                        in_string = False
                    continue
                if current == '"':
                    in_string = True
                elif current == char:
                    depth += 1
                elif current == close_char:
                    depth -= 1
                    if depth == 0:
                        return text[start_index : index + 1]
        return None

    def _validate_proposal(self, proposal: Any) -> Dict[str, Any]:
        if not isinstance(proposal, dict):
            raise OllamaClientError("Ollama proposal must be a JSON object", 502)

        if "actions" not in proposal:
            raise OllamaClientError("Ollama proposal actions must be a list", 502)
        actions = proposal.get("actions")
        if not isinstance(actions, list):
            raise OllamaClientError("Ollama proposal actions must be a list", 502)
        for action in actions:
            if not isinstance(action, dict):
                raise OllamaClientError("Each Ollama action must be a JSON object", 502)

        speech = proposal.get("speech", "")
        if speech is None:
            speech = ""
        if not isinstance(speech, str):
            speech = str(speech)

        next_check_ms = proposal.get("next_check_ms", 500)
        if isinstance(next_check_ms, bool) or not isinstance(next_check_ms, (int, float)):
            next_check_ms = 500
        next_check_ms = int(max(100, min(5000, next_check_ms)))

        return {
            "speech": speech[:500],
            "actions": deepcopy(actions),
            "next_check_ms": next_check_ms,
        }

    def _action_reference(self) -> Dict[str, Any]:
        return deepcopy(ACTION_REFERENCE)

    def _build_model_snapshot(self, robot_snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Return a compact, model-facing snapshot without internal AI metadata."""
        robot = robot_snapshot.get("robot", {}) if isinstance(robot_snapshot, dict) else {}
        memory = robot_snapshot.get("memory", {}) if isinstance(robot_snapshot, dict) else {}

        camera = robot.get("camera", {}) if isinstance(robot, dict) else {}
        display = robot.get("display", {}) if isinstance(robot, dict) else {}
        safety = robot.get("safety", {}) if isinstance(robot, dict) else {}
        executor = robot.get("executor", {}) if isinstance(robot, dict) else {}

        compact_robot = {
            "mode": robot.get("mode"),
            "operator_goal": robot.get("operator_goal"),
            "emergency_stop": robot.get("emergency_stop"),
            "emergency_stop_reason": robot.get("emergency_stop_reason"),
            "motors": deepcopy(robot.get("motors", {})),
            "executor": {
                "active_action": deepcopy(executor.get("active_action")),
            },
            "camera": {
                "available": camera.get("available"),
                "width": camera.get("width"),
                "height": camera.get("height"),
                "last_frame": deepcopy(camera.get("last_frame")),
            },
            "display": {
                "available": display.get("available"),
                "width": display.get("width"),
                "height": display.get("height"),
                "last_action": deepcopy(display.get("last_action")),
            },
            "safety": {
                "mode": safety.get("mode"),
                "emergency_stop": safety.get("emergency_stop"),
                "limits": deepcopy(safety.get("limits", {})),
                "last_decision": self._compact_last_decision(safety.get("last_decision")),
            },
        }

        recent_actions = memory.get("recent_actions", [])
        if not isinstance(recent_actions, list):
            recent_actions = []

        return {
            "status": robot_snapshot.get("status"),
            "robot": compact_robot,
            "sensors": deepcopy(robot_snapshot.get("sensors", {})),
            "memory": {
                "last_action": deepcopy(memory.get("last_action")),
                "recent_actions": deepcopy(recent_actions[-5:]),
            },
            "timestamps": deepcopy(robot_snapshot.get("timestamps", {})),
        }

    def _compact_last_decision(self, decision: Any) -> Any:
        if not isinstance(decision, dict):
            return None
        return {
            "decision": decision.get("decision"),
            "source": decision.get("source"),
            "action": deepcopy(decision.get("action")),
            "reason": decision.get("reason"),
            "executed": decision.get("executed"),
            "timestamp": decision.get("timestamp"),
        }

    def _record_error(self, message: str, start: float) -> None:
        self._last_response_at = self._now()
        self._last_duration_ms = int((time.monotonic() - start) * 1000)
        self._last_error = message
        self._last_proposal = None
        ollama_log(message)

    def _write_request_log(
        self,
        event: str,
        stage: str,
        model: str,
        request_at: Optional[str],
        response_at: Optional[str],
        duration_ms: Optional[int],
        payload: Dict[str, Any],
        response: Optional[Dict[str, Any]],
        proposal: Optional[Dict[str, Any]],
        intent: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        if not self.request_log_enabled:
            return

        try:
            with self._log_lock:
                request_id = self._next_log_id
                self._next_log_id += 1
                event = {
                    "event": event,
                    "stage": stage,
                    "request_id": request_id,
                    "request_at": request_at,
                    "response_at": response_at,
                    "duration_ms": duration_ms,
                    "url": self.url,
                    "model": model,
                    "timeout_ms": (
                        self.translator_timeout_ms
                        if stage == "translator"
                        else self.timeout_ms
                    ),
                    "status": "error" if error else "success",
                    "error": error,
                    "intent": intent,
                    "request": self._sanitize_for_log(payload),
                    "response": self._sanitize_for_log(response),
                    "proposal": deepcopy(proposal),
                }
                self.request_log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.request_log_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(event, separators=(",", ":"), default=str))
                    f.write("\n")
                self._last_log_error = None
        except Exception as e:
            self._last_log_error = str(e)
            ollama_log(f"Request log write failed: {e}")

    def _sanitize_for_log(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: self._sanitize_images(item) if key == "images" else self._sanitize_for_log(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._sanitize_for_log(item) for item in value]
        return deepcopy(value)

    def _sanitize_images(self, images: Any) -> Any:
        if self.request_log_include_images:
            return deepcopy(images)
        if not isinstance(images, list):
            return "<omitted non-list images payload>"

        summaries = []
        for image in images:
            if isinstance(image, str):
                summaries.append(
                    {
                        "omitted": True,
                        "base64_chars": len(image),
                        "sha256": sha256(image.encode("utf-8")).hexdigest(),
                    }
                )
            else:
                summaries.append({"omitted": True, "type": type(image).__name__})
        return summaries

    def _now(self) -> str:
        return datetime.now().isoformat(timespec="milliseconds")
