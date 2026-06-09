"""Path traversal prevention utilities.

Defense-in-depth against ../ attacks, null bytes, and symlink escapes.
"""
import os
from pathlib import Path


class PathTraversalError(ValueError):
    """Raised when a path traversal attempt is detected."""


def sanitize_filename(filename: str) -> str:
    """Sanitize a filename: strip path separators, null bytes, and dangerous chars.

    Use this for user-supplied filenames that should be plain names (no directories).
    """
    # Null byte injection
    if "\x00" in filename:
        raise PathTraversalError("Null byte in filename")
    # Strip directory traversal
    name = filename.replace("\\", "/")
    # Take only the last component
    name = name.split("/")[-1]
    # Strip leading dots (hidden files, parent dir refs)
    name = name.lstrip(".")
    if not name:
        raise PathTraversalError("Empty filename after sanitization")
    return name


def safe_resolve_within(base: Path, user_path: str) -> Path:
    """Resolve user_path and verify it stays within base directory.

    Raises PathTraversalError if the resolved path escapes base.
    """
    # Null byte injection
    if "\x00" in user_path:
        raise PathTraversalError("Null byte in path")

    base_resolved = base.resolve()
    # Resolve the combined path (handles ../, symlinks, etc.)
    target = (base / user_path).resolve()

    # Verify containment: target must start with base
    if not (target == base_resolved or str(target).startswith(str(base_resolved) + os.sep)):
        raise PathTraversalError(
            f"Path traversal detected: '{user_path}' resolves outside base directory"
        )

    return target


def safe_join_object_name(base: Path, object_name: str) -> Path:
    """Safely join an object_name to a base directory.

    Rejects traversal sequences, null bytes, and ensures result stays within base.
    """
    return safe_resolve_within(base, object_name)
