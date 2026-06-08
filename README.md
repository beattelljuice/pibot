# Motor Control System - Quick Start

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

Edit `config.json` with your GPIO pin assignments:

```json
{
  "dc_motors": {
    "left_motor": {"enable": 12, "direction": 13},
    "right_motor": {"enable": 6, "direction": 5}
  },
  "stepper_motors": {
    "stepper_1": {"pins": [17, 18, 27, 22]},
    "stepper_2": {"pins": [24, 25, 8, 7]}
  },
  "api": {
    "host": "0.0.0.0",
    "port": 5000,
    "debug": false
  }
}
```

## Running

Start the server:
```bash
python3 main.py
```

Open your browser to `http://localhost:5000` and you'll see the motor controller interface.

The browser UI includes both the manual motor controller and a Robot Runtime Tester for:

- Robot state/status
- Goal and mode changes
- Emergency stop and clear
- Timed drive and rotate actions
- Fake or real sensor updates
- Low-level stepper action tests

## Features

### Web Interface
- 🎮 Visual motor controller dashboard
- 📊 Real-time motor status
- ⚡ Sliders for DC motor speed control
- 🔄 Buttons for direction control
- 🎯 Stepper motor step controls with RPM adjustment
- ✅ Connection status indicator

### DC Motors
- Speed control (0-100%)
- Forward/Backward direction
- Stop command

### Stepper Motors
- Configurable steps to move
- Forward/Backward direction
- RPM speed adjustment
- Non-blocking async stepping
- Stop command

## API Endpoints

All endpoints are automatically accessible from the web UI, but can also be called directly:

**List motors:**
```bash
curl http://localhost:5000/motors
```

**Set DC motor speed:**
```bash
curl -X POST http://localhost:5000/motors/left_motor/speed \
  -H "Content-Type: application/json" \
  -d '{"speed": 75}'
```

**Set direction:**
```bash
curl -X POST http://localhost:5000/motors/left_motor/direction \
  -H "Content-Type: application/json" \
  -d '{"direction": "forward"}'
```

**Step stepper:**
```bash
curl -X POST http://localhost:5000/motors/stepper_1/step \
  -H "Content-Type: application/json" \
  -d '{"steps": 100, "direction": "forward"}'
```

**Stop motor:**
```bash
curl -X POST http://localhost:5000/motors/left_motor/stop
```

**Timed chassis drive:**
```bash
curl -X POST http://localhost:5000/actions/drive_tank \
  -H "Content-Type: application/json" \
  -d '{"left_power": 25, "right_power": 25, "duration_ms": 300}'
```

**Timed chassis rotate:**
```bash
curl -X POST http://localhost:5000/actions/rotate \
  -H "Content-Type: application/json" \
  -d '{"power": 25, "direction": "left", "duration_ms": 300}'
```

**Stop all motors:**
```bash
curl -X POST http://localhost:5000/actions/stop_all
```

**Action executor status:**
```bash
curl http://localhost:5000/actions/status
```

**Robot state snapshot:**
```bash
curl http://localhost:5000/robot/state
```

**Set robot goal:**
```bash
curl -X POST http://localhost:5000/robot/goal \
  -H "Content-Type: application/json" \
  -d '{"goal": "explore slowly and avoid obstacles"}'
```

**Set robot mode:**
```bash
curl -X POST http://localhost:5000/robot/mode \
  -H "Content-Type: application/json" \
  -d '{"mode": "manual"}'
```

**Update a fake or real sensor reading:**
```bash
curl -X POST http://localhost:5000/robot/sensors/front_distance_cm \
  -H "Content-Type: application/json" \
  -d '{"value": 85, "stale_after_ms": 500}'
```

**Emergency stop:**
```bash
curl -X POST http://localhost:5000/robot/estop \
  -H "Content-Type: application/json" \
  -d '{"reason": "operator stop"}'
```

## Pin Configuration Guide

### DC Motor (2 pins)
- `enable`: PWM pin for speed control
- `direction`: Digital pin for direction

### Stepper Motor (4 pins)
- Four pins for the ULN2003 driver: IN1, IN2, IN3, IN4

## File Structure

- `main.py` - Application entry point
- `api.py` - Flask REST API
- `motor.py` - DCMotor and StepperMotor classes
- `motor_manager.py` - Motor registry
- `gpio_controller.py` - Low-level GPIO control
- `config.py` - Configuration loader
- `config.json` - Motor configuration
- `index.html` - Web UI controller
- `requirements.txt` - Python dependencies

## Troubleshooting

**Cannot connect to API**
- Ensure `main.py` is running
- Check the API host/port in config.json
- Verify firewall allows the port

**Motor not responding**
- Check pin assignments in config.json
- Verify physical wiring
- Ensure motor power supply is connected

**Stepper not moving**
- Verify all 4 pins in order: IN1, IN2, IN3, IN4
- Try reordering pins if vibrating but not moving
- Check stepper power supply

See `MOTOR_API.md` for detailed API documentation.
