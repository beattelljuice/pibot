#!/usr/bin/env python3
import contextlib
import io
import json
import time
from pathlib import Path

from memory_store import MemoryStore, MemoryStoreError
from ollama_client import OllamaClient
from phase3_test_harness import make_context
from phase4_test_harness import make_snapshot


def make_store(path, **overrides):
    config = {
        "enabled": True,
        "path": str(path),
        "max_memories": 50,
        "prompt_limit": 4,
        "max_text_chars": 300,
    }
    config.update(overrides)
    return MemoryStore(**config)


def test_path(name):
    return Path(f"phase6_test_{name}.json")


def cleanup_path(path):
    path = Path(path)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    for candidate in (path, tmp_path):
        if candidate.exists():
            candidate.unlink()


def test_memory_persists_across_reload():
    path = test_path("persist")
    cleanup_path(path)
    try:
        store = make_store(path)
        added = store.add(
            "Drive power below 35 is usually too weak to move the chassis.",
            memory_type="calibration",
            tags=["drive", "power"],
            confidence=0.9,
        )
        reloaded = make_store(path)
        memories = reloaded.list_memories()["memories"]
        assert len(memories) == 1
        assert memories[0]["id"] == added["memory"]["id"]
        assert "too weak" in memories[0]["text"]
    finally:
        cleanup_path(path)


def test_relevance_prefers_matching_memory():
    path = test_path("relevance")
    cleanup_path(path)
    try:
        store = make_store(path)
        store.add("The OLED display works on I2C address 0x3C.", memory_type="fact", tags=["oled"])
        store.add(
            "Drive power below 35 is usually too weak to move the chassis.",
            memory_type="calibration",
            tags=["drive", "power"],
            confidence=0.95,
        )
        relevant = store.relevant_memories("move forward with drive power", limit=1)
        assert relevant["memories"][0]["type"] == "calibration"
        assert "Drive power" in relevant["memories"][0]["text"]
    finally:
        cleanup_path(path)


def test_archive_hides_memory_by_default():
    path = test_path("archive")
    cleanup_path(path)
    try:
        store = make_store(path)
        memory_id = store.add("Temporary doorway observation.", memory_type="scene")["memory"]["id"]
        store.archive(memory_id)
        assert store.list_memories()["memories"] == []
        assert store.list_memories(include_archived=True)["memories"][0]["archived"] is True
    finally:
        cleanup_path(path)


def test_invalid_memory_rejected():
    path = test_path("invalid")
    cleanup_path(path)
    try:
        store = make_store(path)
        try:
            store.add("", memory_type="lesson")
        except MemoryStoreError as e:
            assert "text" in str(e)
        else:
            raise AssertionError("empty memory text should be rejected")
    finally:
        cleanup_path(path)


def test_safety_remember_action_allowed_during_estop():
    path = test_path("safety")
    cleanup_path(path)
    try:
        state, manager, executor, display, camera, safety = make_context()
        safety.memory_store = make_store(path)
        state.set_emergency_stop(True, "test")
        result = safety.propose(
            [
                {
                    "type": "remember",
                    "memory_type": "lesson",
                    "text": "The operator wants durable calibration notes preserved.",
                    "tags": ["operator", "memory"],
                    "confidence": 0.8,
                }
            ],
            source="ai",
        )
        first = result["results"][0]
        assert first["decision"] == "approved"
        assert first["executed"] is True
        assert first["result"]["memory"]["source"] == "ai"
        assert safety.memory_store.get_status()["count"] == 1
    finally:
        cleanup_path(path)


def test_ollama_prompt_includes_persistent_memories_and_remember_action():
    client = OllamaClient(enabled=True, model="test-model", request_log_enabled=False)
    snapshot = make_snapshot()
    snapshot["memory"]["persistent_memories"] = [
        {
            "id": 1,
            "type": "calibration",
            "source": "operator",
            "text": "Drive power below 35 is usually too weak to move.",
            "tags": ["drive", "power"],
            "confidence": 0.9,
            "created_at": "2026-06-09T00:00:00.000",
        }
    ]
    payload = client.build_planner_payload(snapshot, operator_goal="drive forward")
    user_payload = json.loads(payload["messages"][1]["content"])
    memories = user_payload["robot_snapshot"]["memory"]["persistent_memories"]
    assert user_payload["available_actions"].count("remember") == 1
    assert "remember" in user_payload["action_reference"]
    assert memories[0]["type"] == "calibration"
    assert "below 35" in memories[0]["text"]


TESTS = [
    test_memory_persists_across_reload,
    test_relevance_prefers_matching_memory,
    test_archive_hides_memory_by_default,
    test_invalid_memory_rejected,
    test_safety_remember_action_allowed_during_estop,
    test_ollama_prompt_includes_persistent_memories_and_remember_action,
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
