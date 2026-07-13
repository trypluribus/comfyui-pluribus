"""Private, crash-safe JSON persistence for local plugin state."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from typing import Any


def ensure_private_dir(path: str) -> None:
    """Create a data directory and restrict it to the current OS user."""
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        # Some hosted/network filesystems do not expose POSIX modes. The
        # caller can still opt into a private writable path via
        # PLURIBUS_DATA_DIR.
        pass


def fallback_private_data_dir(plugin_dir: str, temp_root: str | None = None) -> str:
    """Return a stable per-user, per-install fallback under the temp root."""
    user_key = str(os.getuid()) if hasattr(os, "getuid") else os.path.expanduser("~")
    install_key = hashlib.sha256(
        f"{user_key}:{os.path.realpath(plugin_dir)}".encode("utf-8")
    ).hexdigest()[:12]
    path = os.path.join(
        temp_root or tempfile.gettempdir(),
        f"comfyui-pluribus-{install_key}",
    )
    ensure_private_dir(path)
    return path


def write_private_json(path: str, value: Any) -> None:
    """Atomically replace JSON with a best-effort 0600 file mode."""
    directory = os.path.dirname(path) or "."
    ensure_private_dir(directory)
    fd, temporary_path = tempfile.mkstemp(
        dir=directory,
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
    )
    try:
        fchmod = getattr(os, "fchmod", None)
        if fchmod is not None:
            try:
                fchmod(fd, 0o600)
            except OSError:
                pass
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            json.dump(value, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        # Persist the directory entry as well as the file contents where the
        # platform supports directory fsync.
        directory_fd = -1
        try:
            directory_fd = os.open(directory, os.O_RDONLY)
            os.fsync(directory_fd)
        except (AttributeError, OSError):
            pass
        finally:
            if directory_fd >= 0:
                os.close(directory_fd)
    except Exception:
        if fd >= 0:
            os.close(fd)
        try:
            os.remove(temporary_path)
        except FileNotFoundError:
            pass
        raise
