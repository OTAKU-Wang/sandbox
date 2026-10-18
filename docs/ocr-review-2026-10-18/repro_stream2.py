
import sys
sys.path.insert(0, "D:/workspace/sandbox/sdk")
from cds_sdk.stream import stream_exec, _is_closed_error
from cds_sdk.client import CDSError

WebSocketTimeoutException = type("WebSocketTimeoutException", (Exception,), {})
WebSocketConnectionClosedException = type("WebSocketConnectionClosedException", (Exception,), {})
print("_is_closed_error(WebSocketTimeoutException) =", _is_closed_error(WebSocketTimeoutException()))

class FakeClient:
    class _C: base_url = "http://x/"
    _client = _C()
    def request_ws_ticket(self): return {"ticket": "eyJhbGciOiJIUzI1NiJ9.a.b"}

class FakeWS:
    def __init__(self, frames, boom=None): self.frames = list(frames); self.boom = boom or WebSocketTimeoutException
    def send(self, s): pass
    def recv(self):
        if not self.frames: raise self.boom("timed out waiting for data")
        return self.frames.pop(0)
    def close(self): pass

frames = ['{"type":"stdout","data":"line1"}', '{"type":"stdout","data":"line2"}']
out = list(stream_exec(FakeClient(), "sid", "print(1)", _ws_open=lambda u, t: FakeWS(frames)))
print("yielded:", [f.get("type") for f in out], "| terminal frame:",
      any(f.get("type") in ("exit", "blocked", "error") for f in out))
print("=> caller sees a normal StopIteration end after a 30s recv timeout, no exit frame")

class Boom(FakeWS):
    def send(self, s): raise WebSocketConnectionClosedException("socket is already closed")
try:
    list(stream_exec(FakeClient(), "sid", "x", _ws_open=lambda u, t: Boom([])))
except CDSError: print("send failure -> CDSError (contract kept)")
except Exception as e: print("send failure -> RAW " + type(e).__name__ + " (CDSError contract broken)")

class EmptyFrame(FakeWS):
    def recv(self):
        if not self.frames: return ""
        return self.frames.pop(0)
out2 = list(stream_exec(FakeClient(), "sid", "x", _ws_open=lambda u, t: EmptyFrame(['{"type":"stdout","data":"a"}'])));
print("empty-frame stop: yielded", [f.get("type") for f in out2], "-> iteration ends on empty payload")
