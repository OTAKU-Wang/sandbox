# Verification report — claims-training.md (42 claims)

Verified against `D:/workspace/sandbox` @ branch `feat/ss-11-productization` (commit 5d5417e).
Every `file:line` below was opened and read in this session. No file in `D:/workspace/sandbox` was modified.

**Verdict rubric used** (as instructed):
- **TRUE·LIVE** — the code behaves as claimed **and** the module is reachable from the running app (`app.main` imports it).
- **TRUE·LATENT** — the code behaves as claimed, but the module has **no production caller** (dead code, exercised only by `tests/`).
- **FALSE** — the code does not behave as claimed; disproving line quoted.

---

## Reachability map

Command run:

```
cd /d/workspace/sandbox && PYTHONPATH=/d/workspace/sandbox .venv-gate/Scripts/python.exe \
  "…/rev_imports3.py" app.services.unstructured_pipeline app.services.training_pipeline \
                        app.services.training_config_validator app.services.training_audit
```

| Module | direct importers | reaches `app.main`? | via `app/api`? | dynamic import? | verdict class |
|---|---|---|---|---|---|
| `app/services/unstructured_pipeline.py` | `app.api.data_pipeline` (`:17`) | **True** | `app/api/data_pipeline.py` | none needed | **LIVE** |
| `app/services/training_pipeline.py` | **NONE** | **False** | NO | none found | **LATENT** |
| `app/services/training_config_validator.py` | **NONE** | **False** | NO | none found | **LATENT** |
| `app/services/training_audit.py` | **NONE** | **False** | NO | none found | **LATENT** |

Dynamic-import sweep (because three modules came back `False`): `rg -n 'importlib|__import__|import_module' app/ sdk/ tests/` returns **no** hit for any of the three module names — the only `importlib`/`__import__` uses in `app/` are `app/api/gateway.py:116` (`__import__("datetime")`), `app/services/retention_janitor.py:70` (registry of audit backends), `notebook_service.py:475` / `sandbox_sessions.py:1730` (in-sandbox probe strings), and `code_scanner.py:94` (a deny-list literal). `app/api/training.py` (922 L) does **not** import `training_pipeline` / `training_config_validator` / `training_audit` — a full-name grep over `app/ sdk/ deploy/ scripts/` returns only the four modules' own definitions (`training_audit.py:150`, `training_pipeline.py:374`, `training_config_validator.py:186`, `unstructured_pipeline.py:683`) plus `app/api/data_pipeline.py:17`. Each of the three dead modules is covered by its own test file (`tests/test_training_audit.py:4`, `tests/test_training_pipeline.py:3`, `tests/test_training_config_validator.py:4`), i.e. **test-only reachability**.

Live call path for `unstructured_pipeline`: `app/api/data_pipeline.py:78-79` (`POST /upload-and-process`, roles ADMIN/OPERATOR/DATA_PROVIDER) and `:134-135` (`POST /process-path`, roles ADMIN/OPERATOR) — both call `pipeline.submit_task(...)` then `await pipeline.execute_task(...)` **inline in the request**.

---

## Claims

### [8] unstructured_pipeline: retry succeeds but task reported as failed — **CRITICAL claim**

**Verdict: FALSE.**

The success branch clears `last_error` *before* breaking. Read the actual loop (`app/services/unstructured_pipeline.py:122-156`):

```python
122:  for attempt in range(1, task.max_retries + 2):
...
140:      if result.get("exit_code") == 0:
141:          self._set_stage(task, "sandbox_execution", "completed", attempt=attempt, duration_ms=duration)
142:          last_error = None          # <-- disproving line
143:          break
144:      last_error = result.get("output") or f"Sandbox exited with {result.get('exit_code', -1)}"
```

Disproving line: **`unstructured_pipeline.py:142` `last_error = None`**, immediately followed by `break` at `:143`. The gate at `:156 if last_error is not None:` is therefore `False` on any successful attempt, so `_parse_output` (`:164`), `_persist_artifacts` and `_review_output` all run. The claim's premise — "循环内只在 `last_error is None` 时才 `break`" and "最后一次迭代结束时 `last_error` 仍是第 1 次的错误值" — does not match the code; the break is on `exit_code == 0` and the sentinel is reset. The "双重语义 of `last_error`" it warns about was already resolved by the reset. No fix needed. (Only cosmetic note: `last_error` doubles as "reason for failure", which is fine given `:142`.)

---

### [9] unstructured_pipeline: sandbox execution has no explicit timeout / no total budget

**Verdict: TRUE·LIVE** (severity should be medium, not high — a default cap does exist).

- `unstructured_pipeline.py:128-130` — `await self._adapter.execute(f"bwrap-{task_id}", script, "python")`: the 4th/5th params (`session_key`, `env_vars`) and **`timeout` are all omitted** (`sandbox_runtime.py:970-972` signature).
- `sandbox_runtime.py:986` — `effective_timeout = timeout or config.resource_limits.timeout_seconds`; `config` comes from `_load_session_config`, which reads `workspace / ".security_config.json"` (`sandbox_runtime.py:709-713`) and falls back to `SandboxSecurityConfig()` when absent → `timeout_seconds = 120` (`sandbox_security.py:287`).
- This pipeline never calls `provision()` (grep for `provision` in `unstructured_pipeline.py` → **0 hits**), so no `.security_config.json` is ever written for these workspaces → every OCR/ASR/video run gets the 120 s default.
- Serialization: `max_retries` is clamped to ≤5 (`:552-558`), so `range(1, max_retries + 2)` = up to **6 sequential attempts** → worst case ~720 s held inside one HTTP request (`app/api/data_pipeline.py:79`). No `task.options["timeout"]` budget exists; `CancelledError` is a `BaseException` so the `except Exception` at `:146` does not intercept it, and the workspace is left behind.

Fix: pass an explicit per-attempt `timeout=` derived from `task.options`, and cap the total budget across attempts.

---

### [10] unstructured_pipeline: workspace lifecycle never terminated (plaintext residue in /tmp)

