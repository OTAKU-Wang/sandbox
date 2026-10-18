
import sys
sys.path.insert(0, "D:/workspace/sandbox/sdk")
from cds_sdk.stream import stream_exec
from cds_sdk.client import CDSError

class FakeClient:
    class _C:  base_url = "http://x/"
    _client = _C()
    def request_ws_ticket(self): return {"ticket": "jwt.like.token"}

class WsTimeout(Exception):
    pass

class FakeWS:
    def __init__(self, frames): self.frames = list(frames); self.closed = False
    def send(self, s): pass
    def recv(self):
        if not self.frames: raise WsTimeout("timed out waiting for data")
        return self.frames.pop(0)
    def close(self): self.closed = True

frames = ['{"type":"stdout","data":"line1"}', '{"type":"stdout","data":"line2"}']
out = list(stream_exec(FakeClient(), "6dd31d5f-0000-4000-8000-000000000000", "print(1)", _ws_open=lambda u, t: FakeWS(frames)))
print("frames yielded:", [f.get("type") for f in out])
print("last frame:", out[-1] if out else None)
print("saw terminal exit frame:", any(f.get("type") in ("exit","blocked","error") for f in out))

class WsSendBoom(FakeWS):
    def send(self, s): raise RuntimeError("socket is already closed")
try:
    list(stream_exec(FakeClient(), "sid", "print(1)", _ws_open=lambda u, t: WsSendBoom([])))
except CDSError as e:
    print("send error -> CDSError (contract kept)")
except Exception as e:
    print("send error -> RAW", type(e).__name__, str(e)[:50], "| CDSError contract broken:", not isinstance(e, CDSError))

class WsConnectBoom(FakeWS):
    def __init__(self): pass
    def connect(self, u): raise RuntimeError("handshake failed")
try:
    stream_exec(FakeClient(), "sid", "print(1)", _ws_open=lambda u, t: (_ for _ in ()).throw(RuntimeError("connect failed: [Errno 110] timed out"))).send(None)
except CDSError as e:
    print("connect error message:", str(e)[:120])
