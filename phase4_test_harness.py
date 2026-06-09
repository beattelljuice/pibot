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


def make_client(contents, capture=None, two_stage=True):
    if isinstance(contents, list):
        response_contents = list(contents)
    else:
        response_contents = [contents]
    call_index = {"value": 0}

    def transport(payload, timeout_seconds):
        index = min(call_index["value"], len(response_contents) - 1)
        call_index["value"] += 1
        if capture is not None:
            capture.setdefault("calls", []).append(
                {"payload": payload, "timeout_seconds": timeout_seconds}
            )
            capture["payload"] = payload
            capture["timeout_seconds"] = timeout_seconds
        return {"message": {"content": response_contents[index]}}

    return OllamaClient(
        enabled=True,
        url="http://ollama.test:11434",
        model="planner-test-model",
        two_stage=two_stage,
        translator_model="translator-test-model",
        timeout_ms=1200,
        translator_timeout_ms=800,
        request_log_enabled=False,
        transport=transport,
    )


def test_decide_parses_strict_json():
    client = make_client(
        [
            "Show the word clear on the OLED display and do not move.",
            json.dumps(
                {
                    "speech": "I see a clear path.",
                    "actions": [{"type": "display_text", "text": "clear"}],
                    "next_check_ms": 250,
                }
            ),
        ]
    )
    result = client.decide(make_snapshot())
    assert result["status"] == "success"
    assert result["mode"] == "two_stage"
    assert result["intent"] == "Show the word clear on the OLED display and do not move."
    assert result["proposal"]["speech"] == "I see a clear path."
    assert result["proposal"]["actions"][0]["type"] == "display_text"
    assert result["proposal"]["next_check_ms"] == 250


def test_decide_recovers_wrapped_json():
    client = make_client(
        [
            "Hold position.",
            'Here is the decision: {"speech":"holding","actions":[],"next_check_ms":300}',
        ]
    )
    result = client.decide(make_snapshot())
    assert result["proposal"]["speech"] == "holding"
    assert result["proposal"]["actions"] == []
    assert result["proposal"]["next_check_ms"] == 300


def test_missing_actions_rejected():
    client = make_client(
        [
            "Hold position.",
            '{"speech":"missing action list","next_check_ms":300}',
        ]
    )
    try:
        client.decide(make_snapshot())
    except OllamaClientError as e:
        assert "actions" in str(e)
        assert e.status_code == 502
    else:
        raise AssertionError("missing actions should be rejected")


def test_action_must_be_object():
    client = make_client(
        [
            "Drive forward.",
            '{"speech":"bad action","actions":["drive"],"next_check_ms":300}',
        ]
    )
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
    payload = client.build_planner_payload(make_snapshot(), image_b64="abc123")
    assert "format" not in payload
    assert payload["stream"] is False
    assert payload["messages"][1]["images"] == ["abc123"]


def test_transport_receives_timeout_and_goal_override():
    capture = {}
    client = make_client(
        [
            "Acknowledge the goal without moving.",
            '{"speech":"goal acknowledged","actions":[],"next_check_ms":500}',
        ],
        capture=capture,
    )
    client.decide(make_snapshot(), operator_goal="inspect the doorway")
    assert len(capture["calls"]) == 2
    assert capture["calls"][0]["timeout_seconds"] == 1.2
    assert capture["calls"][1]["timeout_seconds"] == 0.8
    user_payload = json.loads(capture["calls"][0]["payload"]["messages"][1]["content"])
    assert user_payload["operator_goal"] == "inspect the doorway"
    assert "drive_tank" in user_payload["available_actions"]


def test_model_snapshot_omits_internal_ollama_status():
    client = OllamaClient(
        enabled=True,
        model="test-model",
        request_log_enabled=False,
    )
    snapshot = make_snapshot()
    snapshot["robot"]["ollama"] = {"execute_actions": False, "last_error": "old error"}
    payload = client.build_payload(snapshot)
    user_payload = json.loads(payload["messages"][1]["content"])
    assert "ollama" not in user_payload["robot_snapshot"]["robot"]


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
    client = make_client(
        [
            "Store a no-op intent.",
            '{"speech":"stored","actions":[],"next_check_ms":500}',
        ]
    )
    client.robot_state = state
    client.decide(make_snapshot())
    snapshot = state.snapshot()
    response = snapshot["memory"]["last_ai_response"]["response"]
    assert response["mode"] == "two_stage"
    assert response["proposal"]["speech"] == "stored"
    assert snapshot["memory"]["last_ai_response"]["error"] is None


def test_retry_translation_uses_cached_intent_without_planner():
    capture = {}
    client = make_client(
        [
            "Write retry on the OLED.",
            '{"speech":"bad json","actions":',
            '{"speech":"retry ok","actions":[{"type":"display_text","text":"retry"}],"next_check_ms":500}',
        ],
        capture=capture,
    )
    try:
        client.decide(make_snapshot())
    except OllamaClientError:
        pass
    else:
        raise AssertionError("bad first translation should be rejected")

    retry = client.translate_last_intent()
    assert retry["source"] == "retry"
    assert retry["intent"] == "Write retry on the OLED."
    assert retry["proposal"]["actions"][0]["text"] == "retry"
    assert len(capture["calls"]) == 3
    assert capture["calls"][2]["payload"]["model"] == "translator-test-model"


def test_request_log_records_success_and_omits_images():
    log_path = Path("phase4_test_ollama_requests.jsonl")
    if log_path.exists():
        log_path.unlink()

    try:
        client = make_client(
            [
                "Log a no-op intent.",
                '{"speech":"logged","actions":[],"next_check_ms":500}',
            ]
        )
        client.request_log_enabled = True
        client.request_log_path = log_path
        client.decide(make_snapshot(), image_b64="abc123")
        log = client.read_request_log(limit=5)
        assert log["count"] == 2
        planner_entry = log["entries"][0]
        translator_entry = log["entries"][1]
        assert planner_entry["event"] == "ollama_planner"
        assert planner_entry["status"] == "success"
        assert planner_entry["intent"] == "Log a no-op intent."
        assert planner_entry["request"]["messages"][1]["images"][0]["omitted"] is True
        assert planner_entry["request"]["messages"][1]["images"][0]["base64_chars"] == 6
        assert translator_entry["event"] == "ollama_translator"
        assert translator_entry["proposal"]["speech"] == "logged"
    finally:
        if log_path.exists():
            log_path.unlink()


def test_request_log_records_raw_response_on_parse_error():
    log_path = Path("phase4_test_ollama_error.jsonl")
    if log_path.exists():
        log_path.unlink()

    try:
        client = make_client(
            [
                "Translate this into bad JSON first.",
                '{"speech" "bad json","actions":[],"next_check_ms":500}',
            ]
        )
        client.request_log_enabled = True
        client.request_log_path = log_path
        try:
            client.decide(make_snapshot())
        except OllamaClientError:
            pass
        else:
            raise AssertionError("bad model JSON should be rejected")

        log = client.read_request_log(limit=5)
        assert log["count"] == 2
        entry = log["entries"][1]
        assert entry["event"] == "ollama_translator"
        assert entry["status"] == "error"
        assert entry["response"]["message"]["content"].startswith('{"speech" "bad json"')
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
    test_model_snapshot_omits_internal_ollama_status,
    test_disabled_client_rejected,
    test_robot_state_records_success,
    test_retry_translation_uses_cached_intent_without_planner,
    test_request_log_records_success_and_omits_images,
    test_request_log_records_raw_response_on_parse_error,
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
