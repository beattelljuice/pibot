from flask import Flask, Response, jsonify, request, send_file
from action_executor import ActionExecutor, ActionExecutorError
from base64 import b64encode
from camera_controller import CameraController, CameraControllerError
from display_controller import DisplayController, DisplayControllerError
from motor import DCMotor, StepperMotor
from motor_manager import MotorManager
from ollama_client import OllamaClient, OllamaClientError
from robot_state import RobotState, RobotStateError
from safety_supervisor import SafetySupervisor, SafetySupervisorError
from datetime import datetime


def api_log(msg: str):
    """Log API message with timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{timestamp}] [API] {msg}")


def create_api(
    motor_manager: MotorManager,
    action_executor: ActionExecutor | None = None,
    robot_state: RobotState | None = None,
    display_controller: DisplayController | None = None,
    camera_controller: CameraController | None = None,
    safety_supervisor: SafetySupervisor | None = None,
    ollama_client: OllamaClient | None = None,
) -> Flask:
    """Create Flask API application."""
    app = Flask(__name__, static_folder='.', static_url_path='')
    if robot_state is None:
        robot_state = action_executor.robot_state if action_executor else None
    if robot_state is None:
        robot_state = RobotState()
    if action_executor is None:
        action_executor = ActionExecutor(motor_manager, robot_state=robot_state)
    elif action_executor.robot_state is None:
        action_executor.robot_state = robot_state
    if display_controller is None:
        display_controller = DisplayController(enabled=False)
    if camera_controller is None:
        camera_controller = CameraController(enabled=False)
    if safety_supervisor is None:
        safety_supervisor = SafetySupervisor(
            robot_state=robot_state,
            action_executor=action_executor,
            display_controller=display_controller,
            camera_controller=camera_controller,
        )
    if ollama_client is None:
        ollama_client = OllamaClient(enabled=False, robot_state=robot_state)
    api_log("Creating Flask API application")

    def get_json_body() -> dict:
        data = request.get_json(silent=True)
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ActionExecutorError("Request body must be a JSON object")
        return data

    def record_api_action(action: dict, result: str = "completed") -> None:
        try:
            robot_state.record_action(action, source="manual_api", result=result)
        except Exception as e:
            api_log(f"State action record failed: {e}")

    def get_robot_snapshot() -> dict:
        snapshot = robot_state.snapshot(
            motor_states=motor_manager.list_motors(),
            action_status=action_executor.get_status(),
        )
        snapshot["robot"]["display"] = display_controller.get_status()
        snapshot["robot"]["camera"] = camera_controller.get_status()
        snapshot["robot"]["safety"] = safety_supervisor.get_status()
        snapshot["robot"]["ollama"] = ollama_client.get_status()
        return snapshot

    def display_error_response(error: DisplayControllerError):
        status_code = 503 if not display_controller.available else 400
        api_log(f"Display error: {error}")
        return jsonify({"error": str(error), "display": display_controller.get_status()}), status_code

    def camera_error_response(error: CameraControllerError):
        status_code = 503 if not camera_controller.available else 400
        api_log(f"Camera error: {error}")
        return jsonify({"error": str(error), "camera": camera_controller.get_status()}), status_code

    def record_camera_capture(meta: dict) -> None:
        try:
            robot_state.update_sensor(
                "camera_snapshot",
                {
                    "captured_at": meta["captured_at"],
                    "width": meta["width"],
                    "height": meta["height"],
                    "mime": meta["mime"],
                    "bytes": meta["bytes"],
                    "snapshot_url": "/camera/snapshot.jpg",
                },
                camera_controller.stale_after_ms,
            )
        except Exception as e:
            api_log(f"Camera sensor state update failed: {e}")

    def record_ai_error(error: str) -> None:
        try:
            robot_state.record_ai_response(None, error=error)
        except Exception as e:
            api_log(f"AI error state update failed: {e}")

    @app.route('/')
    def serve_ui():
        """Serve the web UI."""
        api_log("GET / - Serving UI")
        return send_file('index.html')

    @app.route("/robot/state", methods=["GET"])
    def get_robot_state():
        """Get the complete robot state snapshot."""
        api_log("GET /robot/state - Getting robot snapshot")
        return jsonify(get_robot_snapshot())

    @app.route("/robot/status", methods=["GET"])
    def get_robot_status():
        """Get compact robot state status."""
        api_log("GET /robot/status - Getting robot status")
        return jsonify(robot_state.get_status())

    @app.route("/robot/goal", methods=["POST"])
    def set_robot_goal():
        """Set current operator goal."""
        api_log(f"POST /robot/goal - Request body: {request.get_json(silent=True)}")
        try:
            data = get_json_body()
            goal = data.get("goal", data.get("operator_request", ""))
            return jsonify(robot_state.set_goal(goal))
        except (ActionExecutorError, RobotStateError) as e:
            api_log(f"State error: {e}")
            return jsonify({"error": str(e)}), 400

    @app.route("/robot/mode", methods=["POST"])
    def set_robot_mode():
        """Set robot mode: manual, ai, paused, or estop."""
        api_log(f"POST /robot/mode - Request body: {request.get_json(silent=True)}")
        try:
            data = get_json_body()
            mode = data.get("mode")
            result = robot_state.set_mode(mode)
            if result["mode"] == "estop":
                action_executor.stop_all(source="estop")
            return jsonify(result)
        except (ActionExecutorError, RobotStateError) as e:
            api_log(f"State error: {e}")
            return jsonify({"error": str(e)}), 400

    @app.route("/robot/estop", methods=["POST"])
    def set_robot_estop():
        """Activate emergency stop and stop all motors."""
        api_log(f"POST /robot/estop - Request body: {request.get_json(silent=True)}")
        try:
            data = get_json_body()
            reason = data.get("reason", "manual emergency stop")
            state_result = robot_state.set_emergency_stop(True, reason)
            stop_result = action_executor.stop_all(source="estop")
            return jsonify(
                {
                    "status": "success",
                    "robot": state_result,
                    "stop": stop_result,
                }
            )
        except (ActionExecutorError, RobotStateError) as e:
            api_log(f"State error: {e}")
            return jsonify({"error": str(e)}), 400

    @app.route("/robot/estop/clear", methods=["POST"])
    def clear_robot_estop():
        """Clear emergency stop and leave robot paused."""
        api_log("POST /robot/estop/clear - Clearing emergency stop")
        try:
            return jsonify(robot_state.set_emergency_stop(False))
        except RobotStateError as e:
            api_log(f"State error: {e}")
            return jsonify({"error": str(e)}), 400

    @app.route("/robot/sensors/<name>", methods=["POST"])
    def update_robot_sensor(name: str):
        """Update a sensor reading for the robot state snapshot."""
        api_log(
            f"POST /robot/sensors/{name} - Request body: {request.get_json(silent=True)}"
        )
        try:
            data = get_json_body()
            if "value" not in data:
                return jsonify({"error": "Missing 'value' parameter"}), 400
            result = robot_state.update_sensor(
                name,
                data["value"],
                data.get("stale_after_ms"),
                data.get("metadata"),
            )
            return jsonify(result)
        except (ActionExecutorError, RobotStateError) as e:
            api_log(f"State error: {e}")
            return jsonify({"error": str(e)}), 400

    @app.route("/robot/sensors/<name>", methods=["DELETE"])
    def clear_robot_sensor(name: str):
        """Clear a sensor reading from robot state."""
        api_log(f"DELETE /robot/sensors/{name} - Clearing sensor")
        try:
            return jsonify(robot_state.clear_sensor(name))
        except RobotStateError as e:
            api_log(f"State error: {e}")
            return jsonify({"error": str(e)}), 400

    @app.route("/display/status", methods=["GET"])
    def get_display_status():
        """Get OLED display status."""
        api_log("GET /display/status - Getting display status")
        return jsonify(display_controller.get_status())

    @app.route("/display/clear", methods=["POST"])
    def clear_display():
        """Clear the OLED display."""
        api_log("POST /display/clear - Clearing display")
        try:
            result = display_controller.clear()
            record_api_action({"type": "display_clear"})
            return jsonify(result)
        except DisplayControllerError as e:
            return display_error_response(e)

    @app.route("/display/text", methods=["POST"])
    def display_text():
        """Render text on the OLED display."""
        api_log(f"POST /display/text - Request body: {request.get_json(silent=True)}")
        try:
            data = get_json_body()
            result = display_controller.display_text(
                data.get("text", ""),
                data.get("x", 0),
                data.get("y", 0),
                data.get("clear", True),
            )
            record_api_action(
                {
                    "type": "display_text",
                    "text": data.get("text", ""),
                    "x": data.get("x", 0),
                    "y": data.get("y", 0),
                }
            )
            return jsonify(result)
        except (ActionExecutorError, DisplayControllerError) as e:
            if isinstance(e, DisplayControllerError):
                return display_error_response(e)
            return jsonify({"error": str(e)}), 400

    @app.route("/display/frame", methods=["POST"])
    def display_frame():
        """Render a complete 128x64 1-bit frame on the OLED display."""
        data = request.get_json(silent=True)
        frame_keys = list(data.keys()) if isinstance(data, dict) else []
        api_log(f"POST /display/frame - Frame keys: {frame_keys}")
        try:
            data = get_json_body()
            result = display_controller.display_frame(data)
            record_api_action(
                {
                    "type": "display_frame",
                    "format": result["display"]["last_action"]["details"].get("format"),
                    "width": result["display"]["width"],
                    "height": result["display"]["height"],
                }
            )
            return jsonify(result)
        except (ActionExecutorError, DisplayControllerError) as e:
            if isinstance(e, DisplayControllerError):
                return display_error_response(e)
            return jsonify({"error": str(e)}), 400

    @app.route("/camera/status", methods=["GET"])
    def get_camera_status():
        """Get USB camera status."""
        api_log("GET /camera/status - Getting camera status")
        return jsonify(camera_controller.get_status())

    @app.route("/camera/snapshot.jpg", methods=["GET"])
    def get_camera_snapshot():
        """Capture and return a JPEG snapshot from the USB camera."""
        api_log("GET /camera/snapshot.jpg - Capturing JPEG snapshot")
        try:
            jpeg_bytes, meta = camera_controller.capture_jpeg()
            record_camera_capture(meta)
            record_api_action(
                {
                    "type": "camera_snapshot",
                    "width": meta["width"],
                    "height": meta["height"],
                    "bytes": meta["bytes"],
                }
            )
            return Response(
                jpeg_bytes,
                mimetype="image/jpeg",
                headers={"Cache-Control": "no-store"},
            )
        except CameraControllerError as e:
            return camera_error_response(e)

    @app.route("/camera/capture", methods=["POST"])
    def capture_camera_json():
        """Capture a JPEG frame and return metadata plus base64 image data."""
        api_log("POST /camera/capture - Capturing JSON camera frame")
        try:
            jpeg_bytes, meta = camera_controller.capture_jpeg()
            record_camera_capture(meta)
            record_api_action(
                {
                    "type": "camera_capture",
                    "width": meta["width"],
                    "height": meta["height"],
                    "bytes": meta["bytes"],
                }
            )
            return jsonify(
                {
                    "status": "success",
                    "camera": camera_controller.get_status(),
                    "frame": {
                        **meta,
                        "data": b64encode(jpeg_bytes).decode("ascii"),
                        "encoding": "base64",
                    },
                }
            )
        except CameraControllerError as e:
            return camera_error_response(e)

    @app.route("/ollama/status", methods=["GET"])
    def get_ollama_status():
        """Get Ollama one-shot brain status."""
        api_log("GET /ollama/status - Getting Ollama status")
        return jsonify(ollama_client.get_status())

    @app.route("/ollama/decide", methods=["POST"])
    def ollama_decide():
        """Ask Ollama for one action proposal and optionally execute it safely."""
        api_log(f"POST /ollama/decide - Request body: {request.get_json(silent=True)}")
        camera_frame = None
        try:
            data = get_json_body()
            include_camera = bool(data.get("include_camera", ollama_client.include_camera))
            execute = bool(data.get("execute", ollama_client.execute_actions))
            operator_goal = data.get("goal")
            if operator_goal is not None and not isinstance(operator_goal, str):
                return jsonify({"error": "goal must be a string"}), 400

            image_b64 = None
            if include_camera:
                jpeg_bytes, meta = camera_controller.capture_jpeg()
                image_b64 = b64encode(jpeg_bytes).decode("ascii")
                record_camera_capture(meta)
                camera_frame = {
                    **meta,
                    "encoding": "base64",
                    "included_in_prompt": True,
                }

            decision = ollama_client.decide(
                get_robot_snapshot(),
                operator_goal=operator_goal,
                image_b64=image_b64,
            )
            response = {
                "status": "success",
                "execute": execute,
                "include_camera": include_camera,
                "camera_frame": camera_frame,
                "ollama": ollama_client.get_status(),
                "decision": decision,
            }
            if execute:
                response["safety"] = safety_supervisor.propose(
                    decision["proposal"]["actions"],
                    source="ai",
                )
            return jsonify(response)
        except CameraControllerError as e:
            record_ai_error(str(e))
            return camera_error_response(e)
        except ActionExecutorError as e:
            record_ai_error(str(e))
            return jsonify({"error": str(e)}), 400
        except OllamaClientError as e:
            api_log(f"Ollama error: {e}")
            record_ai_error(str(e))
            return jsonify({"error": str(e), "ollama": ollama_client.get_status()}), e.status_code
        except SafetySupervisorError as e:
            api_log(f"Ollama safety execution error: {e}")
            record_ai_error(str(e))
            return jsonify({"error": str(e)}), 400

    @app.route("/motors", methods=["GET"])
    def list_motors():
        """List all motors and their current state."""
        api_log("GET /motors - Listing all motors")
        states = motor_manager.list_motors()
        api_log(f"Returning {len(states)} motors")
        return jsonify(states)

    @app.route("/motors/<name>", methods=["GET"])
    def get_motor(name: str):
        """Get state of specific motor."""
        api_log(f"GET /motors/{name} - Getting motor state")
        motor = motor_manager.get_motor(name)
        if not motor:
            api_log(f"✗ Motor '{name}' not found")
            return jsonify({"error": f"Motor '{name}' not found"}), 404
        return jsonify(motor.get_state())

    @app.route("/motors/<name>/power", methods=["POST"])
    def set_motor_power(name: str):
        """Set DC motor power (-100 to 100). Positive=forward, Negative=backward, 0=stop."""
        api_log(f"POST /motors/{name}/power - Request body: {request.get_json()}")
        motor = motor_manager.get_motor(name)
        if not motor:
            api_log(f"✗ Motor '{name}' not found")
            return jsonify({"error": f"Motor '{name}' not found"}), 404

        if not isinstance(motor, DCMotor):
            api_log(f"✗ Motor '{name}' is not a DC motor")
            return jsonify({"error": f"'{name}' is not a DC motor"}), 400

        data = request.get_json()
        if "power" not in data:
            api_log(f"✗ Missing 'power' parameter")
            return jsonify({"error": "Missing 'power' parameter"}), 400

        power = data["power"]
        if not isinstance(power, (int, float)) or power < -100 or power > 100:
            api_log(f"✗ Invalid power value: {power}")
            return jsonify({"error": "Power must be -100 to 100"}), 400

        api_log(f"Setting motor '{name}' power to {power}%")
        motor.set_power(int(power))
        record_api_action(
            {
                "type": "motor_power",
                "motor": name,
                "power": int(power),
            }
        )
        api_log(f"✓ Power set successfully")
        return jsonify({"status": "success", "motor": name, "power": motor.speed if motor.direction != "stopped" else 0, "direction": motor.direction})

    @app.route("/motors/<name>/speed", methods=["POST"])
    def set_stepper_speed(name: str):
        """Set stepper motor speed in RPM."""
        api_log(f"POST /motors/{name}/speed - Request body: {request.get_json()}")
        motor = motor_manager.get_motor(name)
        if not motor:
            api_log(f"✗ Motor '{name}' not found")
            return jsonify({"error": f"Motor '{name}' not found"}), 404

        if not isinstance(motor, StepperMotor):
            api_log(f"✗ Motor '{name}' is not a stepper motor")
            return jsonify({"error": f"'{name}' is not a stepper motor"}), 400

        data = request.get_json()
        if "rpm" not in data:
            api_log(f"✗ Missing 'rpm' parameter")
            return jsonify({"error": "Missing 'rpm' parameter for stepper motor"}), 400

        rpm = data["rpm"]
        if not isinstance(rpm, (int, float)) or rpm < 0:
            api_log(f"✗ Invalid RPM value: {rpm}")
            return jsonify({"error": "RPM must be non-negative"}), 400

        api_log(f"Setting stepper motor '{name}' speed to {rpm} RPM")
        motor.set_speed(float(rpm))
        record_api_action(
            {
                "type": "stepper_speed",
                "motor": name,
                "rpm": float(rpm),
            }
        )
        api_log(f"✓ RPM set successfully")
        return jsonify({"status": "success", "motor": name, "rpm": rpm})

    @app.route("/motors/<name>/step", methods=["POST"])
    def step_motor(name: str):
        """Command stepper motor."""
        api_log(f"POST /motors/{name}/step - Request body: {request.get_json()}")
        motor = motor_manager.get_motor(name)
        if not motor:
            api_log(f"✗ Motor '{name}' not found")
            return jsonify({"error": f"Motor '{name}' not found"}), 404
        if not isinstance(motor, StepperMotor):
            api_log(f"✗ Motor '{name}' is not a stepper motor")
            return jsonify({"error": f"'{name}' is not a stepper motor"}), 400

        data = request.get_json()
        steps = data.get("steps", 100)
        direction = data.get("direction", "forward").lower()

        if not isinstance(steps, int) or steps < 1:
            api_log(f"✗ Invalid steps value: {steps}")
            return jsonify({"error": "Steps must be a positive integer"}), 400
        if direction not in ["forward", "backward"]:
            api_log(f"✗ Invalid direction: {direction}")
            return jsonify({"error": "Direction must be 'forward' or 'backward'"}), 400

        api_log(f"Commanding stepper '{name}' to move {steps} steps {direction}")
        motor.step(steps, direction)
        record_api_action(
            {
                "type": "stepper_move",
                "motor": name,
                "steps": steps,
                "direction": direction,
            },
            "started",
        )
        api_log(f"✓ Step command issued successfully")
        return jsonify(
            {
                "status": "success",
                "motor": name,
                "steps": steps,
                "direction": direction,
            }
        )

    @app.route("/motors/<name>/stop", methods=["POST"])
    def stop_motor(name: str):
        """Stop motor."""
        api_log(f"POST /motors/{name}/stop")
        motor = motor_manager.get_motor(name)
        if not motor:
            api_log(f"✗ Motor '{name}' not found")
            return jsonify({"error": f"Motor '{name}' not found"}), 404

        api_log(f"Stopping motor '{name}'")
        motor.stop()
        record_api_action({"type": "motor_stop", "motor": name})
        api_log(f"✓ Motor stopped successfully")
        return jsonify({"status": "success", "motor": name, "stopped": True})

    @app.route("/safety/status", methods=["GET"])
    def get_safety_status():
        """Get safety supervisor status."""
        api_log("GET /safety/status - Getting safety status")
        return jsonify(safety_supervisor.get_status())

    @app.route("/actions/propose", methods=["POST"])
    def propose_actions():
        """Submit proposed actions through the safety supervisor."""
        api_log(f"POST /actions/propose - Request body: {request.get_json(silent=True)}")
        try:
            data = get_json_body()
            source = data.get("source", "ai")
            actions = data.get("actions")
            if actions is None and "action" in data:
                actions = data["action"]
            result = safety_supervisor.propose(actions, source=source)
            return jsonify(result)
        except (ActionExecutorError, SafetySupervisorError) as e:
            api_log(f"Safety action error: {e}")
            return jsonify({"error": str(e)}), 400

    @app.route("/actions/status", methods=["GET"])
    def get_action_status():
        """Get action executor status."""
        api_log("GET /actions/status - Getting action executor status")
        return jsonify(action_executor.get_status())

    @app.route("/actions/stop", methods=["POST"])
    @app.route("/actions/stop_all", methods=["POST"])
    def action_stop_all():
        """Stop every registered motor and clear any active timed action."""
        api_log("POST /actions/stop_all - Stopping all motors")
        try:
            return jsonify(action_executor.stop_all(source="manual_api"))
        except ActionExecutorError as e:
            api_log(f"Action error: {e}")
            return jsonify({"error": str(e)}), 400

    @app.route("/actions/drive_tank", methods=["POST"])
    def action_drive_tank():
        """Drive left and right DC motors for a bounded duration."""
        api_log(
            f"POST /actions/drive_tank - Request body: {request.get_json(silent=True)}"
        )
        try:
            data = get_json_body()
            result = action_executor.drive_tank(
                data.get("left_power"),
                data.get("right_power"),
                data.get("duration_ms"),
                source="manual_api",
            )
            return jsonify(result)
        except ActionExecutorError as e:
            api_log(f"Action error: {e}")
            return jsonify({"error": str(e)}), 400

    @app.route("/actions/rotate", methods=["POST"])
    def action_rotate():
        """Rotate chassis in place for a bounded duration."""
        api_log(
            f"POST /actions/rotate - Request body: {request.get_json(silent=True)}"
        )
        try:
            data = get_json_body()
            result = action_executor.rotate(
                data.get("power"),
                data.get("direction"),
                data.get("duration_ms"),
                source="manual_api",
            )
            return jsonify(result)
        except ActionExecutorError as e:
            api_log(f"Action error: {e}")
            return jsonify({"error": str(e)}), 400

    @app.route("/actions/stepper_move", methods=["POST"])
    def action_stepper_move():
        """Move a named stepper motor by a bounded number of steps."""
        api_log(
            f"POST /actions/stepper_move - Request body: {request.get_json(silent=True)}"
        )
        try:
            data = get_json_body()
            motor_name = data.get("motor") or data.get("name")
            result = action_executor.stepper_move(
                motor_name,
                data.get("steps"),
                data.get("direction", "forward"),
                source="manual_api",
            )
            return jsonify(result)
        except ActionExecutorError as e:
            api_log(f"Action error: {e}")
            return jsonify({"error": str(e)}), 400

    @app.errorhandler(404)
    def not_found(error):
        api_log(f"✗ 404 Error: {error}")
        return jsonify({"error": "Endpoint not found"}), 404

    @app.errorhandler(500)
    def server_error(error):
        api_log(f"✗ 500 Error: {error}")
        return jsonify({"error": str(error)}), 500

    api_log("Flask API application created successfully")
    return app
