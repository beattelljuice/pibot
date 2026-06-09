# AI Chassis Control Design

## Goal

Put an AI-controlled decision layer in the robot chassis without letting the AI write directly to GPIO.

The AI should feel like it is "inside" the chassis because it receives the robot's live state, sensor inputs, current goal, motor state, recent action history, and safety status. It should respond by choosing validated robot actions and optional speech, not by producing free-form motor commands.

## Core Principle

The AI proposes actions. The robot runtime owns execution.

This matters because an LLM hosted through Ollama can be slow, unavailable, or wrong. The robot still needs immediate safety behavior even when the model is thinking, timing out, or hallucinating an invalid command.

## Recommended Architecture

```text
                         LAN
                  +----------------+
                  | Ollama server  |
                  | model + API    |
                  +-------^--------+
                          |
                          | strict JSON request/response
                          |
+-------------------------+--------------------------+
| Raspberry Pi robot runtime                         |
|                                                    |
|  +----------------+      +----------------------+  |
|  | Sensor layer   |----->| Robot state store    |  |
|  | camera, GPIO,  |      | latest world snapshot|  |
|  | distance, IMU, |      +----------+-----------+  |
|  | battery, mic   |                 |              |
|  +----------------+                 v              |
|                              +------+-------+      |
|                              | AI brain     |      |
|                              | Ollama client|      |
|                              +------+-------+      |
|                                     |              |
|                          proposed action JSON      |
|                                     v              |
|  +----------------+      +----------+-----------+  |
|  | Manual UI/API  |----->| Safety supervisor    |  |
|  +----------------+      | validates/clamps     |  |
|                          +----------+-----------+  |
|                                     |              |
|                                     v              |
|                          +----------+-----------+  |
|                          | Action executor      |  |
|                          | drive/stop/stepper   |  |
|                          +----------+-----------+  |
|                                     |              |
|                                     v              |
|                          Existing MotorManager     |
|                          DCMotor / StepperMotor    |
|                          GPIOController            |
+----------------------------------------------------+
```

Your current code already covers the bottom layer:

- `GPIOController`: raw pin setup/read/write.
- `DCMotor` and `StepperMotor`: hardware-specific motor control.
- `MotorManager`: motor registry.
- Flask API: manual HTTP control and browser UI.

The next layer should not replace that code. It should wrap it.

## Runtime Loops

The system should have separate timing loops.

### 1. Safety Loop

Runs fast, ideally 20-50 Hz.

Responsibilities:

- Stop motors when an obstacle is too close.
- Stop motors when an action expires.
- Stop motors when the AI heartbeat is missing.
- Stop motors when manual emergency stop is active.
- Clamp motor power and stepper movement to safe limits.
- Prevent movement when battery or temperature is unsafe.

This loop must not wait for Ollama.

### 2. Sensor Loop

Runs at sensor-appropriate rates.

Examples:

- Distance sensor: 10-20 Hz.
- IMU: 20-100 Hz if used.
- Battery state: 1 Hz.
- Camera summaries: slower, maybe 1-5 Hz depending on model and hardware.
- Microphone/speech transcription: event-based.

Every sensor reading should include a timestamp. Old sensor data should be treated as stale.

Camera frames should not be embedded into every `/robot/state` response. Keep the state snapshot lightweight by storing camera availability and latest capture metadata, then let the AI loop request a fresh image only when needed.

### 3. AI Decision Loop

Runs slower, usually 1-4 Hz at first.

Responsibilities:

- Build a compact robot-state snapshot.
- Send it to Ollama with the current user goal.
- Require a strict JSON response.
- Pass proposed actions to the safety supervisor.
- Store the AI's short-term memory and last decisions.

The AI loop should use a short timeout. If Ollama does not respond in time, the robot should continue the current short action only until it expires, then stop or fall back to a safe behavior.

## Why Short Actions Matter

Do not let the AI say "drive forward" with no end time.

Use short-lived commands:

```json
{
  "type": "drive_tank",
  "left_power": 30,
  "right_power": 30,
  "duration_ms": 250
}
```

The AI has to keep renewing motion. If it freezes, the robot naturally stops.

## AI Input Snapshot

The AI should receive a compact JSON object like this:

