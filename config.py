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