**Verdict: TRUE·LIVE.**

- Workspace is created by hand: `:88 workspace = self.workspace_root / task_id` (`workspace_root="/tmp/cds-pipeline"`, `:52`), `:89-96` mkdir `input`/`output`/`tmp`.
- `grep -n 'terminate|provision|to_thread' app/services/unstructured_pipeline.py` → **0 matches**: neither `provision()` nor `terminate()` is ever called, in any branch, success or failure.
- Contrast with the sanctioned path: `BwrapAdapter.provision` (`sandbox_runtime.py:920-963`) builds `{workspace_root}/{user_id}/{session_id}/` (tenant prefix, `:927`), encrypts the workspace with a per-session DEK from KMS, and writes `.security_config.json`; `BwrapAdapter.terminate` (`sandbox_runtime.py:1145-1167`) secure-wipes `.seccomp.json`, `.security_config.json`, `tmp/exec.*` and `rmtree`s the workspace.
- Consequence is exactly as claimed: `shutil.copy2/copytree` plaintext input copies (`:108`, `:111`), full OCR/ASR text artifacts (`:266`, `:304`, `:327`, `:426`, `:438`, `:449`), video frame JPEGs (`:380-383`) and pre-sanitisation DICOM stay under `/tmp/cds-pipeline/<task_id>` forever, with no per-tenant isolation directory (note `data_pipeline.py` *does* know the user: it passes `user_id` into `options` at `:78`/`:134`, but `execute_task` never uses it for the path).

Fix: `try/finally` around the execution block calling `self._adapter.terminate(f"bwrap-{task_id}")`, and put the workspace under a `{user_id}/{task_id}` layout.

---

### [11] unstructured_pipeline: DICOM de-identification incomplete, failures swallowed as `partial_success`

**Verdict: TRUE·LIVE.**

Generated script (`unstructured_pipeline.py:462-509`):

1. **Top-level tags only**: `:484-487` `for tag in SENSITIVE_TAGS: if hasattr(ds, tag): delattr(ds, tag)` — plain `delattr` on the top-level `Dataset`; no recursion into sequences (`RequestAttributesSequence`/`ReferringPhysicianName`), no private-tag sweep, no curve/overlay cleanup.
2. **UIDs retained**: `SENSITIVE_TAGS` (`:467-473`) contains no `SOPInstanceUID`/`SeriesInstanceUID`/`StudyInstanceUID`, and `:489 ds.save_as(str(out_file))` performs no UID rewrite → sanitised files stay linkable to the originals. The `_sanitized.dcm` output is then encrypted to object storage by `_persist_artifacts` (`:586-615`) and shipped out.
3. **Failure is silent to the caller**: `:503-505` `except Exception as e: results["partial_success"] = True; results["files"].append({"filename": ..., "error": str(e)})` → exit code stays 0, so the attempt counts as success at `:140`. `partial_success` is **never read anywhere in this file** (only `:475`, `:497`, `:504` set it), so `PipelineResult.success=True`. `result.json` is a text artifact (`_is_text_artifact`, `:660-664` matches `.json`), so `str(e)` plus the original filename flows into `_collect_review_text` (`:639-656`) and into `output["files"]`, which is persisted to the DB (`data_pipeline.py:83`) and returned by `GET /tasks/{task_id}`.

Fix: use `pydicom` `clean_pixel_data`/`clean_structuring_attributes` + UID remapping, and fail the task (or mark `success=False`) when `partial_success` is set.

---

### [12] unstructured_pipeline: `input_path` used unconstrained → arbitrary host file read / cross-tenant copy

**Verdict: TRUE·LIVE** (privilege-gated — not anonymous).

- `:99 src = Path(task.input_path)` → `:107-111` `shutil.copy2(src, input_dir / src.name)` / `shutil.copytree(src, input_dir/"data", ...)`. No `resolve()`, no containment check, no symlink/regular-file check anywhere on this path.
- Contrast the containment the module *does* implement for outputs: `:596-599` rejects symlink artifacts and anything not under `base` — proving the author knew the pattern and applied it only to the output side.
- Reachability: `app/api/data_pipeline.py:118-127` `POST /process-path?input_path=…` is a **raw query parameter**; the only validation is `:126 if not Path(input_path).exists()`. Route guard: `require_roles(UserRole.ADMIN, UserRole.OPERATOR)` (`:115`) — so the "attacker" is a privileged operator or a compromised operator token, not an unauthenticated caller. The `/upload-and-process` route (`:46-51`, also DATA_PROVIDER) does not take a path; its `input_path` is server-derived (`UPLOAD_ROOT/<user_id>/<filename>`).
- Exploitation shape is real: e.g. `task_type=document&input_path=/tmp/cds-uploads/<other_user_id>` → `copytree` of another tenant's uploads → the `.txt/.md/.csv` branch (`:444-449`) writes their contents into `output/`, which `_persist_artifacts` (`:586`) encrypts to object storage and `_collect_review_text` (`:639`) admits into the review payload; `_clean_output_dir`/shared `task_id` directories never scope by user. DLP (`_review_output`, `:182`) can block *release of the metadata*, but artifacts are uploaded **before** review (`:171` vs `:182`), so the data has already left the workspace.

Fix: in `submit_task`, `p = Path(input_path).resolve()` and assert `p` is under the declared upload root (reject symlink / non-regular), mirroring `:596-599`.

---

### [23] unstructured_pipeline: `"start" in locals()` as an initialised-variable test

**Verdict: TRUE·LIVE** (correctness-neutral today; hygiene only).

`:147 duration = int((datetime.now(timezone.utc) - start).total_seconds() * 1000) if "start" in locals() else 0`. `start` is bound at `:127`, the first statement inside the `try` that opens at `:126`, so in the current shape the guard is always true and the `else 0` branch is dead. The claim itself concedes "逻辑尚可"; the real objection — a `locals()` probe defeats static analysis and silently changes meaning under refactor — stands. Fix: `start: datetime | None = None` before the loop, `duration = … if start else 0`.

---

