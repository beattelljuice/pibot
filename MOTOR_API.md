# Motor Control API Documentation

Complete guide to the Raspberry Pi motor control system for DC and stepper motors.

## Architecture Overview

```
┌─────────────────────────────────┐
│     Flask REST API (api.py)     │  HTTP endpoints
├─────────────────────────────────┤
│    Motor Manager (motor_manager.py)  │  Central registry
├─────────────────────────────────┤
│  DCMotor & StepperMotor (motor.py)  │  Motor abstractions
├─────────────────────────────────┤
│  GPIO Controller (gpio_controller.py) │  Low-level GPIO
```

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Motors

Edit `config.json` with your pin assignments:

```json
{
  "dc_motors": {
    "left_motor": {
      "enable": 12,
      "direction": 13
    },
    "right_motor": {
      "enable": 6,
      "direction": 5
    }
  },
  "stepper_motors": {
    "stepper_1": {
      "pins": [17, 18, 27, 22]
    },
    "stepper_2": {
      "pins": [24, 25, 8, 7]
    }
  },
  "api": {
    "host": "0.0.0.0",
    "port": 5000,
    "debug": false
  }
}
```

### 3. Run Server

```bash
python3 main.py
```

Output:
```
Initializing GPIO Controller...
Loading configuration...
Initializing Motor Manager...
Registering DC motors...
  Registered: left_motor
  Registered: right_motor
Registering stepper motors...
  Registered: stepper_1
  Registered: stepper_2

Motor Summary:
  left_motor: DC
  right_motor: DC
  stepper_1: Stepper
  stepper_2: Stepper

Starting API server on 0.0.0.0:5000...
```

## Direct Python API

### DC Motor Control

```python
from gpio_controller import GPIOController
from motor import DCMotor

gpio = GPIOController()
motor = DCMotor(gpio, enable_pin=12, direction_pin=13, name="left_motor")

# Set speed (0-100%)
motor.set_speed(50)

# Set direction
motor.forward()
motor.backward()

# Stop
motor.stop()

# Get state
state = motor.get_state()
# {'name': 'left_motor', 'type': 'DC', 'speed': 50, 'direction': 'forward'}

motor.cleanup()
```

### Stepper Motor Control

```python
from gpio_controller import GPIOController
from motor import StepperMotor

gpio = GPIOController()
stepper = StepperMotor(gpio, pins=[17, 18, 27, 22], name="stepper_1")

# Set speed in RPM
stepper.set_speed(10)

# Step motor (non-blocking)
stepper.step(steps=100, direction="forward")
stepper.step(steps=100, direction="backward")

# Stop immediately
stepper.stop()

# Get state
state = stepper.get_state()
# {'name': 'stepper_1', 'type': 'Stepper', 'stepping': False, 'step_delay': 0.029, 'rpm': 10}

stepper.cleanup()
```

### Motor Manager

```python
from gpio_controller import GPIOController
from motor_manager import MotorManager
from config import Config

gpio = GPIOController()
config = Config("config.json")
manager = MotorManager(gpio)

# Register motors from config
for name, pins in config.get_dc_motors().items():
    manager.register_dc_motor(name, pins["enable"], pins["direction"])

for name, pins_config in config.get_stepper_motors().items():
    manager.register_stepper_motor(name, pins_config["pins"])

# Get specific motor
left = manager.get_motor("left_motor")
left.set_speed(75)
left.forward()

# List all motors
states = manager.list_motors()
print(states)

# Cleanup
manager.cleanup()
```

## REST API Endpoints

### List All Motors

**GET** `/motors`

Returns status of all motors.

```bash
curl http://localhost:5000/motors
```

Response:
```json
{
  "left_motor": {
    "name": "left_motor",
    "type": "DC",
    "speed": 0,
    "direction": "stopped"
  },
  "stepper_1": {
    "name": "stepper_1",
    "type": "Stepper",
    "stepping": false,
    "step_delay": 0.01,
    "rpm": 0
  }
}
```

### Get Motor State

**GET** `/motors/<name>`

Returns state of specific motor.

```bash
curl http://localhost:5000/motors/left_motor
```

Response:
```json
{
  "name": "left_motor",
  "type": "DC",
  "speed": 0,
  "direction": "stopped"
}
```

### Set DC Motor Speed

**POST** `/motors/<name>/speed`

Set speed (0-100%).

```bash
curl -X POST http://localhost:5000/motors/left_motor/speed \
  -H "Content-Type: application/json" \
  -d '{"speed": 75}'
```

Response:
```json
{
  "status": "success",
  "motor": "left_motor",
  "speed": 75
}
```

### Set DC Motor Direction

