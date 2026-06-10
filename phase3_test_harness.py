#!/usr/bin/env python3
import contextlib
import io
import json
import sys
import time
import types


def install_gpio_stub() -> None:
    """Install a minimal RPi.GPIO stub before importing motor classes."""
    rpi = types.ModuleType("RPi")
    gpio = types.ModuleType("RPi.GPIO")
    gpio.BCM = 1
    gpio.BOARD = 2
    gpio.OUT = 3
    gpio.IN = 4
    gpio.HIGH = 1
    gpio.LOW = 0
    gpio.PUD_UP = 1
    gpio.PUD_DOWN = 2
    gpio.PUD_OFF = 0
    gpio.setmode = lambda *args, **kwargs: None
    gpio.setwarnings = lambda *args, **kwargs: None
    gpio.setup = lambda *args, **kwargs: None
    gpio.output = lambda *args, **kwargs: None
    gpio.input = lambda *args, **kwargs: 0
    gpio.cleanup = lambda *args, **kwargs: None

    class FakePwm:
        def __init__(self, *args, **kwargs):
            pass

        def start(self, *args, **kwargs):
            pass

        def ChangeDutyCycle(self, *args, **kwargs):
            pass

        def stop(self):
            pass

    gpio.PWM = FakePwm
    rpi.GPIO = gpio
    sys.modules["RPi"] = rpi
    sys.modules["RPi.GPIO"] = gpio


install_gpio_stub()

from action_executor import ActionExecutor
from motor import DCMotor, StepperMotor
from robot_state import RobotState
from safety_supervisor import SafetySupervisor


class FakeDCMotor(DCMotor):
    def __init__(self, name):
        self.name = name
        self.speed = 0
        self.direction = "stopped"
        self.power_history = []

    def set_power(self, power):
        self.power_history.append(power)
        self.speed = abs(power)
        self.direction = "forward" if power > 0 else "backward" if power < 0 else "stopped"

    def stop(self):
        self.set_power(0)

    def get_state(self):
        return {
            "name": self.name,
            "type": "DC",
            "speed": self.speed,
            "direction": self.direction,
        }


class FakeStepperMotor(StepperMotor):
    def __init__(self, name):
        self.name = name
        self.moves = []

    def step(self, steps, direction="forward"):
        self.moves.append((steps, direction))

    def stop(self):
        self.moves.append(("stop", None))

    def get_state(self):
        return {
            "name": self.name,
            "type": "Stepper",
            "stepping": False,
            "rpm": 0,
        }


class FakeManager:
    def __init__(self):
        self.motors = {
            "left_motor": FakeDCMotor("left_motor"),
            "right_motor": FakeDCMotor("right_motor"),
            "stepper_1": FakeStepperMotor("stepper_1"),
        }

    def get_motor(self, name):
        return self.motors.get(name)

    def list_motors(self):
        return {name: motor.get_state() for name, motor in self.motors.items()}


class FakeDisplay:
    available = True
    width = 128
    height = 64

    def __init__(self):
        self.actions = []

    def display_text(self, text, x=0, y=0, clear=True):
        self.actions.append(("text", text, x, y, clear))
        return {"status": "success", "action": "display_text"}

    def display_frame(self, payload):
        self.actions.append(("frame", payload))
        return {"status": "success", "action": "display_frame"}

    def get_status(self):
        return {"status": "success", "available": True}


class FakeCamera:
    available = True
    stale_after_ms = 2000

    def __init__(self):
        self.captures = 0

    def capture_jpeg(self):
        self.captures += 1
        return (
            b"jpeg",
            {
                "captured_at": "2026-06-09T00:00:00.000",
                "width": 640,
                "height": 480,
                "mime": "image/jpeg",
                "bytes": 4,
            },
        )

    def get_status(self):
        return {"status": "success", "available": True, "captures": self.captures}


def make_context():
    state = RobotState()
    manager = FakeManager()
    executor = ActionExecutor(manager, robot_state=state)
    display = FakeDisplay()
    camera = FakeCamera()
    safety = SafetySupervisor(
        robot_state=state,
        action_executor=executor,
        display_controller=display,
        camera_controller=camera,
        manual_enforcement=False,
        obstacle_enforcement=False,
        max_drive_power=100,
        max_action_ms=1500,
        max_stepper_steps=500,
    )
    return state, manager, executor, display, camera, safety


def first_result(response):
    return response["results"][0]


def test_ai_drive_rejected_in_manual_mode():
    state, manager, executor, display, camera, safety = make_context()
    result = first_result(
        safety.propose(
            [{"type": "drive_tank", "left_power": 25, "right_power": 25, "duration_ms": 300}],
            source="ai",
        )
    )
    assert result["decision"] == "rejected"
    assert not result["executed"]
    assert manager.motors["left_motor"].direction == "stopped"


def test_ai_drive_allowed_in_ai_mode():
    state, manager, executor, display, camera, safety = make_context()
    state.set_mode("ai")
    result = first_result(
        safety.propose(
            [{"type": "drive_tank", "left_power": 25, "right_power": 25, "duration_ms": 50}],
            source="ai",
        )
    )
    assert result["decision"] == "approved"
    assert result["executed"]
    assert manager.motors["left_motor"].power_history[-1] == 25
    executor.stop_all()


def test_ai_movement_rejected_in_paused():
    state, manager, executor, display, camera, safety = make_context()
    state.set_mode("paused")
    result = first_result(
        safety.propose(
            [{"type": "rotate", "power": 20, "direction": "left", "duration_ms": 100}],
            source="ai",
        )
    )
    assert result["decision"] == "rejected"
    assert not result["executed"]