### [24] unstructured_pipeline: `result.json` consumed without shape validation

**Verdict: TRUE·LIVE** (real hardening gap; **no trigger via the five shipped script templates**).

- `:535-550` `_parse_output` returns `json.loads(result_file.read_text())` verbatim; only `json.JSONDecodeError` is caught (`:541`), so a non-dict document (list/str/int) or a non-UTF-8 file (`UnicodeDecodeError`, not caught) is returned/raised unchecked.
- Immediate consumers assume a dict: `:166 output["output_path"] = str(output_dir)`, `:194 output["output_review"].get("blocked")`, `_redact_manifest_for_review`'s `dict(output)` (`:668`), `_collect_review_text` (`:634`).
- Escape hatch: `_result_metadata` does `dict(output)` (`:580`). None of these are wrapped, so an escaping exception leaves `task.status == "running"` (set at `:77`) while the API request 500s — the status/actual mismatch the claim describes.
- Caveat recorded for honesty: all five generators always `json.dump` a dict (`:273`, `:333`, `:395`, `:454`, `:507`, `:530`), and the script text is built only from `task_type` (`:212-223`; `filename` is interpolated into none of the script bodies), so a misbehaving `result.json` is not producible by input data today.

Fix: after parse, `if not isinstance(data, dict): raise/return {}` and wrap the parse in `except Exception`.

---

### [25] unstructured_pipeline: artifacts read fully into memory before upload

**Verdict: TRUE·LIVE.**

`:601 data = artifact.read_bytes()` for every `output_dir.rglob("*")` file (`:592`), then `:604 storage_service.upload(data, object_name, content_type=...)`. The sink is byte-oriented and synchronous: `app/services/storage_service.py:103 def upload(self, data: bytes, object_name: str, content_type: str = ...)`. Peak RSS therefore equals the largest single artifact — video frame dumps (`:380-383` writes `frame_%04d.jpg` per 10 s) and de-identified DICOM (`:489`) are exactly the large cases — multiplied by concurrent in-flight requests, which is unbounded (`/upload-and-process` has no semaphore). No size ceiling exists on the artifact path.

Fix: add a per-file size cap (reject/skip over-limit) and/or a streaming/file-handle upload API.

---

### [26] unstructured_pipeline: byte/char units mixed in `_collect_review_text`, `remaining` can go negative

**Verdict: FALSE.**

Read `:635-656`:

```python
635:  remaining = max_bytes - len(chunks[0].encode("utf-8", errors="ignore"))
636:  if remaining <= 0: return chunks[0]
...
649:      snippet_bytes = text.encode("utf-8", errors="ignore")[:remaining]   # bytes[:bytes]
650:      snippet = snippet_bytes.decode("utf-8", errors="ignore")
651:      if not snippet: continue
653:      chunks.append(f"\n--- artifact:{rel} ---\n{snippet}")
654:      remaining -= len(snippet_bytes)
655:      if remaining <= 0: break
```

Three claim assertions are wrong:
1. "字符切片" — `[:remaining]` at `:649` slices a **`bytes`** object by a **byte** count; units match.
2. "`remaining` 变为负数" — impossible: `len(snippet_bytes) ≤ remaining` by construction at `:649`, so `remaining` after `:654` is always `≥ 0`.
3. "`remaining <= 0` 之后循环仍会先执行 `[:remaining]`" — the `break` at `:655-656` fires in the same iteration `remaining` reaches 0; there is no next iteration (and `[:0]` would yield `b""`, caught by `:651`).

Only real (and different) overflow: the `f"\n--- artifact:{rel} ---\n"` header at `:653` is never charged against `remaining`, so total output can exceed `max_bytes` by (header length × artifact count). Fix that if the cap must be hard; it is not the claimed mechanism.

---

### [27] unstructured_pipeline: `fps` parsing crashes and discards already-parsed metadata

**Verdict: TRUE·LIVE.**

Video script, `:374-376`:

```python
374:  r_frame_rate = video_streams[0].get("r_frame_rate", "1/1")
375:  num, den = r_frame_rate.split("/")
376:  fps = int(num) / int(den)
```

`r_frame_rate` is ffprobe-controlled (the input file is attacker-supplied): `"0/0"` → `ZeroDivisionError` at `:376`; a non-`a/b` value → unpack `ValueError` at `:375`; a non-numeric numerator → `ValueError` at `:376`. The handler is `:391-392 except Exception as e: results["files"].append({{"filename": …, "error": str(e)}})`, and crucially `results["files"].append(file_result)` sits at **`:388`** — *after* the frame-extraction block — as does `results["total_duration_sec"] += duration` at `:389`. So the exception really does discard the successfully built `file_result` and the duration, leaving only an error entry in `result.json`. Exit code stays 0 → the task reports success with silently missing metadata.

Fix: parse fps in its own `try` with `den != 0` validation, and append `file_result` before frame extraction.

---

### [28] unstructured_pipeline: `.doc` routed to python-docx; no `else` for unsupported extensions

**Verdict: TRUE·LIVE.**

- `:430 elif ext in (".docx", ".doc")` → `:432-433 from docx import Document; doc = Document(str(doc_file))`. `python-docx` accepts OOXML only; a binary OLE `.doc` raises `PackageNotFoundError` (a `ValueError` subclass, **not** `ImportError`), so it lands in `:441-442 except Exception as e: …"error": str(e)` — a guaranteed per-file failure for an extension the branch explicitly claims to support.
- The chain ends at `:444 elif ext in (".txt", ".md", ".csv"):` with **no trailing `else`** (loop body `:411-452`), so `.xlsx`, `.pptx`, `.rtf`, etc. produce no entry in `results["files"]` and never set any failure flag. Combined with exit code 0, the caller sees `success=True` with an empty result — indistinguishable from "no files were uploaded".

Fix: reject `.doc` (or route to `antiword`/`libreoffice`), and add `else: results["files"].append({"filename": …, "error": "unsupported type"})`.

---

### [29] unstructured_pipeline: module singleton holds unbounded, unlocked, reusable task state

