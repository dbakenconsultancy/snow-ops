"""snow-ops – Jinja-templated SQL runner for Snowflake."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("snow-ops")
except PackageNotFoundError:
    __version__ = "unknown"
