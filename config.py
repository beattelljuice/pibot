import json
from pathlib import Path
from typing import Dict, Any


class Config:
    """Load and manage motor configurations."""

    def __init__(self, config_path: str = "config.json"):
        """
        Load configuration from file.

        Args:
            config_path: Path to config JSON file
        """
        self.path = Path(config_path)
        self.data = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """Load JSON config file."""
        if not self.path.exists():
            raise FileNotFoundError(f"Config file not found: {self.path}")

        with open(self.path) as f:
            return json.load(f)

    def get_dc_motors(self) -> Dict[str, Dict[str, int]]:
        """Get DC motor configurations."""
        return self.data.get("dc_motors", {})

    def get_stepper_motors(self) -> Dict[str, Dict[str, list]]:
        """Get stepper motor configurations."""
        return self.data.get("stepper_motors", {})

    def get_api_config(self) -> Dict[str, Any]:
        """Get API server configuration."""
        return self.data.get("api", {"host": "0.0.0.0", "port": 5000, "debug": False})

    def get_display_config(self) -> Dict[str, Any]:
        """Get OLED display configuration."""
        return self.data.get(
            "display",
            {
                "enabled": False,
                "driver": "sh1106",
                "width": 128,
                "height": 64,
                "i2c_port": 1,
                "i2c_address": "0x3C",
                "rotate": 0,
            },
        )

    def get_camera_config(self) -> Dict[str, Any]:
        """Get USB camera configuration."""
        return self.data.get(
            "camera",
            {
                "enabled": False,
                "device_index": 0,
                "width": "auto",
                "height": "auto",
                "fps": "auto",
                "auto_resolution": True,
                "prefer_max_resolution": True,
                "jpeg_quality": 85,
                "warmup_frames": 2,
                "stale_after_ms": 2000,
            },
        )

    def get_safety_config(self) -> Dict[str, Any]:
        """Get safety supervisor configuration."""
        return self.data.get(
            "safety",
            {
                "manual_enforcement": False,
                "obstacle_enforcement": False,
                "max_drive_power": 100,
                "max_action_ms": 1500,
                "max_stepper_steps": 500,
            },
        )

    def get_ollama_config(self) -> Dict[str, Any]:
        """Get Ollama one-shot brain configuration."""
        return self.data.get(
            "ollama",
            {
                "enabled": False,
                "url": "http://localhost:11434",
                "model": "llava:latest",
                "timeout_ms": 1500,
                "include_camera": False,
                "execute_actions": False,
            },
        )