**Verdict: TRUE·LIVE.**

- `_tasks: dict[str, PipelineTask]` declared at `:56`, written at `:70`, read at `:207`/`:210`; grep shows no `del`, `pop`, TTL, or capacity cap anywhere in the file → one retained `PipelineTask` (with full `stage_status`/`attempts`/`artifacts`) per request, for the process lifetime.
- `execute_task` resets shared state on the same object — `:77-84` (`status`, `started_at`, `attempts`, `artifacts`, `error`, `result`, `stage_status`) — so two concurrent executions of one `task_id` would overwrite each other; no `asyncio.Lock` guards `_tasks` or the workspace.
- Mitigating fact: `data_pipeline.py:78-79` always pairs `submit_task` → `execute_task` inside one request and never exposes the in-memory `task_id` to another caller, so same-id concurrency needs either a code change or the id collision in claim [31].

Fix: bound the dict (max entries + TTL, or move state to the `pipeline_task` table which already exists) and guard execution with a per-task lock / re-entrancy rejection.

---

### [30] unstructured_pipeline: blocking synchronous I/O inside `async def execute_task`

**Verdict: TRUE·LIVE.**

`execute_task` is `async` (`:73`) yet calls, with no `asyncio.to_thread` (grep `to_thread` → **0 hits** in this file):
- `:108 shutil.copy2(...)` and `:111 shutil.copytree(src, input_dir/"data", dirs_exist_ok=True)` — GB-scale video/DICOM directories, straight on the event loop;
- `:601 artifact.read_bytes()` + `:604 storage_service.upload(data, ...)` in `_persist_artifacts` — a synchronous, sequential network upload per artifact (`storage_service.py:103 def upload(self, data: bytes, …)` — no `async` variant used here).
Only the sandbox subprocess itself is off-loop (`sandbox_runtime.py:1010-1014 asyncio.wait_for(asyncio.to_thread(subprocess.run, …))`), which is what keeps the loop alive at all. While a large copy or a 50-artifact upload runs, every other request on the worker — including `/health` — is stalled.

Fix: `await asyncio.to_thread(...)` around the copies and the whole `_persist_artifacts` walk/upload, or an async storage client.

---

### [31] unstructured_pipeline: 8-hex-char `task_id` as the sole key for task map and workspace

**Verdict: TRUE·LIVE.**

`:60 task_id = str(uuid.uuid4())[:8]` → 32 bits of entropy (`16^8 ≈ 4.3e9`), used as the **only** key for `self._tasks[task_id]` (`:70`) and for the workspace directory `self.workspace_root / task_id` (`:88`). On collision the later `submit_task` silently overwrites the earlier record (`:70`, no key-free assertion) and both tasks then operate on the same `input/`/`output/` directories, with `:124 self._clean_output_dir(output_dir)` (`:560-566`, unlinks every child of `output/`) actively deleting the other task's produced artifacts before each attempt. Birthday arithmetic: at 10⁴ concurrent-ish tasks ≈1.2 %, at 10⁵ ≈ >50 %. The API layer generates its own longer ids (`data_pipeline.py:62 f"pipe-{uuid.uuid4().hex[:12]}"`) for the DB row, so the truncation is purely internal to this module and free to fix.

Fix: `task_id = uuid.uuid4().hex` (or `secrets.token_urlsafe(16)`) plus `assert task_id not in self._tasks`.

---

### [39] unstructured_pipeline: `pipeline_type` parameter is dead

**Verdict: TRUE·LIVE.**

`:212 def _generate_script(self, task_type: str, filename: str, pipeline_type: str) -> str:` — grep for `pipeline_type` in this file returns **only** that definition line; the body dispatches purely on `task_type` (`:213-223`). The sole call site duplicates the argument: `:116 script = self._generate_script(task.task_type, input_name, task.task_type)`. The sub-script generators take `filename` and none of the five templates interpolates it (each scans `/workspace/input` wholesale, e.g. `:236 INPUT.glob("*")`), which the claim also notes.

Fix: drop the parameter (and `filename`, or actually use it).

---

### [40] unstructured_pipeline: stray space in `results["files"] .append(...)`

**Verdict: TRUE·LIVE** (cosmetic).

`:261 results["files"] .append({{"filename": img_file.name, "chars": len(text)}})` inside `_ocr_script`. Every other call site in the same file is written without the space (`:271`, `:276`, `:311`, `:331`, `:393`, `:422`, `:428`, `:435`, `:442`, `:446`, `:452`, `:490`, `:505`). Legal Python, inconsistent style.

Fix: remove the space.

---

### [41] unstructured_pipeline: `total_start` assigned and never read

**Verdict: TRUE·LIVE.**

`:119 total_start = datetime.now(timezone.utc)` is its only occurrence — the full grep for `total_start|execution_duration` shows `total_start` never reappears, while every duration path uses the accumulator `execution_duration` (`:121`, `:132`, `:148`, reported at `:160-204`). Consequence the claim points at: `duration_ms` counts only sandbox attempts, never workspace prep, artifact encryption, or output review, despite `total_start` implying it did.

Fix: delete it, or use it for an end-to-end duration.

---

### [42] unstructured_pipeline: sandbox scripts write with no explicit `encoding=`

**Verdict: TRUE·LIVE** (and the highest-value of the "low" batch).

The scripts force Chinese output — `:250/:254/:258 pytesseract.image_to_string(…, lang="chi_sim+eng")`, `:291 whisper … transcribe(…, language="zh")`, `:315 vosk Model(lang="cn")` — then write it with locale-dependent calls:
- `out_file.write_text(text)` at `:266`, `:304`, `:327`, `:426`, `:438`, `:449` (6 sites), and
- `open(OUTPUT / "result.json", "w")` at `:273`, `:333`, `:395`, `:454`, `:507`, `:530` (6 sites), all with `ensure_ascii=False`,
- plus `:445 text = doc_file.read_text()` reading arbitrary user text.
`grep -n 'LANG|LC_ALL|LC_CTYPE|PYTHONIOENCODING|PYTHONUTF8' app/services/sandbox_security.py` → **no matches**: the bwrap guest is given no UTF-8 locale, so `locale.getpreferredencoding()` inside the sandbox is whatever the minimal rootfs defaults to (typically ANSI_X3.4-1968). The first non-ASCII character then raises `UnicodeEncodeError` inside the per-file `try`, which is converted into a quiet `"error": str(e)` entry (`:270`, `:452`) or, for `write_text` before the append at `:261`, loses the record — i.e. "task succeeded, all text lost", exactly as claimed.

