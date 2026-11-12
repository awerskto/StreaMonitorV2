"""Path helpers that refuse to escape their base directory.

Every file path that reaches the web layer comes from the client, so it is
untrusted. `safe_join` resolves the path and verifies that it really is inside
the base directory, which blocks `..` traversal and absolute path injection.
"""
import os


class UnsafePathError(ValueError):
    """Raised when a path would escape its base directory."""


def safe_join(base, *parts):
    """Join `parts` onto `base` and guarantee the result stays inside `base`.

    Raises UnsafePathError when the path escapes. Symlinks are resolved before
    the check, so a symlink pointing outside `base` is rejected too.
    """
    if base is None:
        raise UnsafePathError("no base directory")

    base_real = os.path.realpath(base)
    # Reject absolute parts and any parent references before joining, so that
    # the check below cannot be fooled by how the platform normalises paths.
    for part in parts:
        if part is None:
            continue
        text = str(part)
        if os.path.isabs(text) or text.startswith('/') or text.startswith('\\'):
            raise UnsafePathError(f"absolute path not allowed: {text!r}")
        if '\x00' in text:
            raise UnsafePathError("null byte in path")

    candidate = os.path.realpath(os.path.join(base_real, *[str(p) for p in parts if p is not None]))
    if candidate != base_real and not candidate.startswith(base_real + os.sep):
        raise UnsafePathError(f"path escapes base directory: {candidate!r}")
    return candidate


def safe_relpath(base, *parts):
    """Like safe_join, but returns a path relative to `base`."""
    joined = safe_join(base, *parts)
    return os.path.relpath(joined, os.path.realpath(base))


def is_within(base, path):
    """True when `path` resolves inside `base`."""
    try:
        safe_join(base, os.path.relpath(os.path.realpath(path), os.path.realpath(base)))
        return True
    except (UnsafePathError, ValueError):
        return False
