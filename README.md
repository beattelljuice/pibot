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
- Safety supervisor proposed-action tests
- Timed drive and rotate actions
- Fake or real sensor updates
- Low-level stepper action tests
- SH1106 OLED text and pixel-frame tests
- USB camera snapshot and capture tests

## Phase 3 Safety Tests

Run the standalone fake-hardware safety test harness:

```bash
python3 phase3_test_harness.py
```

It prints structured JSON with pass/fail results.

The AI-facing supervised action endpoint is:

```text
GET  /safety/status
POST /actions/propose
```

Example proposal:

```bash
curl -X POST http://localhost:5000/actions/propose \
  -H "Content-Type: application/json" \
  -d '{"source":"ai","actions":[{"type":"drive_tank","left_power":25,"right_power":25,"duration_ms":300}]}'
```

AI movement is allowed only while robot mode is `ai`. Safe non-movement actions such as OLED text and camera capture are allowed in paused or emergency-stop states.

## USB Camera Setup

Plug the USB camera into the Raspberry Pi before starting the server.

Install OpenCV and optional camera tools:

```bash
sudo apt install -y python3-opencv v4l-utils
```

Check that the camera appears:

```bash
v4l2-ctl --list-devices
```

The default camera config uses device index `0` and automatic resolution:

```json
"camera": {
  "enabled": true,
  "device_index": 0,
  "width": "auto",
  "height": "auto",
  "fps": "auto",
  "auto_resolution": true,
  "prefer_max_resolution": true,
  "jpeg_quality": 85,
  "warmup_frames": 2,
  "stale_after_ms": 2000
}
```

With `auto_resolution` and `prefer_max_resolution` enabled, the controller uses `v4l2-ctl` to find the largest discrete camera mode and asks OpenCV to use it. If mode detection is unavailable, it uses the camera's default output. The actual captured width and height are reported in `/camera/status`, `/robot/state`, and `/camera/capture`.

If the camera is not the first video device, change `device_index` in `config.json`. To force a specific mode, set numeric `width` and `height`, then set `auto_resolution` to `false`.

The camera is exposed through:

```text
GET  /camera/status
GET  /camera/snapshot.jpg
POST /camera/capture
```

`/camera/snapshot.jpg` is for browser preview. `/camera/capture` returns JPEG image data as base64 plus metadata for the future AI vision loop.

## SH1106 OLED Wiring

For a Blue 1.3 Inch OLED Display I2C 128x64 SH1106 module, use the Raspberry Pi I2C1 pins:

| OLED pin | Raspberry Pi pin |
| --- | --- |
| VCC | 3.3V, physical pin 1 |
| GND | Ground, physical pin 6 |
| SDA | GPIO2 / SDA1, physical pin 3 |
| SCL | GPIO3 / SCL1, physical pin 5 |

Use 3.3V for VCC unless your exact module documentation says otherwise. Some OLED boards accept 5V power, but the Raspberry Pi I2C pins are 3.3V logic.

Enable I2C on the Pi:

```bash
sudo raspi-config
```

Go to `Interface Options` -> `I2C` -> `Yes`, then reboot if prompted.

Install tools and verify the display address:

```bash
sudo apt install -y i2c-tools
i2cdetect -y 1
```

Most SH1106 I2C OLED modules appear at `0x3C`. If yours appears at `0x3D`, edit `config.json`:

```json
"display": {
  "enabled": true,
  "driver": "sh1106",
  "width": 128,
  "height": 64,
  "i2c_port": 1,
  "i2c_address": "0x3C",
  "rotate": 0
}
```

The OLED is exposed through:

```text
GET  /display/status
POST /display/clear
POST /display/text
POST /display/frame
```

`/display/frame` accepts full 128x64 pixel control using `rows`, where each row is a 128-character string of `0` and `1`.

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
