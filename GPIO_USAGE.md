# GPIO Controller Documentation

A Python class for simple, seamless interaction with Raspberry Pi GPIO pins.

## Installation

Install the required dependency:

```bash
pip install RPi.GPIO
```

## Quick Start

```python
from gpio_controller import GPIOController

# Create controller (BCM mode by default)
gpio = GPIOController()

# Setup a pin as output
gpio.setup_output(17)

# Write to pin
gpio.write(17, True)   # Turn on (HIGH)
gpio.write(17, False)  # Turn off (LOW)

# Cleanup
gpio.cleanup()
```

## Pin Numbering Modes

### BCM (Broadcom) - Recommended
Uses GPIO chip pin numbers. Most documentation and tutorials use this.

```python
gpio = GPIOController(mode="BCM")
gpio.setup_output(17)  # GPIO17
```

### BOARD
Uses physical header pin positions (1-40 on RPi 3B+). Changes between RPi models.

```python
gpio = GPIOController(mode="BOARD")
gpio.setup_output(11)  # Physical pin 11 (GPIO17)
```

## API Reference

### Initialization

```python
gpio = GPIOController(mode="BCM")  # "BCM" or "BOARD"
```

### Output Operations

#### `setup_output(pin, initial_state=False)`
Configure a pin as an output.

```python
gpio.setup_output(17)              # Default: starts LOW
gpio.setup_output(18, initial_state=True)  # Starts HIGH
```

#### `write(pin, state)`
Set pin HIGH (True) or LOW (False).

```python
gpio.write(17, True)   # Turn on
gpio.write(17, False)  # Turn off
```

#### `toggle(pin)`
Toggle pin between HIGH and LOW.

```python
gpio.toggle(17)  # If HIGH→LOW, if LOW→HIGH
```

#### `blink(pin, times=1, delay=0.5)`
Blink pin on/off repeatedly.

```python
gpio.blink(17)                     # Blink once
gpio.blink(17, times=5)            # Blink 5 times
gpio.blink(17, times=3, delay=0.1) # Fast blink
```

### Input Operations

#### `setup_input(pin, pull_up_down="NONE")`
Configure a pin as an input.

```python
gpio.setup_input(18)                      # Floating (no pull)
gpio.setup_input(18, pull_up_down="UP")   # Pull-up resistor
gpio.setup_input(18, pull_up_down="DOWN") # Pull-down resistor
```

#### `read(pin)`
Read pin state. Returns True (HIGH) or False (LOW).

```python
state = gpio.read(18)
if state:
    print("Button pressed")
```

### Cleanup

#### `cleanup()`
Release GPIO resources. Called automatically on exit.

```python
gpio.cleanup()
```

## Usage Patterns

### Context Manager (Automatic Cleanup)

Automatically cleans up resources when exiting the block:

```python
with GPIOController() as gpio:
    gpio.setup_output(17)
    gpio.write(17, True)
    # cleanup() called automatically
```

### LED Control

```python
with GPIOController() as gpio:
    led_pin = 17
    gpio.setup_output(led_pin)
    
    gpio.write(led_pin, True)   # Turn on
    gpio.blink(led_pin, times=10, delay=0.2)
    gpio.write(led_pin, False)  # Turn off
```

### Button Reading

```python
with GPIOController() as gpio:
    button_pin = 18
    gpio.setup_input(button_pin, pull_up_down="UP")
    
    if gpio.read(button_pin):
        print("Button not pressed")
    else:
        print("Button pressed")
```

### Multiple Pins

```python
with GPIOController() as gpio:
    # Setup multiple outputs
    led1, led2, led3 = 17, 18, 27
    for pin in [led1, led2, led3]:
        gpio.setup_output(pin)
    
    # Cycle through LEDs
    for pin in [led1, led2, led3]:
        gpio.write(pin, True)
        time.sleep(0.5)
        gpio.write(pin, False)
```

### Polling a Button

```python
import time

with GPIOController() as gpio:
    button = 18
    gpio.setup_input(button, pull_up_down="UP")
    
    print("Waiting for button press...")
    while True:
        if not gpio.read(button):  # Button pressed (active low)
            print("Button pressed!")
            time.sleep(0.2)  # Debounce
        time.sleep(0.05)  # Poll every 50ms
```

## Common Patterns

### Active High vs Active Low

**Active High** (default):
```python
gpio.write(led, True)   # LED on
gpio.write(led, False)  # LED off
```

**Active Low** (common with buttons):
```python
state = gpio.read(button)
if not state:  # Button pressed (LOW)
    print("Pressed")
```

### Debouncing

Add a small delay after reading button state to avoid noise:

```python
if not gpio.read(button):
    time.sleep(0.2)  # Debounce delay
    if not gpio.read(button):  # Re-check
        print("Button confirmed pressed")
```

## Tips & Best Practices

1. **Always cleanup** — Use context manager or call `cleanup()` to avoid GPIO warnings
2. **Use BCM mode** — More portable across Raspberry Pi models
3. **Pull resistors for buttons** — Use `pull_up_down="UP"` for button inputs
4. **Add debounce delays** — Mechanical buttons can bounce; add small delays
5. **Check voltage specs** — RPi GPIO is 3.3V, not 5V
6. **Use a GPIO diagram** — Reference your RPi pinout before wiring

## Raspberry Pi 3B+ Pin Layout

Common pins (BCM mode):
- **GPIO17, 18, 27**: Digital IO (LEDs, relays)
- **GPIO22, 23, 24, 25**: Additional digital IO
- **GPIO2, 3**: I2C (SDA, SCL)
- **GPIO10, 9, 11**: SPI (MOSI, MISO, CLK)

See [pinout.xyz](https://pinout.xyz) for visual reference.

## Troubleshooting

**"No module named 'RPi.GPIO'"**
- Install: `pip install RPi.GPIO`
- Must run on actual Raspberry Pi

**"GPIO is in use" warnings**
- Call `gpio.cleanup()` or use context manager
- Stop other programs using GPIO

**Pin not responding**
- Verify pin number (BCM vs BOARD mode)
- Check physical wiring
- Confirm voltage levels (3.3V max)
