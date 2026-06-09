import json
import socket
import time
import urllib.error
import urllib.request
from copy import deepcopy
from datetime import datetime
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

SYSTEM_PROMPT = """You are the decision layer for a physical robot chassis.
You must only return valid JSON.
You may only use the actions listed in available_actions.
All movement must be short-duration and cautious.
If sensor data is stale, unsafe, or missing, stop or wait by returning no movement actions.
Never invent sensors, motors, actions, or hardware limits.
The robot runtime validates every action before execution.
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
        timeout_ms: int = 1500,
        include_camera: bool = False,
        execute_actions: bool = False,
        robot_state: Optional[RobotState] = None,
        transport: Optional[Callable[[Dict[str, Any], float], Dict[str, Any]]] = None,
    ):
        self.enabled = bool(enabled)
        self.url = str(url or "http://localhost:11434").rstrip("/")
        self.model = str(model or "llava:latest")
        self.timeout_ms = max(1, int(timeout_ms))
        self.include_camera = bool(include_camera)
        self.execute_actions = bool(execute_actions)
        self.robot_state = robot_state
        self.transport = transport
        self._last_request_at: Optional[str] = None
        self._last_response_at: Optional[str] = None
        self._last_duration_ms: Optional[int] = None
        self._last_error: Optional[str] = None
        self._last_proposal: Optional[Dict[str, Any]] = None
        ollama_log(
            "Initialized "
            f"enabled={self.enabled} url={self.url} model={self.model} "
            f"timeout_ms={self.timeout_ms}"
        )

    def get_status(self) -> Dict[str, Any]:
        """Return Ollama configuration and latest one-shot decision status."""
        return {
            "status": "success",
            "enabled": self.enabled,
            "url": self.url,
            "model": self.model,
            "timeout_ms": self.timeout_ms,
            "include_camera": self.include_camera,
            "execute_actions": self.execute_actions,
            "available_actions": list(AVAILABLE_ACTIONS),
            "last_request_at": self._last_request_at,
            "last_response_at": self._last_response_at,
            "last_duration_ms": self._last_duration_ms,
            "last_error": self._last_error,
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
            "robot_snapshot": robot_snapshot,
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

    def decide(
        self,
        robot_snapshot: Dict[str, Any],
        operator_goal: Optional[str] = None,
        image_b64: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Ask Ollama for one robot action proposal."""
        if not self.enabled:
            raise OllamaClientError("Ollama client is disabled")

        payload = self.build_payload(robot_snapshot, operator_goal, image_b64)
        timeout_seconds = self.timeout_ms / 1000.0
        self._last_request_at = self._now()
        self._last_error = None
        start = time.monotonic()

        try:
            response = (
                self.transport(payload, timeout_seconds)
                if self.transport is not None
                else self._post_chat(payload, timeout_seconds)
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
            return {
                "status": "success",
                "model": self.model,
                "duration_ms": duration_ms,
                "proposal": proposal,
                "raw": response,
            }
        except OllamaClientError as e:
            self._record_error(str(e), start)
            if self.robot_state:
                self.robot_state.record_ai_response(None, error=str(e))
            raise
        except Exception as e:
            message = f"Ollama request failed: {e}"
            self._record_error(message, start)
            if self.robot_state:
                self.robot_state.record_ai_response(None, error=message)
            raise OllamaClientError(message) from e

    def _post_chat(
        self,
        payload: Dict[str, Any],
        timeout_seconds: float,
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
            raise OllamaClientError(
                f"Ollama timed out after {self.timeout_ms} ms",
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

    def _record_error(self, message: str, start: float) -> None:
        self._last_response_at = self._now()
        self._last_duration_ms = int((time.monotonic() - start) * 1000)
        self._last_error = message
        self._last_proposal = None
        ollama_log(message)

    def _now(self) -> str:
        return datetime.now().isoformat(timespec="milliseconds")
