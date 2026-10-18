## Addendum — independent re-check of E2-84…E2-107 (second reviewer)

Scope: every SDK claim above was re-derived from `nl -ba` of `sdk/cds_sdk/client.py`,
`sdk/cds_sdk/stream.py`, `app/api/session_stream.py` and `app/api/sandbox_tasks.py`
during a second pass, without reading the verdicts first. Verdicts confirmed: all 24.
Below are (a) line-reference corrections — several `stream.py` anchors are offset and
one `client.py` anchor points into the wrong block, and (b) two reachability
disagreements. No existing verdict was rewritten.

### (a) Corrected anchors (verified against the files as of this commit)

| claim | anchor above | verified anchor | what is actually there |
|---|---|---|---|
| E2-90 | `client.py:197-207`, `stream.py:60` | `client.py:190-208` | `has_token/has_app/has_key` at 190-192, the `>1` guard at 193-198, half app-pair 199-200, `cds_ak_` prefix 201-202, "no credential at all" 203-208. (`client.py:185-193` is docstring text; `stream.py:60` is blank.) |
| E2-100 | `client.py:77` | `client.py:65` | the `self.code = code or (...)` ternary. `:72-78` is the `Retry-After` parse, not the cited snippet. |
| E2-103 | `stream.py:133-141` + `:163-168` | `stream.py:138-144` + `:164-168` | `raw = ws.recv()` at 140, `if _is_closed_error(exc): return` at 142-143, `raise CDSError(502, ...)` at 144. |
| E2-104 | `stream.py:111-114` | `stream.py:119-122` | `path = (f"/api/v1/sandbox-sessions/{session_id}/exec/stream" f"?ticket={ticket}")`. `:49` (`ws.connect(url)`) was cited correctly. |
| E2-105 | `stream.py:46-50` | `stream.py:47-51` | `def _default_ws_open` 47, `ws.connect(url)` 49, `ws.settimeout(timeout)` 50. |
| E2-106 | `stream.py:129-133` | `stream.py:137` | `ws.send(json.dumps(frame))`; the surrounding `try:` opens at 133 and only `finally: ws.close()` (157-161) guards it. |
| E2-107 | `stream.py:130-131`, `:53-55`, `:96-98` | `stream.py:135-136`, `:54-59`, `:98-100` | `if timeout_seconds is not None:` 135 / `frame["timeout"] = int(timeout_seconds)` 136; `_build_command`'s `CDSError(400, "code cannot be empty")` 54-56. |

Server-side counterpart for E2-104/E2-107, worth having in the fix note:
`app/api/session_stream.py:127` reads the ticket from the query string
(`ticket = ws.query_params.get("ticket", "")`), so the ticket **must** stay in the URL
unless the W10 contract changes — only `quote(..., safe='')` on both interpolations is
available client-side. `app/api/session_stream.py:236-240` clamps the frame timeout:
`timeout = max(1, min(int(timeout or max_timeout), max_timeout))` inside
`except (TypeError, ValueError): timeout = max_timeout`. Consequence, precisely:
`timeout_seconds=0` is falsy so the server silently uses
`SESSION_EXEC_TIMEOUT_SECONDS`, while `timeout_seconds=-5` is clamped to **1 second** —
so the negative case is exactly the "fails late, looks like the command died" outcome the
verdict describes; a non-numeric value never reaches the server because `int()` raises in
the client first.

For E2-88, the server owns the same half of the contract:
`app/api/sandbox_tasks.py:64-71` declares `create_task(..., code: str | None = None, ...
rag_query: str | None = None)` as query parameters, so `client.py:609-620` is faithful to
it. Moving code/RAG text out of the URL is a two-sided change (backend + SDK + the
API↔FE↔SDK↔doc lock in `docs/error-codes.md`), not an SDK-only fix — relevant for
prioritising it.

### (b) Reachability disagreements (both graded the other way by this reviewer)

- **E2-86 — verdict above: TRUE·LIVE; this reviewer: TRUE·LATENT.** The code fact is
  confirmed (`client.py:117-122` catches only `ValueError` from `resp.json()`, then
  `payload.get("code")` at 127-129 assumes a dict). But nothing in `app/` can produce the
  non-object JSON body that makes it an `AttributeError`: the W2 handler emits JSON objects
  (`app/main.py:269`, `app/main.py:397`) and FastAPI's own `HTTPException` emits
  `{"detail": ...}`; a non-JSON body (proxy HTML page, uvicorn plain-text 400) lands in the
  `except ValueError` branch at `client.py:120-122` and is safe. What would wire it: a
  fronting gateway that answers errors with a JSON array or a bare JSON string. Hardening
  is still worth doing — `isinstance(payload, dict)` at 127 — but per the
  reachability rubric this is latent, not live.
- **E2-92 — verdict above: TRUE·LATENT; this reviewer: TRUE·LIVE (different vector).**
  The mis-routing the verdict needs is indeed contrived, but there is a one-call
  demonstrable loss on the *offset* branch: `list_sessions(page=2, page_size=50, limit=10)`
  fails the guard at `client.py:322` (`page == 1 and page_size == 20` is false), falls to
  the offset branch at 333-341 and **silently discards `limit`**, sending
  `skip=50&limit=50`. The caller's `limit` is dropped with no error — same class of silent
  parameter loss as E2-99, and reachable through the published method signature
  (`limit` is a documented parameter at `client.py:313`).

### (c) Confirmed unchanged (spot-checks of the riskier LIVE calls)

- E2-85 / E2-91: `_request_with_headers` (`client.py:245-254`) has no `>= 400` branch — it
  returns `(body, headers)` for any status — and `list_sessions` then does
  `body["next_cursor"] = headers.get("x-next-cursor")` at 331. A 401 on the keyset path is
  therefore returned as a "successful" listing whose dict carries the error fields, and
  `iter_sessions` (349-353) yields nothing and returns cleanly. LIVE, and the most
  consequential SDK defect in this range.
- E2-94: `_access_token` is written at `client.py:209` and `:511` and read nowhere (the
  live credential is `self._client.headers["Authorization"]`), so the dead-state half is
  real; the `_set_tokens`-clobbers-api_key scenario is unreachable because
  `self._refresh_token` is set only by `login`/`register` (532, 554) — LATENT, agreed.
- E2-96: `_TERMINAL_STATUSES = ("completed", "terminated", "failed", "expired")`
  (`client.py:13`) is used by `wait_for_status` (395) but not by `wait_for_operation`
  (451-461); the operation model only carries `pending`/`running`/`succeeded`/`failed`
  (`app/models/session_operation.py:26` default `"pending"`), so "polls until timeout on an
  unhandled terminal state" has no producer today — LATENT, agreed.
- E2-102: `session.get("sandbox_level") if isinstance(session, dict) else None`
  (`client.py:692`) then reports `(this session is None)` inside a `STREAM_UNSUPPORTED`
  message (698-704) — a wrong-session-id 404 can never reach it (`get_session` raises via
  `_request`), which is why LATENT rather than LIVE. Agreed.