def test_ai_movement_rejected_in_estop():
    state, manager, executor, display, camera, safety = make_context()
    state.set_emergency_stop(True, "test")
    result = first_result(
        safety.propose(
            [{"type": "stepper_move", "motor": "stepper_1", "steps": 10}],
            source="ai",
        )
    )
    assert result["decision"] == "rejected"
    assert not result["executed"]


def test_stop_all_allowed_in_any_mode():
    state, manager, executor, display, camera, safety = make_context()
    state.set_emergency_stop(True, "test")
    result = first_result(safety.propose([{"type": "stop_all"}], source="ai"))
    assert result["decision"] == "approved"
    assert result["executed"]
    assert result["result"]["action"] == "stop_all"


def test_drive_power_clamps_to_100():
    state, manager, executor, display, camera, safety = make_context()
    state.set_mode("ai")
    result = first_result(
        safety.propose(
            [{"type": "drive_tank", "left_power": 200, "right_power": -200, "duration_ms": 50}],
            source="ai",
        )
    )
    assert result["decision"] == "clamped"
    assert result["action"]["left_power"] == 100
    assert result["action"]["right_power"] == -100
    executor.stop_all()


def test_duration_clamps_to_1500_ms():
    state, manager, executor, display, camera, safety = make_context()
    state.set_mode("ai")
    result = first_result(
        safety.propose(
            [{"type": "drive_tank", "left_power": 10, "right_power": 10, "duration_ms": 9999}],
            source="ai",
        )
    )
    assert result["decision"] == "clamped"
    assert result["action"]["duration_ms"] == 1500
    executor.stop_all()


def test_stepper_move_clamps_to_500_steps():
    state, manager, executor, display, camera, safety = make_context()
    state.set_mode("ai")
    result = first_result(
        safety.propose(
            [{"type": "stepper_move", "motor": "stepper_1", "steps": 9999}],
            source="ai",
        )
    )
    assert result["decision"] == "clamped"
    assert result["action"]["steps"] == 500
    assert manager.motors["stepper_1"].moves[-1] == (500, "forward")


def test_oled_text_allowed_during_estop():
    state, manager, executor, display, camera, safety = make_context()
    state.set_emergency_stop(True, "test")
    result = first_result(
        safety.propose([{"type": "display_text", "text": "AI online"}], source="ai")
    )
    assert result["decision"] == "approved"
    assert result["executed"]
    assert display.actions[-1][0] == "text"


def test_oled_text_accepts_ai_aliases_and_string_coordinates():
    state, manager, executor, display, camera, safety = make_context()
    state.set_mode("ai")
    result = first_result(
        safety.propose(
            [
                {
                    "type": "display_text",
                    "message": "Alias text",
                    "x": "4",
                    "y": "8",
                    "clear": "false",
                }
            ],
            source="ai",
        )
    )
    assert result["decision"] == "approved"
    assert result["executed"]
    assert display.actions[-1] == ("text", "Alias text", 4, 8, False)


def test_oled_text_clamps_offscreen_coordinates():
    state, manager, executor, display, camera, safety = make_context()
    state.set_mode("ai")
    result = first_result(
        safety.propose(
            [{"type": "display_text", "text": "Visible", "x": 999, "y": 999}],
            source="ai",
        )
    )
    assert result["decision"] == "approved"
    assert result["executed"]
    assert display.actions[-1][2] == 127
    assert display.actions[-1][3] == 63


def test_oled_blank_text_rejected():
    state, manager, executor, display, camera, safety = make_context()
    state.set_mode("ai")
    result = first_result(safety.propose([{"type": "display_text", "message": ""}], source="ai"))
    assert result["decision"] == "rejected"
    assert not result["executed"]


def test_camera_capture_allowed_during_paused():
    state, manager, executor, display, camera, safety = make_context()
    state.set_mode("paused")
    result = first_result(safety.propose([{"type": "camera_capture"}], source="ai"))
    assert result["decision"] == "approved"
    assert result["executed"]
    assert camera.captures == 1


def test_unknown_action_type_rejected():
    state, manager, executor, display, camera, safety = make_context()
    state.set_mode("ai")
    result = first_result(safety.propose([{"type": "teleport"}], source="ai"))
    assert result["decision"] == "rejected"
    assert not result["executed"]


def test_malformed_action_rejected():
    state, manager, executor, display, camera, safety = make_context()
    state.set_mode("ai")
    result = first_result(safety.propose(["not an object"], source="ai"))
    assert result["decision"] == "rejected"
    assert not result["executed"]


TESTS = [
    test_ai_drive_rejected_in_manual_mode,
    test_ai_drive_allowed_in_ai_mode,
    test_ai_movement_rejected_in_paused,
    test_ai_movement_rejected_in_estop,
    test_stop_all_allowed_in_any_mode,
    test_drive_power_clamps_to_100,
    test_duration_clamps_to_1500_ms,
    test_stepper_move_clamps_to_500_steps,
    test_oled_text_allowed_during_estop,
    test_oled_text_accepts_ai_aliases_and_string_coordinates,
    test_oled_text_clamps_offscreen_coordinates,
    test_oled_blank_text_rejected,
    test_camera_capture_allowed_during_paused,
    test_unknown_action_type_rejected,
    test_malformed_action_rejected,
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
