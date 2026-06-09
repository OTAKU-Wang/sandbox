"""Launch mitmproxy with CDS output inspection addon.

Provides a helper to start a mitmproxy instance configured as an upstream
proxy that inspects outbound HTTP traffic for PII leakage using the
CDSOutputInspector addon.

Usage::

    from app.services.proxy_launcher import start_proxy

    proc = start_proxy(listen_port=8080, upstream_port=8000)
    # ... later
    proc.terminate()
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Locate the addon module so mitmdump can import it.
_ADDON_MODULE = "app.services.app_output_proxy"


def start_proxy(
    listen_port: int = 8080,
    upstream_port: int = 8000,
    listen_host: str = "127.0.0.1",
    extra_args: list[str] | None = None,
) -> subprocess.Popen:
    """Start mitmdump with the CDS output inspection addon.

    Args:
        listen_port: Port the proxy listens on for inbound connections.
        upstream_port: Port of the upstream application server to forward to.
        listen_host: Address to bind the proxy listener.
        extra_args: Additional arguments passed to ``mitmdump``.

    Returns:
        A :class:`subprocess.Popen` handle for the mitmdump process.

    Raises:
        FileNotFoundError: If ``mitmdump`` is not on PATH.
        ImportError: If ``mitmproxy`` is not installed.
    """
    # Import mitmproxy to verify it is available before spawning a subprocess
    try:
        import mitmproxy  # noqa: F401  -- type: ignore[import-untyped]
    except ImportError as exc:
        raise ImportError(
            "mitmproxy is required to launch the CDS output proxy. "
            "Install it with:  pip install mitmproxy"
        ) from exc

    # Build the upstream proxy URL
    upstream_url = f"http://{listen_host}:{upstream_port}"

    cmd: list[str] = [
        sys.executable,
        "-m",
        "mitmproxy.tools.dump",
        "--mode",
        f"upstream:{upstream_url}",
        "--listen-port",
        str(listen_port),
        "--listen-host",
        listen_host,
        "--set",
        f"console_eventlog_verbosity=warn",
        "--set",
        f"flow_detail=1",
        "-s",
        _addon_script_path(),
    ]
    if extra_args:
        cmd.extend(extra_args)

    logger.info(
        "Starting mitmdump: listen=%s:%d upstream=%s",
        listen_host,
        listen_port,
        upstream_url,
    )
    logger.debug("Full command: %s", " ".join(cmd))

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    logger.info("mitmdump started (pid=%d)", proc.pid)
    return proc


def _addon_script_path() -> str:
    """Return the filesystem path to the addon module so ``mitmdump -s`` can load it.

    mitmdump's ``-s`` flag expects a path to a Python file.  We locate the
    ``app_output_proxy.py`` file relative to this module.
    """
    here = Path(__file__).resolve().parent
    addon_path = here / "app_output_proxy.py"
    if not addon_path.exists():
        raise FileNotFoundError(
            f"CDS output proxy addon not found at {addon_path}. "
            "Ensure app_output_proxy.py is in the same directory."
        )
    return str(addon_path)


# ---------------------------------------------------------------------------
# Convenience wrapper for programmatic (non-subprocess) usage
# ---------------------------------------------------------------------------

async def start_proxy_async(
    listen_port: int = 8080,
    upstream_port: int = 8000,
    listen_host: str = "127.0.0.1",
):
    """Start mitmproxy's DumpMaster in-process (async).

    This is useful when embedding the proxy inside an already-running async
    application (e.g. a FastAPI server) and you want to avoid a subprocess.

    Returns:
        The running :class:`mitmproxy.tools.dump.DumpMaster` instance.
    """
    from mitmproxy.options import Options
    from mitmproxy.tools.dump import DumpMaster

    from app.services.app_output_proxy import create_addon

    opts = Options(
        mode=[f"upstream:http://{listen_host}:{upstream_port}"],
        listen_port=listen_port,
        listen_host=listen_host,
    )
    addon = create_addon()
    master = DumpMaster(opts)
    master.addons.add(addon)

    logger.info(
        "Starting in-process mitmproxy: listen=%s:%d upstream=http://%s:%d",
        listen_host,
        listen_port,
        listen_host,
        upstream_port,
    )
    await master.run()
    return master