Fix: `write_text(text, encoding="utf-8")` and `open(..., "w", encoding="utf-8")` at all 12 sites (and `read_text(encoding="utf-8", errors="replace")` at `:445`).

---

### [1] training_audit: `record()` is a lock-free check-then-append on a module singleton

**Verdict: TRUE·LATENT.**

- `:53-55 __init__` sets only `_entries` / `_job_entries` — no `threading.Lock` anywhere in the 150-line file.
- The critical region is split across `:79 prev_hash = self._entries[-1].entry_hash if self._entries else "0"*64` → hash at `:92-97` → `:99 self._entries.append(entry)` and `:100 self._job_entries.setdefault(job_id, []).append(entry)`. Two writers reading `:79` before either appends produce two entries with the same `prev_hash`, after which `verify_chain()`'s link check `:124 if entry.prev_hash != self._entries[i-1].entry_hash: return False` is permanently False. `_job_entries` vs `_entries` index skew is also real.
- Precision the claim overstates: `record()` contains **no `await`**, so two coroutines on one event loop cannot interleave inside it; only genuinely multi-threaded callers (e.g. a future `asyncio.to_thread` training worker) can. And the module is never imported by production code (see reachability map), so nothing can race today.

Fix: `self._lock = threading.Lock()` in `__init__`, wrap `:79-100` in `with self._lock:`.

---

### [2] training_audit: `actor` excluded from the hash; `:`-joined variable-length fields

**Verdict: TRUE·LATENT.**

- Hash input, `:92-96`: `f"{entry_id}:{event_type.value}:{job_id}:{entry.timestamp.isoformat()}:{prev_hash}:{_stable_json(details or {})}"` — **`actor` is absent**; the identical omission exists in the verifier at `:128-133`. `actor` is populated at `:86` and logged at `:102`, but rewriting `entry.actor` post-hoc leaves `verify_chain()` returning True — directly contradicting the file header (`:9 "Each entry is hash-chained (SM3) to prevent tampering"`) and `:47 "Immutable, hash-chained audit log"`.
- Boundary ambiguity is also real: `job_id` is caller-supplied and interpolated with bare `:` separators, so `job_id="a:b"` + `event_type`/timestamp shifted can reproduce another entry's `hash_input`.

Fix: hash a canonical encoding that includes `actor`, e.g. `_stable_json({"entry_id":…, "actor":actor, …})` in both `record()` and `verify_chain()`.

---

### [13] training_audit: caller's `details` dict is stored by reference and re-hashed later

**Verdict: TRUE·LATENT.**

`:87 details=details or {}` stores the caller's object (no copy), and `:96` hashes `_stable_json(details or {})` — the same object. `verify_chain()` recomputes from the stored reference at `:132 _stable_json(entry.details)`. Any later in-place mutation by the caller therefore flips `verify_chain()` to False (false tamper alarm), and a caller that keeps the reference can write-then-mutate. No `copy.deepcopy` anywhere in the file.

Fix: `details=copy.deepcopy(details) if details else {}` and hash `entry.details`.

---

### [14] training_audit: `_stable_json` raises on unserialisable details; `import json` is per-call

**Verdict: TRUE·LATENT.**

- `:143-146 def _stable_json(d): import json; return json.dumps(d, sort_keys=True, separators=(",", ":"))` — no `default=`, so `datetime`/`set`/`bytes`/numpy/tensor values raise `TypeError`; `sort_keys=True` additionally raises `TypeError: '<' not supported between instances of 'int' and 'str'` for mixed int/str keys.
- `record()` has no `try/except` around `:92-97`, so a single unserialisable detail aborts the business call that merely tried to log — the inverse of the "audit must not break training" rule this module exists for.
- `import json` at `:145` is inside the function, re-run by both `record()` and `verify_chain()`.

Fix: module-level `import json`, add `default=str, ensure_ascii=False`, and guard the serialisation in `record()`.

---

### [15] training_audit: hash-input construction duplicated between `record()` and `verify_chain()`

**Verdict: TRUE·LATENT.**

Byte-for-byte the same concatenation appears twice — `:92-96` (write) and `:128-133` (verify) — with no shared helper; `grep 'hash_input' app/services/training_audit.py` → exactly those two sites. Any field added to one and not the other (e.g. the `actor` fix in claim [2]) invalidates every historical entry.

Fix: extract `def _compute_hash(entry) -> str` used by both.

---

### [16] training_audit: keyless chain cannot detect tail truncation

**Verdict: TRUE·LATENT** (and structurally weaker than the claim implies).

`:113 def verify_chain(self) -> bool:` takes no anchor/count; the head is the public constant `"0"*64` (`:79`, checked at `:121`) and `entry_hash` is an unkeyed `crypto_service.sm3_hash` (`:97`, `:134`). Deleting the last *N* entries leaves the remaining chain self-consistent → still `True`. No HMAC, no SM2 signature, no persisted head anchor, no count comparison anywhere in the file (`crypto_service` is imported at `:17` but only used for hashing).
Extra fact worth flagging to the reviewer: the log is **process-memory only** — nothing in the module persists entries to disk or DB — so "tampering" requires memory access in the first place; the missing anchor matters only once this log is persisted.

Fix: persist + sign the head (`crypto_service.sm2_sign`) and let `verify_chain(expected_head_hash, expected_count)` compare against it.

---

### [32] training_audit: unbounded `_entries`/`_job_entries`, full copies on every read, mutable "Immutable" entries

