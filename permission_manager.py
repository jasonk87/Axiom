from __future__ import annotations

from models import Mode, PermissionSet


class PermissionDeniedError(RuntimeError):
    pass


class PermissionManager:
    _MODE_PERMISSIONS = {
        Mode.CONVERSATION: PermissionSet(
            can_read_files=True,
            can_write_files=False,
            can_run_commands=False,
        ),
        Mode.PLAN: PermissionSet(
            can_read_files=True,
            can_write_files=False,
            can_run_commands=False,
        ),
        Mode.IMPLEMENT: PermissionSet(
            can_read_files=True,
            can_write_files=True,
            can_run_commands=True,
        ),
    }

    @classmethod
    def for_mode(cls, mode: Mode) -> PermissionSet:
        return cls._MODE_PERMISSIONS[mode]

    @staticmethod
    def require_read(permissions: PermissionSet) -> None:
        if not permissions.can_read_files:
            raise PermissionDeniedError(
                "File reads are not allowed in the current mode."
            )

    @staticmethod
    def require_write(permissions: PermissionSet) -> None:
        if not permissions.can_write_files:
            raise PermissionDeniedError(
                "File writes are not allowed in the current mode."
            )

    @staticmethod
    def require_command(permissions: PermissionSet) -> None:
        if not permissions.can_run_commands:
            raise PermissionDeniedError(
                "Command execution is not allowed in the current mode."
            )
