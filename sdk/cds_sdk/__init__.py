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
from .client import CDSApiError, CDSClient, CDSError

__all__ = ["CDSClient", "CDSError", "CDSApiError"]
__version__ = "0.1.0"
