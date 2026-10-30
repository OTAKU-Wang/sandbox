"""CDS SDK — Python client for the confidential data sandbox REST API.

Round 40 usability deliverable: wraps the session lifecycle surface
(create/execute/exec/pause/resume/refresh/terminate), the session file
store, snapshots/rollback, templates, logs and usage in a typed sync client.

Quickstart::

    from cds_sdk import CDSClient

    client = CDSClient("http://127.0.0.1:8000", token="<jwt>")
    session = client.create_session(
        data_product_id="...", contract_id="...", template="python-analysis"
    )
    client.upload_file(session["id"], "data.csv", b"col\\n1\\n")
    result = client.execute(session["id"], "print(open('files/data.csv').read())")
    print(result["output"])
"""
from pathlib import Path as _Path

from .client import CDSApiError, CDSClient, CDSResponse, CDSError

__all__ = ["CDSClient", "CDSError", "CDSApiError", "CDSResponse"]

UNKNOWN_VERSION = "0.0.0+unknown"


def _resolve_version() -> str:
    """Resolve the version from a single source of truth (A78).

    Order: ``[project].version`` in ``sdk/pyproject.toml`` when running from a
    source tree (that file is the source of truth, and the repo test suite imports
    this package off ``sys.path`` with nothing installed), then the installed
    distribution metadata for a real wheel/editable install, then
    ``UNKNOWN_VERSION`` — which discloses that the version is unknown instead of
    importing successfully with a stale number or raising ImportError.
    """
    pyproject = _Path(__file__).resolve().parents[1] / "pyproject.toml"
    try:
        import tomllib

        with pyproject.open("rb") as fh:
            project = tomllib.load(fh).get("project", {})
        name = project.get("name")
        version = project.get("version")
        if name == "cds-sdk" and isinstance(version, str) and version:
            return version
    except (ImportError, OSError, ValueError, AttributeError):
        pass
    try:
        from importlib import metadata
    except ImportError:
        return UNKNOWN_VERSION
    try:
        return metadata.version("cds-sdk")
    except metadata.PackageNotFoundError:
        return UNKNOWN_VERSION


__version__ = _resolve_version()