**POST** `/motors/<name>/direction`

Set direction to forward or backward.

```bash
curl -X POST http://localhost:5000/motors/left_motor/direction \
  -H "Content-Type: application/json" \
  -d '{"direction": "forward"}'
```

Response:
```json
{
  "status": "success",
  "motor": "left_motor",
  "direction": "forward"
}
```

### Step Stepper Motor

**POST** `/motors/<name>/step`

Move stepper a number of steps.

```bash
curl -X POST http://localhost:5000/motors/stepper_1/step \
  -H "Content-Type: application/json" \
  -d '{"steps": 100, "direction": "forward"}'
```

Response:
```json
{
  "status": "success",
  "motor": "stepper_1",
  "steps": 100,
  "direction": "forward"
}
```

### Set Stepper Speed

**POST** `/motors/<name>/speed`

Set speed in RPM.

```bash
curl -X POST http://localhost:5000/motors/stepper_1/speed \
  -H "Content-Type: application/json" \
  -d '{"rpm": 20}'
```

Response:
```json
{
  "status": "success",
  "motor": "stepper_1",
  "rpm": 20
}
```

### Stop Motor

**POST** `/motors/<name>/stop`

Stop any motor immediately.

```bash
curl -X POST http://localhost:5000/motors/left_motor/stop
```

Response:
```json
{
  "status": "success",
  "motor": "left_motor",
  "stopped": true
}
```

## Pin Configuration Guide

### DC Motor Pins

- **enable_pin**: PWM pin that controls speed (0-100%)
- **direction_pin**: Digital pin for direction (HIGH=forward, LOW=backward)

Example:
```json
"left_motor": {
  "enable": 12,
  "direction": 13
}
```

### Stepper Motor Pins (28BYJ-48 + ULN2003)

Four pins corresponding to motor coils (IN1, IN2, IN3, IN4).

Example:
```json
"stepper_1": {
  "pins": [17, 18, 27, 22]
}
```

## Common Patterns

### Robot Movement

```python
# Drive forward at 80% speed
left.set_speed(80)
right.set_speed(80)
left.forward()
right.forward()

# Turn right: left faster than right
left.set_speed(100)
right.set_speed(50)

# Stop
left.stop()
right.stop()
```

### Sequential Stepping

```python
# Move stepper 1 then stepper 2
stepper1.step(200, direction="forward")
stepper1.stop()  # Wait for completion

stepper2.step(150, direction="backward")
stepper2.stop()
```

### Speed Ramping

```python
for speed in range(0, 101, 10):
    motor.set_speed(speed)
    time.sleep(0.2)
```

## Error Handling

### Invalid Motor Name
```json
{
  "error": "Motor 'invalid_name' not found"
}
```

### Invalid Speed
```json
{
  "error": "Speed must be 0-100"
}
```

### Invalid Direction
```json
{
  "error": "Direction must be 'forward' or 'backward'"
}
```

## Performance Notes

- **DC Motor PWM**: 1000Hz frequency (standard)
- **Stepper Default Speed**: 10ms per step (configurable)
- **Stepper Async**: Non-blocking stepping via threading
- **28BYJ-48 Steps/Rev**: 2048 full steps

## Troubleshooting

**Motor not moving**
- Check GPIO pin assignments in config.json
- Verify physical wiring
- Confirm motor power supply

**Stepper vibrating but not moving**
- Pins may be in wrong order (IN1, IN2, IN3, IN4)
- Try different pin sequences in config.json

**API connection refused**
- Ensure main.py is running
- Check API host/port in config.json
- Verify firewall allows port 5000

**GPIO warnings**
- These are suppressed automatically
- Ensure cleanup() is called when finished

## Advanced Topics

### Custom Motor Classes

Extend `DCMotor` or `StepperMotor` to add custom behavior:

```python
class CustomDCMotor(DCMotor):
    def ramp_up(self, target_speed=100, step=5, delay=0.1):
        for speed in range(0, target_speed + 1, step):
            self.set_speed(speed)
            time.sleep(delay)
```

### Custom API Endpoints

Add routes to `api.py`:

```python
@app.route("/robot/forward", methods=["POST"])
def robot_forward():
    left = motor_manager.get_motor("left_motor")
    right = motor_manager.get_motor("right_motor")
    left.set_speed(80)
    right.set_speed(80)
    left.forward()
    right.forward()
    return jsonify({"status": "moving forward"})
```

## References

- [Raspberry Pi GPIO Pinout](https://pinout.xyz)
- [28BYJ-48 Stepper Motor Datasheet](https://www.makerguides.com/28byj-48-stepper-motor-arduino-tutorial/)
- [PWM on Raspberry Pi](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html)
