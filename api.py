from flask import Flask, jsonify, request, send_file
from motor import DCMotor, StepperMotor
from motor_manager import MotorManager
import os


def create_api(motor_manager: MotorManager) -> Flask:
    """Create Flask API application."""
    app = Flask(__name__, static_folder='.', static_url_path='')

    @app.route('/')
    def serve_ui():
        """Serve the web UI."""
        return send_file('index.html')

    @app.route("/motors", methods=["GET"])
    def list_motors():
        """List all motors and their current state."""
        return jsonify(motor_manager.list_motors())

    @app.route("/motors/<name>", methods=["GET"])
    def get_motor(name: str):
        """Get state of specific motor."""
        motor = motor_manager.get_motor(name)
        if not motor:
            return jsonify({"error": f"Motor '{name}' not found"}), 404
        return jsonify(motor.get_state())

    @app.route("/motors/<name>/speed", methods=["POST"])
    def set_speed(name: str):
        """Set DC motor speed (0-100)."""
        motor = motor_manager.get_motor(name)
        if not motor:
            return jsonify({"error": f"Motor '{name}' not found"}), 404
        if not isinstance(motor, DCMotor):
            return jsonify({"error": f"'{name}' is not a DC motor"}), 400

        data = request.get_json()
        if "speed" not in data:
            return jsonify({"error": "Missing 'speed' parameter"}), 400

        speed = data["speed"]
        if not isinstance(speed, (int, float)) or speed < 0 or speed > 100:
            return jsonify({"error": "Speed must be 0-100"}), 400

        motor.set_speed(int(speed))
        return jsonify({"status": "success", "motor": name, "speed": motor.speed})

    @app.route("/motors/<name>/direction", methods=["POST"])
    def set_direction(name: str):
        """Set DC motor direction."""
        motor = motor_manager.get_motor(name)
        if not motor:
            return jsonify({"error": f"Motor '{name}' not found"}), 404
        if not isinstance(motor, DCMotor):
            return jsonify({"error": f"'{name}' is not a DC motor"}), 400

        data = request.get_json()
        if "direction" not in data:
            return jsonify({"error": "Missing 'direction' parameter"}), 400

        direction = data["direction"].lower()
        if direction == "forward":
            motor.forward()
        elif direction == "backward":
            motor.backward()
        else:
            return jsonify({"error": "Direction must be 'forward' or 'backward'"}), 400

        return jsonify(
            {"status": "success", "motor": name, "direction": motor.direction}
        )

    @app.route("/motors/<name>/step", methods=["POST"])
    def step_motor(name: str):
        """Command stepper motor."""
        motor = motor_manager.get_motor(name)
        if not motor:
            return jsonify({"error": f"Motor '{name}' not found"}), 404
        if not isinstance(motor, StepperMotor):
            return jsonify({"error": f"'{name}' is not a stepper motor"}), 400

        data = request.get_json()
        steps = data.get("steps", 100)
        direction = data.get("direction", "forward").lower()

        if not isinstance(steps, int) or steps < 1:
            return jsonify({"error": "Steps must be a positive integer"}), 400
        if direction not in ["forward", "backward"]:
            return jsonify({"error": "Direction must be 'forward' or 'backward'"}), 400

        motor.step(steps, direction)
        return jsonify(
            {
                "status": "success",
                "motor": name,
                "steps": steps,
                "direction": direction,
            }
        )

    @app.route("/motors/<name>/speed", methods=["POST"])
    def set_stepper_speed(name: str):
        """Set stepper motor speed in RPM."""
        motor = motor_manager.get_motor(name)
        if not motor:
            return jsonify({"error": f"Motor '{name}' not found"}), 404
        if not isinstance(motor, StepperMotor):
            return jsonify({"error": f"'{name}' is not a stepper motor"}), 400

        data = request.get_json()
        if "rpm" not in data:
            return jsonify({"error": "Missing 'rpm' parameter"}), 400

        rpm = data["rpm"]
        if not isinstance(rpm, (int, float)) or rpm < 0:
            return jsonify({"error": "RPM must be non-negative"}), 400

        motor.set_speed(float(rpm))
        return jsonify({"status": "success", "motor": name, "rpm": rpm})

    @app.route("/motors/<name>/stop", methods=["POST"])
    def stop_motor(name: str):
        """Stop motor."""
        motor = motor_manager.get_motor(name)
        if not motor:
            return jsonify({"error": f"Motor '{name}' not found"}), 404

        motor.stop()
        return jsonify({"status": "success", "motor": name, "stopped": True})

    @app.errorhandler(404)
    def not_found(error):
        return jsonify({"error": "Endpoint not found"}), 404

    @app.errorhandler(500)
    def server_error(error):
        return jsonify({"error": str(error)}), 500

    return app