```json
{
  "robot": {
    "mode": "ai",
    "pose": null,
    "battery_percent": 78,
    "motors": {
      "left_motor": {"speed": 0, "direction": "stopped"},
      "right_motor": {"speed": 0, "direction": "stopped"}
    }
  },
  "sensors": {
    "front_distance_cm": 86,
    "left_bumper": false,
    "right_bumper": false,
    "camera_summary": "open floor ahead, table leg on right"
  },
  "safety": {
    "emergency_stop": false,
    "movement_allowed": true,
    "max_drive_power": 45,
    "max_action_ms": 500
  },
  "goal": {
    "operator_request": "explore the room and avoid obstacles"
  },
  "recent_actions": [
    {"type": "drive_tank", "left_power": 25, "right_power": 25, "result": "completed"}
  ],
  "available_actions": [
    "stop",
    "drive_tank",
    "rotate",
    "stepper_move",
    "speak",
    "wait"
  ]
}
```

Keep this snapshot factual. Avoid asking the model to infer hardware limits that the runtime already knows.

## AI Output Schema

The AI should only be allowed to return JSON matching a small schema:

```json
{
  "speech": "I am moving forward slowly.",
  "actions": [
    {
      "type": "drive_tank",
      "left_power": 25,
      "right_power": 25,
      "duration_ms": 300
    }
  ],
  "next_check_ms": 250
}
```

Allowed action types:

- `stop`: stop all motion immediately.
- `drive_tank`: set left and right DC motor power for a short duration.
- `rotate`: turn in place for a short duration.
- `arm_move`: move a named arm by a bounded number of steps.
- `display_frame`: replace the OLED display with a full 128x64 1-bit frame.
- `display_text`: write short text to the OLED display.
- `speak`: say a short phrase through TTS.
- `wait`: do nothing for a short duration.

The runtime should reject anything else.

## Display Control Note

The SH1106 OLED is safe for broad AI control because display pixels cannot move the chassis or arms.

Expose display control differently from motor control:

- Allow complete pixel-frame writes.
- Still validate frame shape and size.
- Keep display writes in the robot state/action history.
- Do not let malformed display payloads block safety or motor shutdown.

The AI can eventually use:

```json
{
  "type": "display_frame",
  "rows": [
    "00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
  ]
}
```

For actual use, `rows` must contain 64 strings, each exactly 128 characters. `1` means lit pixel and `0` means unlit pixel.

## Camera Sensor Note

The USB camera is a sensor capability, not an action executor.

Expose it through:

- `GET /camera/status`
- `GET /camera/snapshot.jpg`
- `POST /camera/capture`

The AI loop should use `/camera/capture` when it needs a fresh vision input. The robot state should carry only compact metadata:

```json
{
  "camera_snapshot": {
    "captured_at": "2026-06-09T12:00:00.000",
    "width": 640,
    "height": 480,
    "mime": "image/jpeg",
    "snapshot_url": "/camera/snapshot.jpg"
  }
}
```

Later, the Ollama vision request can attach the base64 JPEG from `/camera/capture` if the selected model supports image input.

Camera dimensions are dynamic. The runtime may request the largest detected camera mode, but the AI loop should trust the `width` and `height` returned with each capture instead of assuming a fixed resolution.

## Arm Control Note

The two stepper motors should be treated as arms at the AI/action level.

Keep the low-level motor names in the hardware layer if useful, but do not make the AI reason about `stepper_1` and `stepper_2` forever. Give them body-aware aliases such as:

```json
{
  "arms": {
    "left_arm": {"motor": "stepper_1", "min_position": 0, "max_position": 1000},
    "right_arm": {"motor": "stepper_2", "min_position": 0, "max_position": 1000}
  }
}
```

The AI should eventually request semantic arm actions:

```json
{
  "type": "arm_move",
  "arm": "left_arm",
  "steps": 80,
  "direction": "up"
}
```

The runtime should translate that into the correct stepper motor, enforce position limits, and reject movement past the safe arm range. Until position tracking exists, keep arm moves small and bounded.

## Safety Supervisor Rules

The supervisor should validate every AI action before execution.

Minimum rules:

- Reject unknown action types.
- Reject missing fields.
- Clamp motor power to `[-max_drive_power, max_drive_power]`.
- Clamp duration to `max_action_ms`.
- Reject forward motion if front distance is below the safe threshold.
- Reject backward motion if rear distance is unsafe, once rear sensors exist.
- Stop everything if emergency stop is active.
- Stop everything if no fresh AI action arrives before the current action expires.
- Require manual confirmation before enabling AI mode after boot.

The safety supervisor should log both the original AI proposal and the final executed command.

## Manual Mode vs AI Mode

Keep manual control and AI control separate.

Suggested modes:

