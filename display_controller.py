from base64 import b64decode
from datetime import datetime
from threading import RLock
from typing import Any, Dict, Optional


class DisplayControllerError(ValueError):
    """Raised when the OLED display cannot execute a command."""


def display_log(msg: str) -> None:
    """Log display controller messages with timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{timestamp}] [DISPLAY] {msg}")


class DisplayController:
    """Pixel-level controller for a monochrome 128x64 SH1106 OLED."""

    def __init__(
        self,
        enabled: bool = False,
        driver: str = "sh1106",
        width: int = 128,
        height: int = 64,
        port: int = 1,
        address: int | str = 0x3C,
        rotate: int = 0,
    ):
        self.enabled = bool(enabled)
        self.driver = driver.lower()
        self.width = int(width)
        self.height = int(height)
        self.port = int(port)
        self.address = self._parse_address(address)
        self.rotate = int(rotate)
        self._lock = RLock()
        self._device = None
        self._available = False
        self._error = ""
        self._last_action = None
        self._last_frame_at = None
        self._last_image = None

        if self.enabled:
            self.initialize()
        else:
            display_log("Display disabled by configuration")

    @property
    def available(self) -> bool:
        """Return whether the display is ready for commands."""
        return self._available

    def initialize(self) -> None:
        """Initialize the physical OLED display."""
        with self._lock:
            try:
                from luma.core.interface.serial import i2c
                from luma.oled.device import sh1106, ssd1306

                device_classes = {
                    "sh1106": sh1106,
                    "ssd1306": ssd1306,
                }
                if self.driver not in device_classes:
                    raise DisplayControllerError(
                        "display driver must be 'sh1106' or 'ssd1306'"
                    )

                serial = i2c(port=self.port, address=self.address)
                self._device = device_classes[self.driver](
                    serial,
                    width=self.width,
                    height=self.height,
                    rotate=self.rotate,
                )
                self._available = True
                self._error = ""
                display_log(
                    f"Initialized {self.driver} OLED at i2c-{self.port} "
                    f"address=0x{self.address:02X} size={self.width}x{self.height}"
                )
                self.clear()
            except Exception as e:
                self._available = False
                self._device = None
                self._error = str(e)
                display_log(f"Display unavailable: {self._error}")

    def get_status(self) -> Dict[str, Any]:
        """Return display status for API and robot state snapshots."""
        with self._lock:
            return {
                "status": "success",
                "enabled": self.enabled,
                "available": self._available,
                "driver": self.driver,
                "width": self.width,
                "height": self.height,
                "i2c": {
                    "port": self.port,
                    "address": f"0x{self.address:02X}",
                },
                "rotate": self.rotate,
                "last_action": self._last_action,
                "last_frame_at": self._last_frame_at,
                "error": self._error,
            }

    def clear(self) -> Dict[str, Any]:
        """Clear the OLED display."""
        with self._lock:
            self._require_device()
            if hasattr(self._device, "clear"):
                self._device.clear()
            image = self._blank_image()
            self._last_image = image.copy()
            if not hasattr(self._device, "clear"):
                self._device.display(image)
            return self._mark_action("clear")

    def display_text(
        self,
        text: str,
        x: int = 0,
        y: int = 0,
        clear: bool = True,
    ) -> Dict[str, Any]:
        """Render text onto the OLED using Pillow's default font."""
        if not isinstance(text, str):
            raise DisplayControllerError("text must be a string")

        with self._lock:
            self._require_device()
            if clear or self._last_image is None:
                image = self._blank_image()
            else:
                image = self._last_image.copy()
            draw = self._image_draw(image)
            draw.multiline_text((int(x), int(y)), text, fill=255)
            self._device.display(image)
            self._last_image = image.copy()
            return self._mark_action(
                "text",
                {
                    "text": text,
                    "x": int(x),
                    "y": int(y),
                    "clear": bool(clear),
                },
            )

    def display_frame(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Display a complete 1-bit frame from rows, pixels, or packed base64."""
        if not isinstance(payload, dict):
            raise DisplayControllerError("frame payload must be a JSON object")

        with self._lock:
            self._require_device()
            if "rows" in payload:
                image = self._image_from_rows(payload["rows"])
                frame_format = "rows"
            elif "pixels" in payload:
                image = self._image_from_pixels(payload["pixels"])
                frame_format = "pixels"
            elif "data" in payload:
                image = self._image_from_base64(payload["data"])
                frame_format = payload.get("format", "base64_1bpp_msb")
            else:
                raise DisplayControllerError(
                    "frame payload must include 'rows', 'pixels', or 'data'"
                )

            self._device.display(image)
            self._last_image = image.copy()
            return self._mark_action(
                "frame",
                {
                    "format": frame_format,
                    "width": self.width,
                    "height": self.height,
                },
            )

    def _require_device(self) -> None:
        if not self.enabled:
            raise DisplayControllerError("display is disabled in config.json")
        if not self._available or self._device is None:
            message = self._error or "display is not available"
            raise DisplayControllerError(message)

    def _blank_image(self):
        from PIL import Image

        return Image.new("1", (self.width, self.height), 0)

    def _image_draw(self, image):
        from PIL import ImageDraw

        return ImageDraw.Draw(image)

    def _image_from_rows(self, rows: Any):
        if not isinstance(rows, list):
            raise DisplayControllerError("rows must be a list of strings")
        if len(rows) != self.height:
            raise DisplayControllerError(f"rows must contain {self.height} entries")

        image = self._blank_image()
        pixels = image.load()
        for y, row in enumerate(rows):
            if not isinstance(row, str):
                raise DisplayControllerError("each row must be a string")
            if len(row) != self.width:
                raise DisplayControllerError(
                    f"each row must be exactly {self.width} characters"
                )
            for x, value in enumerate(row):
                pixels[x, y] = 255 if value in {"1", "#", "X", "x", "*"} else 0
        return image

    def _image_from_pixels(self, rows: Any):
        if not isinstance(rows, list):
            raise DisplayControllerError("pixels must be a list of rows")
        if len(rows) != self.height:
            raise DisplayControllerError(f"pixels must contain {self.height} rows")

        image = self._blank_image()
        pixels = image.load()
        for y, row in enumerate(rows):
            if not isinstance(row, list):
                raise DisplayControllerError("each pixel row must be a list")
            if len(row) != self.width:
                raise DisplayControllerError(
                    f"each pixel row must contain {self.width} values"
                )
            for x, value in enumerate(row):
                pixels[x, y] = 255 if bool(value) else 0
        return image

    def _image_from_base64(self, data: Any):
        if not isinstance(data, str):
            raise DisplayControllerError("data must be a base64 string")

        try:
            packed = b64decode(data, validate=True)
        except Exception as e:
            raise DisplayControllerError(f"invalid base64 frame data: {e}") from e

        expected_bytes = (self.width * self.height) // 8
        if len(packed) != expected_bytes:
            raise DisplayControllerError(
                f"packed frame must be {expected_bytes} bytes"
            )

        image = self._blank_image()
        pixels = image.load()
        for y in range(self.height):
            for x in range(self.width):
                bit_index = y * self.width + x
                byte_value = packed[bit_index // 8]
                mask = 1 << (7 - (bit_index % 8))
                pixels[x, y] = 255 if byte_value & mask else 0
        return image

    def _mark_action(
        self,
        action_type: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self._last_action = {
            "type": action_type,
            "details": details or {},
            "timestamp": datetime.now().isoformat(timespec="milliseconds"),
        }
        self._last_frame_at = self._last_action["timestamp"]
        display_log(f"Display action: {action_type}")
        return {
            "status": "success",
            "action": action_type,
            "display": self.get_status(),
        }

    def _parse_address(self, address: int | str) -> int:
        if isinstance(address, str):
            return int(address, 0)
        return int(address)
