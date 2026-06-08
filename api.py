from flask import Flask, jsonify, request, send_file
from motor import DCMotor, StepperMotor
from motor_manager import MotorManager
import os
from datetime import datetime


def api_log(msg: str):
    """Log API message with timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{timestamp}] [API] {msg}")


def create_api(motor_manager: MotorManager) -> Flask:
    """Create Flask API application."""
    app = Flask(__name__, static_folder='.', static_url_path='')
    api_log("Creating Flask API application")

    @app.route('/')
    def serve_ui():
        """Serve the web UI."""
        api_log("GET / - Serving UI")
        return send_file('index.html')

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

    @app.route("/motors/<name>/speed", methods=["POST"])
    def set_motor_speed(name: str):
        """Set motor speed (DC: 0-100%, Stepper: RPM)."""
        api_log(f"POST /motors/{name}/speed - Request body: {request.get_json()}")
        motor = motor_manager.get_motor(name)
        if not motor:
            api_log(f"✗ Motor '{name}' not found")
            return jsonify({"error": f"Motor '{name}' not found"}), 404

        data = request.get_json()

        if isinstance(motor, DCMotor):
            api_log(f"Motor '{name}' is DC motor")
            if "speed" not in data:
                api_log(f"✗ Missing 'speed' parameter")
                return jsonify({"error": "Missing 'speed' parameter for DC motor"}), 400

            speed = data["speed"]
            if not isinstance(speed, (int, float)) or speed < 0 or speed > 100:
                api_log(f"✗ Invalid speed value: {speed}")
                return jsonify({"error": "Speed must be 0-100"}), 400

            api_log(f"Setting DC motor '{name}' speed to {speed}%")
            motor.set_speed(int(speed))
            api_log(f"✓ Speed set successfully")
            return jsonify({"status": "success", "motor": name, "speed": motor.speed})

        elif isinstance(motor, StepperMotor):
            api_log(f"Motor '{name}' is stepper motor")
            if "rpm" not in data:
                api_log(f"✗ Missing 'rpm' parameter")
                return jsonify({"error": "Missing 'rpm' parameter for stepper motor"}), 400

            rpm = data["rpm"]
            if not isinstance(rpm, (int, float)) or rpm < 0:
                api_log(f"✗ Invalid RPM value: {rpm}")
                return jsonify({"error": "RPM must be non-negative"}), 400

            api_log(f"Setting stepper motor '{name}' speed to {rpm} RPM")
            motor.set_speed(float(rpm))
            api_log(f"✓ RPM set successfully")
            return jsonify({"status": "success", "motor": name, "rpm": rpm})

        api_log(f"✗ Unknown motor type")
        return jsonify({"error": "Unknown motor type"}), 400

    @app.route("/motors/<name>/direction", methods=["POST"])
    def set_direction(name: str):
        """Set DC motor direction."""
        api_log(f"POST /motors/{name}/direction - Request body: {request.get_json()}")
        motor = motor_manager.get_motor(name)
        if not motor:
            api_log(f"✗ Motor '{name}' not found")
            return jsonify({"error": f"Motor '{name}' not found"}), 404
        if not isinstance(motor, DCMotor):
            api_log(f"✗ Motor '{name}' is not a DC motor")
            return jsonify({"error": f"'{name}' is not a DC motor"}), 400

        data = request.get_json()
        if "direction" not in data:
            api_log(f"✗ Missing 'direction' parameter")
            return jsonify({"error": "Missing 'direction' parameter"}), 400

        direction = data["direction"].lower()
        api_log(f"Setting direction to '{direction}'")
        if direction == "forward":
            motor.forward()
        elif direction == "backward":
            motor.backward()
        else:
            api_log(f"✗ Invalid direction: {direction}")
            return jsonify({"error": "Direction must be 'forward' or 'backward'"}), 400

        api_log(f"✓ Direction set successfully")
        return jsonify(
            {"status": "success", "motor": name, "direction": motor.direction}
        )

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
        api_log(f"✓ Motor stopped successfully")
        return jsonify({"status": "success", "motor": name, "stopped": True})

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
