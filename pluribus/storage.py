"""Private, crash-safe JSON persistence for local plugin state."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
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


def persistent_private_data_dir(platform_root: str | None = None) -> str:
    """Return a per-user data directory that survives plugin replacement."""

    if platform_root:
        path = platform_root
    elif sys.platform == "darwin":
        path = os.path.join(
            os.path.expanduser("~"),
            "Library",
            "Application Support",
            "ComfyUI",
            "Pluribus",
        )
    elif os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        path = os.path.join(root, "ComfyUI", "Pluribus")
    else:
        root = os.environ.get("XDG_DATA_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "share"
        )
        path = os.path.join(root, "comfyui-pluribus")
    ensure_private_dir(path)
    return path


def _copy_private_tree_missing(source: str, destination: str) -> None:
    """Copy regular files without following links or overwriting newer state."""

    ensure_private_dir(destination)
    for entry in os.scandir(source):
        if entry.is_symlink():
            continue
        destination_path = os.path.join(destination, entry.name)
        if entry.is_dir(follow_symlinks=False):
            _copy_private_tree_missing(entry.path, destination_path)
            continue
        if not entry.is_file(follow_symlinks=False) or os.path.exists(destination_path):
            continue
        directory = os.path.dirname(destination_path) or "."
        ensure_private_dir(directory)
        fd, temporary_path = tempfile.mkstemp(
            dir=directory,
            prefix=f".{entry.name}.",
            suffix=".migration.tmp",
        )
        try:
            with open(entry.path, "rb") as source_handle, os.fdopen(
                fd, "wb"
            ) as destination_handle:
                fd = -1
                shutil.copyfileobj(source_handle, destination_handle)
                destination_handle.flush()
                os.fsync(destination_handle.fileno())
            os.replace(temporary_path, destination_path)
            try:
                os.chmod(destination_path, 0o600)
            except OSError:
                pass
        except BaseException:
            if fd >= 0:
                os.close(fd)
            try:
                os.remove(temporary_path)
            except FileNotFoundError:
                pass
            raise


def migrate_private_data_dir(legacy_dir: str, destination: str) -> bool:
    """Non-destructively copy legacy plugin state and retain a private backup."""

    legacy = os.path.realpath(legacy_dir)
    target = os.path.realpath(destination)
    if legacy == target or not os.path.isdir(legacy):
        return False
    try:
        if os.path.commonpath([legacy, target]) == legacy:
            # An explicit PLURIBUS_DATA_DIR may point below the old data tree.
            # Treat it as already colocated; copying the legacy tree into one
            # of its own descendants would recurse indefinitely.
            return False
    except ValueError:
        # Different Windows drives cannot contain one another.
        pass
    ensure_private_dir(target)
    legacy_hash = hashlib.sha256(legacy.encode("utf-8")).hexdigest()
    backup_key = legacy_hash[:12]
    migration_receipt = os.path.join(
        target,
        f".legacy-migration-{backup_key}.json",
    )
    if os.path.isfile(migration_receipt):
        return False
    backup = os.path.join(target, "migration-backups", f"legacy-{backup_key}")
    _copy_private_tree_missing(legacy, backup)
    _copy_private_tree_missing(legacy, target)
    # Record the migration only after both copies are durable. This prevents
    # intentionally removed state (for example a credential deleted by
    # Disconnect) from being resurrected from the untouched legacy directory
    # on a later ComfyUI restart.
    write_private_json(
        migration_receipt,
        {
            "schemaVersion": 1,
            "legacyPathHash": legacy_hash,
        },
    )
    return True


def resolve_private_data_dir(
    plugin_dir: str,
    *,
    configured_dir: str | None = None,
    comfyui_user_dir: str | None = None,
    platform_root: str | None = None,
    temp_root: str | None = None,
) -> str:
    """Resolve persistent state and migrate the legacy ``<plugin>/data`` tree."""

    candidates: list[str] = []
    if configured_dir:
        candidates.append(configured_dir)
    if comfyui_user_dir:
        candidates.append(os.path.join(comfyui_user_dir, "pluribus"))
    destination = ""
    for candidate in candidates:
        try:
            ensure_private_dir(candidate)
            if os.access(candidate, os.W_OK):
                destination = candidate
                break
        except OSError:
            continue
    if not destination:
        try:
            destination = persistent_private_data_dir(platform_root)
        except OSError:
            destination = ""
    if not destination:
        destination = fallback_private_data_dir(plugin_dir, temp_root)
    migrate_private_data_dir(os.path.join(plugin_dir, "data"), destination)
    return destination


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