- `manual`: browser/API commands control motors directly through the safety supervisor.
- `ai`: Ollama decisions can control motors through the safety supervisor.
- `paused`: no movement except explicit stop/reset.
- `estop`: all movement disabled until physically or manually cleared.

Manual emergency stop should always override AI mode.

## Ollama Integration

Add an Ollama client module that:

- Reads `ollama.url`, `ollama.model`, and timeout settings from `config.json`.
- Sends the robot-state snapshot.
- Requests JSON output.
- Retries only when safe.
- Returns a structured error when the model is unavailable.

Example config shape:

```json
{
  "ollama": {
    "url": "http://192.168.1.50:11434",
    "model": "your-model-name",
    "timeout_ms": 1200
  },
  "ai_control": {
    "enabled_on_start": false,
    "decision_interval_ms": 300,
    "max_action_ms": 500,
    "max_drive_power": 45,
    "heartbeat_timeout_ms": 1000
  },
  "safety": {
    "front_stop_distance_cm": 25,
    "front_slow_distance_cm": 60
  }
}
```

## Prompt Strategy

The system prompt should be strict and boring:

```text
You are the decision layer for a physical robot chassis.
You must only return valid JSON.
You may only use the actions listed in available_actions.
All movement must be short-duration and cautious.
If sensor data is stale, unsafe, or missing, stop or wait.
Never invent sensors or actions.
```

The user/operator prompt can hold the current goal:

```text
Current operator goal: explore the room slowly and avoid obstacles.
```

The live robot snapshot should be sent as structured JSON, not prose.

## Implementation Roadmap

### Phase 1: Make motor execution safer

Create an `ActionExecutor` around `MotorManager`.

It should expose:

- `stop_all()`
- `drive_tank(left_power, right_power, duration_ms)`
- `rotate(power, direction, duration_ms)`
- `stepper_move(name, steps, direction)`

It should track action expiry and stop motors automatically.

For the current chassis, `stepper_move` is the low-level primitive for arm movement. Once the left/right arm mapping is known, add an arm-aware wrapper above it.

### Phase 2: Add robot state

Create a `RobotState` object that stores:

- Current mode.
- Latest motor state.
- Latest sensor readings.
- Last action.
- Last AI response.
- Emergency stop state.
- Current operator goal.

Start with fake or empty sensors. The architecture should work before every sensor exists.

Expose the state through:

- `GET /robot/state`
- `GET /robot/status`
- `POST /robot/goal`
- `POST /robot/mode`
- `POST /robot/estop`
- `POST /robot/estop/clear`
- `POST /robot/sensors/<name>`
- `DELETE /robot/sensors/<name>`

### Phase 3: Add safety supervisor

Create a `SafetySupervisor` that accepts proposed actions and returns approved actions.

The executor should only execute approved actions.

### Phase 4: Add Ollama client

Create an `OllamaBrain` module that:

- Builds prompts from `RobotState`.
- Calls Ollama.
- Parses JSON.
- Handles timeout/errors.
- Produces proposed actions.

### Phase 5: Add AI loop

Run the AI loop in a background thread.

API endpoints to add:

- `GET /ai/status`
- `POST /ai/start`
- `POST /ai/stop`
- `POST /ai/goal`
- `POST /ai/estop`
- `POST /ai/estop/clear`

### Phase 6: Add real sensors

Add sensors one at a time.

Recommended order:

1. Emergency stop input.
2. Front distance sensor.
3. Battery voltage.
4. Camera snapshot or summary.
5. Microphone/speech.
6. IMU/odometry if needed.

### Phase 7: Add memory and personality

Once movement is safe, give the AI:

- A persistent name/persona.
- A short rolling memory of recent events.
- A map or room notes.
- Voice input and TTS output.

This is what makes it feel embodied, but it should come after safety and timing.

## First Practical Milestone

The first useful milestone is not full autonomy. It is:

1. AI mode can be turned on and off.
2. The AI receives current motor state and a text goal.
3. Ollama returns strict JSON.
4. The robot can execute short `drive_tank`, `rotate`, `wait`, and `stop` actions.
5. If Ollama freezes or returns bad JSON, the robot stops.

That milestone proves the control loop without needing cameras, voice, mapping, or complex autonomy.

## Key Design Choice

Use the LLM as a high-level decision maker, not as the motor controller.

The embodied feeling comes from giving it live state and letting it choose bounded actions repeatedly. The safety and timing come from keeping hard limits inside the robot runtime.
