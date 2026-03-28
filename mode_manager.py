from __future__ import annotations

from models import Mode


class ModeManager:
    @staticmethod
    def parse_mode(raw_mode: str) -> Mode:
        try:
            return Mode(raw_mode.lower())
        except ValueError as error:
            valid_modes = ", ".join(mode.value for mode in Mode)
            raise ValueError(f"Unsupported mode '{raw_mode}'. Valid modes: {valid_modes}.") from error