**Verdict: TRUE·LATENT.**

`:54-55` two containers that only grow (`:99`, `:100`); no cap/TTL/eviction in the file. `get_job_history` `:105-107` and `get_all_entries` `:109-111` each `list(...)`-copy the whole history per call (O(n) allocation, and the copies still hand out the same mutable `AuditEntry` objects — `:31-41` is a plain `@dataclass`, despite `:47` advertising an "Immutable … audit log").

Fix: bounded ring/archive + read-only view (`MappingProxyType`/generator), and `@dataclass(frozen=True)` for `AuditEntry`.

---

### [33] training_audit: f-string passed to `logger.info` (eager formatting)

**Verdict: TRUE·LATENT** (micro-optimisation, factually correct).

`:102 logger.info(f"Audit: {event_type.value} job={job_id} actor={actor} id={entry_id}")` — the f-string is built before the level check. Single log statement in the whole module.

Fix: `logger.info("Audit: %s job=%s actor=%s id=%s", event_type.value, job_id, actor, entry_id)`.

---

### [34] training_audit: 64-bit `entry_id`, and two independent `datetime.now()` reads

**Verdict: TRUE·LATENT.**

- `:75-77 entry_id = crypto_service.sm3_hash(f"{job_id}:{event_type.value}:{datetime.now(timezone.utc).isoformat()}".encode())[:16]` — 16 hex chars = 64 bits, and the pre-image is only (job, event type, wall-clock). Two same-type events for one job landing on the same recorded timestamp yield identical `entry_id`.
- `:89 timestamp=datetime.now(timezone.utc)` is a **second**, independent read, so the timestamp inside `entry_id` and `entry.timestamp` can differ (microseconds, more under load) — `entry_id` is therefore not reproducible from the stored record, defeating the "recompute the id from the entry" audit check.

Fix: `now = datetime.now(timezone.utc)` once, and `entry_id = uuid.uuid4().hex[:16]` (or hash `now`).

---

### [3] training_config_validator: no early return before `_check_resources`, which crashes on the bad values it just flagged

**Verdict: TRUE·LATENT.**

- The only early return is `:103-104 if result.errors > 0: return result`, placed right after the required-field loop (`:97-102`). The bounds loop `:106-117` records `add_error(param, "Must be a number", …)` at `:110` and **falls through**.
- `:133 self._check_resources(config, result)` then does `:167-176`: `effective_batch = batch_size * gradient_accumulation` (`:172`), `if effective_batch > 1024` (`:173`), `estimated_memory_mb = (max_seq_length * batch_size * 4) / (1024*1024)` (`:176`). With `"batch_size": "16"`: `"16" * 1` → `str`, then `str > int` → `TypeError` at `:173`. With `"batch_size": null`: `None * 1` → `TypeError` at `:172`. So the validator — the component whose job is to return a structured `ValidationResult` — raises instead, on exactly the input it already classified invalid.
- `dp_config` non-dict is the sibling path (see claim [5]), reached at `:128-130`, i.e. also before `:133`.

Fix: `if result.errors > 0: return result` immediately before `:133`, plus `isinstance(..., (int, float))` guards inside `_check_resources`.

---

### [4] training_config_validator: architecture whitelist defeated by substring match and by warning-only severity

**Verdict: TRUE·LATENT.**

```
121:  arch = model_name.split("/")[0].lower() if "/" in model_name else model_name.lower()
123:  allowed = any(a in arch for a in ALLOWED_ARCHITECTURES)
124:  if not allowed:
125:      result.add_warning("model_name", f"Architecture '{arch}' not in whitelist", model_name)
```

