#!/usr/bin/env python3
import contextlib
import io
import json
import time
from pathlib import Path

from ollama_client import OllamaClient, OllamaClientError
from robot_state import RobotState


def make_snapshot():
    return {
        "status": "success",
        "robot": {
            "mode": "ai",
            "operator_goal": "look around and report what you see",
            "emergency_stop": False,
            "motors": {
                "left_motor": {"type": "DC", "speed": 0, "direction": "stopped"},
                "right_motor": {"type": "DC", "speed": 0, "direction": "stopped"},
            },
            "safety": {
                "limits": {
                    "max_drive_power": 100,
                    "max_action_ms": 1500,
                    "max_stepper_steps": 500,
                }
            },
        },
        "sensors": {},
        "memory": {"recent_actions": [], "last_ai_response": None},
    }


def make_client(content, capture=None):
    def transport(payload, timeout_seconds):
        if capture is not None:
            capture["payload"] = payload
            capture["timeout_seconds"] = timeout_seconds
        return {"message": {"content": content}}

    return OllamaClient(
        enabled=True,
        url="http://ollama.test:11434",
        model="test-model",
        timeout_ms=1200,
        request_log_enabled=False,
        transport=transport,
    )


def test_decide_parses_strict_json():
    client = make_client(
        json.dumps(
            {
                "speech": "I see a clear path.",
                "actions": [{"type": "display_text", "text": "clear"}],
                "next_check_ms": 250,
            }
        )
    )
    result = client.decide(make_snapshot())
    assert result["status"] == "success"
    assert result["proposal"]["speech"] == "I see a clear path."
    assert result["proposal"]["actions"][0]["type"] == "display_text"
    assert result["proposal"]["next_check_ms"] == 250


def test_decide_recovers_wrapped_json():
    client = make_client(
        'Here is the decision: {"speech":"holding","actions":[],"next_check_ms":300}'
    )
    result = client.decide(make_snapshot())
    assert result["proposal"]["speech"] == "holding"
    assert result["proposal"]["actions"] == []
    assert result["proposal"]["next_check_ms"] == 300


def test_missing_actions_rejected():
    client = make_client('{"speech":"missing action list","next_check_ms":300}')
    try:
        client.decide(make_snapshot())
    except OllamaClientError as e:
        assert "actions" in str(e)
        assert e.status_code == 502
    else:
        raise AssertionError("missing actions should be rejected")


def test_action_must_be_object():
    client = make_client('{"speech":"bad action","actions":["drive"],"next_check_ms":300}')
    try:
        client.decide(make_snapshot())
    except OllamaClientError as e:
        assert "Each Ollama action" in str(e)
    else:
        raise AssertionError("non-object action should be rejected")


def test_payload_includes_camera_image():
    client = OllamaClient(
        enabled=True,
        model="test-model",
        request_log_enabled=False,
    )
    payload = client.build_payload(make_snapshot(), image_b64="abc123")
    assert payload["format"] == "json"
    assert payload["stream"] is False
    assert payload["messages"][1]["images"] == ["abc123"]


def test_transport_receives_timeout_and_goal_override():
    capture = {}
    client = make_client(
        '{"speech":"goal acknowledged","actions":[],"next_check_ms":500}',
        capture=capture,
    )
    client.decide(make_snapshot(), operator_goal="inspect the doorway")
    assert capture["timeout_seconds"] == 1.2
    user_payload = json.loads(capture["payload"]["messages"][1]["content"])
    assert user_payload["operator_goal"] == "inspect the doorway"
    assert "drive_tank" in user_payload["available_actions"]


def test_disabled_client_rejected():
    client = OllamaClient(
        enabled=False,
        model="test-model",
        request_log_enabled=False,
    )
    try:
        client.decide(make_snapshot())
    except OllamaClientError as e:
        assert "disabled" in str(e)
    else:
        raise AssertionError("disabled client should reject decisions")


def test_robot_state_records_success():
    state = RobotState()
    client = make_client('{"speech":"stored","actions":[],"next_check_ms":500}')
    client.robot_state = state
    client.decide(make_snapshot())
    snapshot = state.snapshot()
    assert snapshot["memory"]["last_ai_response"]["response"]["speech"] == "stored"
    assert snapshot["memory"]["last_ai_response"]["error"] is None


def test_request_log_records_success_and_omits_images():
    log_path = Path("phase4_test_ollama_requests.jsonl")
    if log_path.exists():
        log_path.unlink()

    try:
        client = make_client('{"speech":"logged","actions":[],"next_check_ms":500}')
        client.request_log_enabled = True
        client.request_log_path = log_path
        client.decide(make_snapshot(), image_b64="abc123")
        log = client.read_request_log(limit=5)
        assert log["count"] == 1
        entry = log["entries"][0]
        assert entry["status"] == "success"
        assert entry["proposal"]["speech"] == "logged"
        assert entry["request"]["messages"][1]["images"][0]["omitted"] is True
        assert entry["request"]["messages"][1]["images"][0]["base64_chars"] == 6
    finally:
        if log_path.exists():
            log_path.unlink()


TESTS = [
    test_decide_parses_strict_json,
    test_decide_recovers_wrapped_json,
    test_missing_actions_rejected,
    test_action_must_be_object,
    test_payload_includes_camera_image,
    test_transport_receives_timeout_and_goal_override,
    test_disabled_client_rejected,
    test_robot_state_records_success,
    test_request_log_records_success_and_omits_images,
]


def run_tests():
    results = []
    for test in TESTS:
        try:
            test()
            results.append({"name": test.__name__.replace("test_", ""), "status": "passed"})
        except Exception as e:
            results.append(
                {
                    "name": test.__name__.replace("test_", ""),
                    "status": "failed",
                    "error": repr(e),
                }
            )
    passed = sum(1 for result in results if result["status"] == "passed")
    failed = len(results) - passed
    return {
        "status": "complete",
        "passed": passed,
        "failed": failed,
        "results": results,
    }


def main():
    noisy_output = io.StringIO()
    with contextlib.redirect_stdout(noisy_output):
        result = run_tests()
        time.sleep(0.05)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
