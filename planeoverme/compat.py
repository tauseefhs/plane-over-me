"""Small helpers so the app runs on Python 3.9 and newer."""

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

__all__ = ["tomllib"]