1. **Substring test** (`:123`): `"evil-bert-backdoor"` → `arch = "evil-bert-backdoor"`, `"bert" in arch` → `True` → no finding at all. Same for `"gpt2-not-audit"`. The whitelist (`:72-75`) is advisory in the loosest possible sense.
2. **Namespace false positive** (`:121`): `split("/")[0]` takes the *org*, so `"deepset/roberta-base"` → `arch = "deepset"` → no allowed token is a substring → spurious warning (the suggested fix's `[-1]` is the correct half).
3. **Non-blocking** (`:125`): `add_warning` (`:45-47`) leaves `valid` untouched — only `add_error` (`:40-43`) sets `self.valid = False` — so a non-whitelisted architecture still yields `valid=True`. Docstring `:5 "Security constraints — model architecture whitelist"` is unenforced.
4. **Type**: `:120 if model_name:` then `:121 model_name.split(...)` — a JSON number (`{"model_name": 42}`) is truthy and raises `AttributeError: 'int' object has no attribute 'split'`.

Fix: `isinstance(model_name, str)` guard, exact token match against `ALLOWED_ARCHITECTURES`, `add_error` on miss.

---

### [5] training_config_validator: `dp_config` truthiness-tested but used as a mapping

**Verdict: TRUE·LATENT.**

- `:128-130 dp_config = config.get("dp_config"); if dp_config: self._validate_dp_config(dp_config, result)` — no `isinstance` check.
- `_validate_dp_config` `:146-163`: `:148 if param in dp_config` then `:149 value = dp_config[param]`, and later `:160 delta = dp_config.get("delta", 1e-5)`.
  - `dp_config = "dp"` → all `param in dp_config` substring tests False → falls to `:160` → **`AttributeError: 'str' object has no attribute 'get'`**.
  - `dp_config = ["noise_multiplier"]` → `"noise_multiplier" in [...]` True → `dp_config["noise_multiplier"]` → **`TypeError: list indices must be integers or slices, not str`**.
  Both escape `validate()` instead of becoming an `ERROR`.

Fix: `if not isinstance(dp_config, dict): result.add_error("dp_config", "Must be a mapping", str(dp_config)); return` as the first statement of `_validate_dp_config`.

---

### [6] training_config_validator: zero noise accepted as valid DP; `delta` read and discarded

**Verdict: TRUE·LATENT.**

1. `:67 "noise_multiplier": (0.0, 100.0)` — the bounds loop `:150-156` accepts `0` (inclusive range), and the only privacy-aware check is `:162-163 if isinstance(noise, (int, float)) and noise < 0.1: result.add_warning(...)` — a **warning**, so `valid` stays `True`. A `noise_multiplier: 0` "DP-SGD" config therefore passes validation with zero privacy guarantee.
2. `:160 delta = dp_config.get("delta", 1e-5)` is assigned and never read again (single occurrence in the file) — the "delta should be much less than 1/n" comment at `:158` documents a check that was never implemented; and `:69 "delta": (1e-10, 1.0)` admits `delta = 1.0`.

Fix: `noise_multiplier <= 0` → `add_error`; implement or delete the `delta` line, and tighten the delta upper bound (e.g. `<= 1e-5`).

---

### [17] training_config_validator: `bool` passes the numeric check and `NaN` passes the range check

**Verdict: TRUE·LATENT.**

`:108-116`:

```
108:  if not isinstance(value, (int, float)):
109:      result.add_error(param, "Must be a number", str(value))
110:  elif value < min_val or value > max_val:
```

1. `bool` is an `int` subclass, so `learning_rate=True` → `isinstance` passes → `1 < 1e-7` False, `1 > 1.0` False (upper bound is inclusive) → accepted as `learning_rate = 1.0`, the largest allowed value. Same for `epochs=True` → 1 epoch.
2. `NaN` comparisons are both False, so `learning_rate=float("nan")` (and `batch_size=NaN`) is reported as in-range and then flows into `_check_resources` (`:133`), where NaN arithmetic silently produces NaN (`NaN > 1024` False) — a fully invalid config validated as clean. `float("nan")`/`Infinity` are accepted by Python's default `json.loads`, so this is reachable from a JSON config body.
3. `:150-156` in `_validate_dp_config` repeats the same two checks verbatim, so `noise_multiplier=NaN` slips through the DP gate as well.

Fix: `if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value): add_error(...)`.

---

### [35] training_config_validator: parameter `field` shadows `dataclasses.field`

**Verdict: TRUE·LATENT** (maintainability only).

`:12 from dataclasses import dataclass, field` — and `field` is genuinely used at `:36 field(default_factory=list)`. The three methods re-bind the name as their first parameter: `:40 def add_error(self, field: str, …)`, `:45 def add_warning(self, field: str, …)`, `:49 def add_info(self, field: str, …)`, each passing it positionally into `ValidationIssue(...)`. Works today because none of them calls `dataclasses.field`; a future use inside those bodies gets the `str`.

Fix: rename to `field_name` (the name `validate()` already uses at `:97`).

---

### [36] training_config_validator: `logging` import and `logger` are dead

**Verdict: TRUE·LATENT.**

`:11 import logging`, `:15 logger = logging.getLogger(__name__)`. `grep -n 'logger' app/services/training_config_validator.py` → **only line 15**; there is not a single log call in the 186-line file, which is also why the crash paths in claims [3]/[5] leave no trace.

Fix: delete both, or log the type-mismatch/`dp_config`-not-a-mapping paths that indicate an upstream misconfiguration.

---

### [7] training_pipeline: `scrub_pii` returns the matched PII verbatim in `findings[].span`

**Verdict: TRUE·LATENT.**

- `:179-184`: `findings.append({"type": pii_type, "span": match.group(), "replacement": _PII_REPLACEMENTS[pii_type]})` — `:182 "span": match.group()` is the **raw** id-card/phone/email/bank-card string, from the patterns at `:101-106`.
- It is a returned field of `PIIResult` (`:93-97`, documented as `# [{type, span, replacement}]`), so any caller that serialises the stage result — `PipelineResult.metrics` (`:89`), the result JSONL, or an audit row — re-emits exactly the PII the stage just removed, contradicting the module header (`:10 "Data stays in sandbox (no raw data output)"`, `:11 "PII scrubbing before any output"`) and `PipelineConfig.forbid_original_text` (`:75`).
- LATENT because nothing constructs `TrainingPipelineManager` in production: the only importers are `tests/test_training_pipeline.py:3`.

Fix: keep `type`/`offset`/`length` + `sha256(matched)`, never `match.group()`.

---

### [18] training_pipeline: texts shorter than the n-gram window score 0.0 similarity

**Verdict: TRUE·LATENT.**

`:274 get_ngrams(text, n=5) → {text[i:i+n] for i in range(max(len(text)-n+1, 0))}`; a 4-char text yields the empty set; `:280-281 if not ngrams1 or not ngrams2: return 0.0`. So two byte-identical short strings — `"同意退款"`, `"好评"`, `"N/A"` — are declared completely dissimilar. Under the shipped defaults (`:59 dedup_enabled: bool = True`, `:60 dedup_similarity_threshold: float = 0.85`) every short duplicate escapes dedup, and duplicates then feed the split-leak in claim [21].

Fix: `if not ngrams1 or not ngrams2: return 1.0 if text1 == text2 else 0.0`.

---

### [19] training_pipeline: `compute_minhash_similarity` implements neither MinHash nor LSH

**Verdict: TRUE·LATENT.**

`:265-268` docstring: "Compute approximate Jaccard similarity **using MinHash**". Implementation `:270-284`: builds two full character-5-gram **sets** (`:274-277`) and returns `len(intersection)/len(union)` (`:283-284`) — exact Jaccard, no hash signatures, no `num_perm`, no banding, and no signature cache, so a dedup sweep is O(N²) pair comparisons each materialising up to O(len) strings, with the `get_ngrams` closure rebuilt per call (`:274`). The name invites callers to point it at GB-scale corpora.

Fix: implement MinHash signatures + LSH with caching, or rename to `jaccard_char_ngram_similarity` and state the applicability limit.

---

### [20] training_pipeline: `train_ratio` used for slicing without validation

**Verdict: TRUE·LATENT.**

`:337-358 split_dataset` validates only emptiness (`:352-353`) and then `:357 split_idx = int(len(shuffled) * train_ratio)` / `:358 return shuffled[:split_idx], shuffled[split_idx:]`. `train_ratio=-0.1` → `split_idx` negative → `shuffled[:-13]`/`shuffled[-13:]` silently yields a train set missing its tail and a 13-record "validation" set from the sorted end. `train_ratio=1.5` → `split_idx > len` → empty validation set, which then surfaces as an error in whichever MIA/eval stage consumes it. `train_ratio` is a `PipelineConfig` field (`:67`), i.e. template/externally sourced.

Fix: `if not 0.0 < train_ratio < 1.0: raise ValueError(...)` before `:357`.

---

### [21] training_pipeline: deterministic hash sort does not keep duplicate records on one side of the split

**Verdict: TRUE·LATENT.**

`:354-356` sorts by `_stable_record_key` (`:361-363`, `sha256(seed + json(record))`) — a content hash, so **identical records produce identical keys and sort adjacently** (`sorted` is stable and they are equal). `:357 split_idx = int(len(shuffled) * train_ratio)` can land inside such a run, putting the same sample in both train and validation → leakage and an inflated metric, precisely the thing the "reproducible split for audit" docstring (`:343-347`) is there to guarantee. No dedup happens in the function, and the pipeline definitions do not guarantee dedup ran first: `PIPELINE_STAGES[SFT]` puts `DEDUPLICATION` before `TRAIN_TEST_SPLIT` (`:135-145`), but `PRETRAIN` (`:125-133`) / `RAG` (`:146-152`) / `MULTIMODAL` (`:153-160`) have no `TRAIN_TEST_SPLIT` stage at all, so a caller wiring its own order gets no protection.

Fix: dedup by `_stable_record_key` (or group by key) before computing `split_idx`.

---

### [22] training_pipeline: `get_stages` hands out the internal `PIPELINE_STAGES` list

**Verdict: TRUE·LATENT.**

`:124 PIPELINE_STAGES: dict[PipelineType, list[PipelineStage]] = {...}` is a mutable class attribute; `:165-167 def get_stages(...): return self.PIPELINE_STAGES.get(pipeline_type, [])` returns the live list object, and `:374 training_pipeline = TrainingPipelineManager()` is a module singleton shared by every caller. One `get_stages(...).remove(...)` or `.sort()` mutates the global stage sequence for the whole process — including the `PII_SCRUB`/`MIA_CHECK` gates. (Today that requires a caller, and there is none outside tests.)

Fix: `return list(self.PIPELINE_STAGES.get(pipeline_type, ()))`, or freeze the table with `types.MappingProxyType` + tuples.

---

### [37] training_pipeline: explicit `null` fields crash the quality scorer

**Verdict: TRUE·LATENT.**

`:208 text = record.get("text", "") or record.get("instruction", "") + record.get("output", "")`. `+` binds tighter than `or`, so this is `record.get("text","") or (record.get("instruction","") + record.get("output",""))`. For a JSONL record `{"instruction": null, "output": null}` the `.get` defaults do **not** apply (the key exists with value `None`), so `None + None` → `TypeError: unsupported operand type(s) for +: 'NoneType' and 'NoneType'`, aborting the whole `QUALITY_FILTER` stage on one dirty row. One correction to the claim: the `+` is **not** evaluated when `text` is truthy — `or` short-circuits — so only pretrain-shaped-missing records hit it. Same pattern recurs at `:216-217` (`inst = record.get("instruction", "")` then `len(inst)`) and `:221-222`.

Fix: `text = record.get("text") or "".join(str(record.get(k) or "") for k in ("instruction", "output"))`.

---

### [38] training_pipeline: `estimate_tokens` ignores its `tokenizer` argument

**Verdict: TRUE·LATENT.**

`:365 def estimate_tokens(self, text: str, tokenizer: str = "sentencepiece") -> int:` — the body `:367-369` reads only `text`; `tokenizer` is never referenced, and `PipelineConfig.tokenizer` (`:71`) is passed through configs, so switching tokenisers changes nothing while appearing to. Docstring half is fair: `:366` documents only the Chinese coefficient while `:369` also applies `/4` to non-Chinese characters (the `1.5 chars/token` figure itself is consistent with `chinese_chars / 1.5`).

Fix: drop the parameter, or document both coefficients and dispatch on the named tokenizer.

---

## Duplicates / already-documented

No claim in this file duplicates another. None of the 42 is an instance of the excluded known/documented facts (AES-labelled-SM4 in dev, SM3→SHA-256 fallback, intentional simulation switches) — `crypto_service.sm3_hash` is *used* at `training_audit.py:75/97/134` but no claim asserts anything about which hash ran.

## Score

| file | claims | TRUE·LIVE | TRUE·LATENT | FALSE |
|---|---|---|---|---|
| `app/services/unstructured_pipeline.py` | 18 | 16 | 0 | 2 |
| `app/services/training_audit.py` | 9 | 0 | 9 | 0 |
| `app/services/training_config_validator.py` | 7 | 0 | 7 | 0 |
| `app/services/training_pipeline.py` | 8 | 0 | 8 | 0 |
| **Total** | **42** | **16** | **24** | **2** |

FALSE: **[8]** (the single CRITICAL — retry logic is correct, `last_error = None` at `unstructured_pipeline.py:142` before `break`) and **[26]** (byte/byte slicing at `:649`; `remaining` provably never goes negative, `:654-655`).

Highest-value true findings, in order: **[12]** arbitrary host path accepted as pipeline input (`:99-111`, gated ADMIN/OPERATOR via `data_pipeline.py:115-127`), **[10]** workspaces never wiped (`0` calls to `terminate`), **[11]** DICOM UIDs/nested tags retained and sanitisation failures reported as success (`:484-505`), **[42]** 12 encoding-less writes in the Chinese-processing scripts, and **[4]** the architecture whitelist that blocks nothing (`:123-125`). The 24 LATENT findings are all in three modules with **zero production importers** — before fixing any of them, decide whether `training_pipeline` / `training_config_validator` / `training_audit` are meant to be wired in (they are fully test-covered) or are dead code to delete.
