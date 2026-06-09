#!/usr/bin/env python3
import contextlib
import io
import json
import time

from phase3_test_harness import make_context
from ai_loop import AILoopController, AILoopError
from ollama_client import OllamaClientError


class FakeOllama:
    enabled = True
    include_camera = False
    execute_actions = False

    def __init__(self, actions=None, fail=False):
        self.actions = actions if actions is not None else []
        self.fail = fail
        self.calls = []

    def decide(self, snapshot, operator_goal=None, image_b64=None):
        self.calls.append(
            {
                "snapshot": snapshot,
                "operator_goal": operator_goal,
                "image_b64": image_b64,
            }
        )
        if self.fail:
            raise OllamaClientError("fake ollama failed")
        return {
            "status": "success",
            "mode": "two_stage",
            "source": "fake",
            "duration_ms": 1,
            "intent": "fake intent",
            "proposal": {
                "speech": "fake speech",
                "actions": self.actions,
                "next_check_ms": 100,
            },
        }

    def get_status(self):
        return {
            "status": "success",
            "enabled": True,
            "last_error": None,
            "calls": len(self.calls),
        }


def make_loop(actions=None, fail=False, include_camera=False, execute_actions=True):
    state, manager, executor, display, camera, safety = make_context()
    ollama = FakeOllama(actions=actions, fail=fail)
    camera_calls = []

    def snapshot_provider():
        snapshot = state.snapshot(
            motor_states=manager.list_motors(),
            action_status=executor.get_status(),
        )
        snapshot["robot"]["safety"] = safety.get_status()
        return snapshot

    def camera_provider():
        camera_calls.append("capture")
        return (
            "fake-image",
            {
                "captured_at": "2026-06-09T00:00:00.000",
                "width": 640,
                "height": 480,
                "mime": "image/jpeg",
                "bytes": 10,
            },
        )

    loop = AILoopController(
        robot_state=state,
        action_executor=executor,
        safety_supervisor=safety,
        ollama_client=ollama,
        snapshot_provider=snapshot_provider,
        camera_frame_provider=camera_provider,
        decision_interval_ms=100,
        idle_interval_ms=10,
        error_backoff_ms=10,
        include_camera=include_camera,
        execute_actions=execute_actions,
        require_ai_mode=True,
        stop_on_error=True,
        max_consecutive_errors=2,
    )
    return state, manager, executor, display, camera, safety, ollama, camera_calls, loop


def test_start_requires_goal():
    state, manager, executor, display, camera, safety, ollama, camera_calls, loop = make_loop()
    state.set_mode("ai")
    try:
        loop.start()
    except AILoopError as e:
        assert "goal" in str(e)
    else:
        raise AssertionError("AI loop should require a goal")


def test_start_can_set_ai_mode():
    state, manager, executor, display, camera, safety, ollama, camera_calls, loop = make_loop()
    result = loop.start(goal="display hello", set_ai_mode=True)
    assert result["running"] is True
    assert state.get_status()["mode"] == "ai"
    loop.stop()


def test_run_once_executes_safety_actions():
    actions = [{"type": "display_text", "text": "AI loop"}]
    state, manager, executor, display, camera, safety, ollama, camera_calls, loop = make_loop(
        actions=actions
    )
    state.set_mode("ai")
    state.set_goal("write AI loop on the OLED")
    wait_ms = loop._run_once()
    status = loop.get_status()
    assert wait_ms == 100
    assert len(ollama.calls) == 1
    assert status["last_safety_result"]["results"][0]["executed"] is True
    assert display.actions[-1][1] == "AI loop"


def test_manual_mode_waits_without_model_call():
    actions = [{"type": "display_text", "text": "should not run"}]
    state, manager, executor, display, camera, safety, ollama, camera_calls, loop = make_loop(
        actions=actions
    )
    state.set_goal("do something")
    wait_ms = loop._run_once()
    assert wait_ms == 10
    assert len(ollama.calls) == 0
    assert "waiting for ai mode" in loop.get_status()["last_error"]


def test_include_camera_passes_image_to_ollama():
    state, manager, executor, display, camera, safety, ollama, camera_calls, loop = make_loop(
        include_camera=True,
        execute_actions=False,
    )
    state.set_mode("ai")
    state.set_goal("look around")
    loop._run_once()
    assert camera_calls == ["capture"]
    assert ollama.calls[0]["image_b64"] == "fake-image"


def test_repeated_errors_stop_loop():
    state, manager, executor, display, camera, safety, ollama, camera_calls, loop = make_loop(
        fail=True
    )
    state.set_mode("ai")
    state.set_goal("try and fail")
    loop._run_once()
    loop._run_once()
    status = loop.get_status()
    assert status["consecutive_errors"] == 2
    assert status["running"] is False
    assert "fake ollama failed" in status["last_error"]


TESTS = [
    test_start_requires_goal,
    test_start_can_set_ai_mode,
    test_run_once_executes_safety_actions,
    test_manual_mode_waits_without_model_call,
    test_include_camera_passes_image_to_ollama,
    test_repeated_errors_stop_loop,
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
