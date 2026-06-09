from datetime import datetime
import re
import subprocess
from threading import RLock
from typing import Any, Dict, Optional


class CameraControllerError(ValueError):
    """Raised when the USB camera cannot execute a command."""


def camera_log(msg: str) -> None:
    """Log camera controller messages with timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{timestamp}] [CAMERA] {msg}")


class CameraController:
    """USB camera controller using OpenCV VideoCapture."""

    def __init__(
        self,
        enabled: bool = False,
        device_index: int = 0,
        width: int | str | None = "auto",
        height: int | str | None = "auto",
        fps: int | str | None = "auto",
        auto_resolution: bool = True,
        prefer_max_resolution: bool = True,
        jpeg_quality: int = 85,
        warmup_frames: int = 2,
        stale_after_ms: int = 2000,
    ):
        self.enabled = bool(enabled)
        self.device_index = int(device_index)
        self.configured_width = self._parse_optional_dimension(width)
        self.configured_height = self._parse_optional_dimension(height)
        self.configured_fps = self._parse_optional_dimension(fps)
        self.auto_resolution = (
            bool(auto_resolution)
            or self.configured_width is None
            or self.configured_height is None
        )
        self.prefer_max_resolution = bool(prefer_max_resolution)
        self.width = self.configured_width
        self.height = self.configured_height
        self.fps = self.configured_fps
        self.jpeg_quality = max(1, min(100, int(jpeg_quality)))
        self.warmup_frames = max(0, int(warmup_frames))
        self.stale_after_ms = max(1, int(stale_after_ms))
        self._lock = RLock()
        self._capture = None
        self._available = False
        self._error = ""
        self._last_capture_at = None
        self._last_frame = None
        self._last_frame_meta: Optional[Dict[str, Any]] = None
        self._selected_mode: Optional[Dict[str, Any]] = None

        if self.enabled:
            self.initialize()
        else:
            camera_log("Camera disabled by configuration")

    @property
    def available(self) -> bool:
        """Return whether the camera is ready for commands."""
        return self._available

    def initialize(self) -> None:
        """Open the USB camera."""
        with self._lock:
            try:
                cv2 = self._cv2()
                self._release_locked()
                capture = cv2.VideoCapture(self.device_index)
                if not capture or not capture.isOpened():
                    raise CameraControllerError(
                        f"could not open camera device {self.device_index}"
                    )

                self._configure_capture(capture, cv2)

                self._capture = capture
                self._available = True
                self._error = ""
                self._update_actual_mode_locked(cv2)
                camera_log(
                    f"Initialized USB camera index={self.device_index} "
                    f"size={self.width}x{self.height} fps={self.fps}"
                )
            except Exception as e:
                self._available = False
                self._capture = None
                self._error = str(e)
                camera_log(f"Camera unavailable: {self._error}")

    def get_status(self) -> Dict[str, Any]:
        """Return camera status for API and robot state snapshots."""
        with self._lock:
            return {
                "status": "success",
                "enabled": self.enabled,
                "available": self._available,
                "device_index": self.device_index,
                "width": self.width,
                "height": self.height,
                "fps": self.fps,
                "configured": {
                    "width": self.configured_width,
                    "height": self.configured_height,
                    "fps": self.configured_fps,
                    "auto_resolution": self.auto_resolution,
                    "prefer_max_resolution": self.prefer_max_resolution,
                },
                "selected_mode": self._selected_mode,
                "jpeg_quality": self.jpeg_quality,
                "stale_after_ms": self.stale_after_ms,
                "last_capture_at": self._last_capture_at,
                "last_frame": self._last_frame_meta,
                "error": self._error,
            }

    def capture_jpeg(self) -> tuple[bytes, Dict[str, Any]]:
        """Capture one JPEG frame from the USB camera."""
        with self._lock:
            self._require_camera()
            cv2 = self._cv2()

            frame = None
            for _ in range(self.warmup_frames + 1):
                ok, frame = self._capture.read()
                if not ok or frame is None:
                    raise CameraControllerError("camera returned no frame")

            ok, encoded = cv2.imencode(
                ".jpg",
                frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality],
            )
            if not ok:
                raise CameraControllerError("failed to encode camera frame")

            captured_at = datetime.now().isoformat(timespec="milliseconds")
            height, width = frame.shape[:2]
            self.width = int(width)
            self.height = int(height)
            jpeg_bytes = encoded.tobytes()
            meta = {
                "captured_at": captured_at,
                "width": int(width),
                "height": int(height),
                "mime": "image/jpeg",
                "bytes": len(jpeg_bytes),
            }
            self._last_capture_at = captured_at
            self._last_frame = jpeg_bytes
            self._last_frame_meta = meta
            camera_log(
                f"Captured frame {meta['width']}x{meta['height']} "
                f"{meta['bytes']} bytes"
            )
            return jpeg_bytes, dict(meta)

    def get_last_frame(self) -> tuple[bytes, Dict[str, Any]]:
        """Return the latest captured frame, capturing one if needed."""
        with self._lock:
            if self._last_frame is None or self._last_frame_meta is None:
                return self.capture_jpeg()
            return self._last_frame, dict(self._last_frame_meta)

    def release(self) -> None:
        """Release the camera device."""
        with self._lock:
            self._release_locked()
            self._available = False

    def _require_camera(self) -> None:
        if not self.enabled:
            raise CameraControllerError("camera is disabled in config.json")
        if not self._available or self._capture is None:
            message = self._error or "camera is not available"
            raise CameraControllerError(message)

    def _release_locked(self) -> None:
        if self._capture is not None:
            try:
                self._capture.release()
            except Exception as e:
                camera_log(f"Camera release error: {e}")
        self._capture = None

    def _configure_capture(self, capture, cv2) -> None:
        selected_mode = None

        if self.auto_resolution and self.prefer_max_resolution:
            selected_mode = self._detect_largest_v4l2_mode()
            if selected_mode:
                capture.set(cv2.CAP_PROP_FRAME_WIDTH, selected_mode["width"])
                capture.set(cv2.CAP_PROP_FRAME_HEIGHT, selected_mode["height"])
                camera_log(
                    "Requested largest detected camera mode "
                    f"{selected_mode['width']}x{selected_mode['height']}"
                )
            else:
                camera_log(
                    "Could not detect camera modes; using camera default resolution"
                )
        elif self.configured_width and self.configured_height:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.configured_width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.configured_height)
            selected_mode = {
                "width": self.configured_width,
                "height": self.configured_height,
                "source": "config",
            }

        if self.configured_fps:
            capture.set(cv2.CAP_PROP_FPS, self.configured_fps)

        self._selected_mode = selected_mode

    def _detect_largest_v4l2_mode(self) -> Optional[Dict[str, Any]]:
        device_path = f"/dev/video{self.device_index}"
        try:
            result = subprocess.run(
                ["v4l2-ctl", "--list-formats-ext", "-d", device_path],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except FileNotFoundError:
            return None
        except Exception as e:
            camera_log(f"Camera mode detection failed: {e}")
            return None

        if result.returncode != 0:
            camera_log(
                "Camera mode detection returned error: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
            return None

        modes = []
        for match in re.finditer(r"Size:\s+Discrete\s+(\d+)x(\d+)", result.stdout):
            width = int(match.group(1))
            height = int(match.group(2))
            modes.append((width, height))

        if not modes:
            return None

        width, height = max(modes, key=lambda item: (item[0] * item[1], item[0]))
        return {
            "width": width,
            "height": height,
            "source": "v4l2-ctl",
            "device": device_path,
        }

    def _update_actual_mode_locked(self, cv2) -> None:
        if not self._capture:
            return

        actual_width = int(self._capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        actual_height = int(self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        actual_fps = self._capture.get(cv2.CAP_PROP_FPS) or 0

        if actual_width > 0:
            self.width = actual_width
        if actual_height > 0:
            self.height = actual_height
        if actual_fps > 0:
            self.fps = float(actual_fps)

    def _parse_optional_dimension(self, value) -> Optional[int]:
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip()
            if value.lower() == "auto":
                return None
        parsed = int(value)
        if parsed < 1:
            return None
        return parsed

    def _cv2(self):
        try:
            import cv2
        except ImportError as e:
            raise CameraControllerError(
                "OpenCV is not installed; install python3-opencv on the Pi"
            ) from e
        return cv2
