"""Unstructured data pipeline — OCR/ASR/video processing inside sandbox isolation.

Processing scripts execute inside bwrap L3 sandbox for security.
Input files are mounted read-only, output is encrypted into object storage and
only release metadata passes through the shared output review path.
"""
import hashlib
import uuid
import json
import mimetypes
import shutil
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

from app.services.sandbox_runtime import BwrapAdapter


@dataclass
class PipelineTask:
    task_id: str
    task_type: str  # ocr | asr | video | document | dicom
    input_path: str
    options: dict[str, Any] = field(default_factory=dict)
    output_path: str | None = None
    status: str = "pending"  # pending | running | completed | failed
    result: dict | None = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    max_retries: int = 0
    retry_count: int = 0
    attempts: list[dict[str, Any]] = field(default_factory=list)
    stage_status: dict[str, dict[str, Any]] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PipelineResult:
    task_id: str
    task_type: str
    success: bool
    output: dict
    duration_ms: int
    error: str | None = None


class UnstructuredPipeline:
    """Orchestrates unstructured data processing inside sandbox."""

    def __init__(self, workspace_root: str = "/tmp/cds-pipeline"):
        self.workspace_root = Path(workspace_root)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self._adapter = BwrapAdapter(workspace_root=workspace_root)
        self._tasks: dict[str, PipelineTask] = {}

    def submit_task(self, task_type: str, input_path: str, options: dict | None = None) -> PipelineTask:
        """Submit a processing task. Returns task descriptor."""
        task_id = str(uuid.uuid4())[:8]
        opts = dict(options or {})
        max_retries = self._coerce_retries(opts.get("max_retries", opts.get("retries", 0)))
        task = PipelineTask(
            task_id=task_id,
            task_type=task_type,
            input_path=input_path,
            options=opts,
            max_retries=max_retries,
        )
        self._tasks[task_id] = task
        return task

    async def execute_task(self, task_id: str) -> PipelineResult:
        """Execute a submitted task inside sandbox."""
        task = self._tasks.get(task_id)
        if not task:
            return PipelineResult(task_id=task_id, task_type="unknown", success=False, output={}, duration_ms=0, error="Task not found")

        task.status = "running"
        task.started_at = datetime.now(timezone.utc)
        task.attempts = []
        task.artifacts = []
        task.error = None
        task.result = None
        task.stage_status = {}

        # Provision sandbox workspace
        workspace = self.workspace_root / task_id
        input_dir = workspace / "input"
        output_dir = workspace / "output"
        tmp_dir = workspace / "tmp"
        workspace.mkdir(parents=True, exist_ok=True)
        input_dir.mkdir(exist_ok=True)
        output_dir.mkdir(exist_ok=True)
        tmp_dir.mkdir(exist_ok=True)
        self._set_stage(task, "workspace_prepared", "completed", workspace=str(workspace))

        # Copy input file
        src = Path(task.input_path)
        if not src.exists():
            task.status = "failed"
            task.error = f"Input file not found: {task.input_path}"
            task.completed_at = datetime.now(timezone.utc)
            self._set_stage(task, "input_loaded", "failed", error=task.error)
            return PipelineResult(task_id=task_id, task_type=task.task_type, success=False, output={}, duration_ms=0, error=task.error)

        if src.is_file():
            shutil.copy2(src, input_dir / src.name)
            input_name = src.name
        else:
            shutil.copytree(src, input_dir / "data", dirs_exist_ok=True)
            input_name = "data"
        self._set_stage(task, "input_loaded", "completed", input=input_name)

        # Generate processing script
        script = self._generate_script(task.task_type, input_name, task.task_type)

        # Execute in sandbox
        total_start = datetime.now(timezone.utc)
        last_error: str | None = None
        execution_duration = 0
        for attempt in range(1, task.max_retries + 2):
            task.retry_count = attempt - 1
            self._clean_output_dir(output_dir)
            self._set_stage(task, "sandbox_execution", "running", attempt=attempt)
            try:
                start = datetime.now(timezone.utc)
                result = await self._adapter.execute(
                    f"bwrap-{task_id}", script, "python"
                )
                duration = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
                execution_duration += duration
                attempt_record = {
                    "attempt": attempt,
                    "exit_code": result.get("exit_code", -1),
                    "duration_ms": duration,
                    "output_truncated": bool(result.get("output_truncated", False)),
                }
                task.attempts.append(attempt_record)
                if result.get("exit_code") == 0:
                    self._set_stage(task, "sandbox_execution", "completed", attempt=attempt, duration_ms=duration)
                    last_error = None
                    break
                last_error = result.get("output") or f"Sandbox exited with {result.get('exit_code', -1)}"
                self._set_stage(task, "sandbox_execution", "failed", attempt=attempt, error=last_error)
            except Exception as e:
                duration = int((datetime.now(timezone.utc) - start).total_seconds() * 1000) if "start" in locals() else 0
                execution_duration += duration
                last_error = str(e)
                task.attempts.append({"attempt": attempt, "exit_code": -1, "duration_ms": duration, "error": last_error})
                self._set_stage(task, "sandbox_execution", "failed", attempt=attempt, error=last_error)

            if attempt <= task.max_retries:
                self._set_stage(task, "retry", "scheduled", next_attempt=attempt + 1, previous_error=last_error)

        if last_error is not None:
            task.status = "failed"
            task.error = last_error
            task.completed_at = datetime.now(timezone.utc)
            output = self._result_metadata(task, {}, execution_duration)
            return PipelineResult(task_id=task_id, task_type=task.task_type, success=False, output=output, duration_ms=execution_duration, error=last_error)

        # Parse output, encrypt artifacts, and run shared output review.
        output = self._parse_output(output_dir)
        task.output_path = str(output_dir)
        output["output_path"] = str(output_dir)

        try:
            artifacts = self._persist_artifacts(task, output_dir)
            task.artifacts = artifacts
            output["artifacts"] = artifacts
            self._set_stage(task, "artifact_encryption", "completed", artifact_count=len(artifacts))
        except Exception as e:
            task.status = "failed"
            task.error = f"Artifact encryption failed: {e}"
            task.completed_at = datetime.now(timezone.utc)
            self._set_stage(task, "artifact_encryption", "failed", error=str(e))
            output = self._result_metadata(task, output, execution_duration)
            return PipelineResult(task_id=task_id, task_type=task.task_type, success=False, output=output, duration_ms=execution_duration, error=task.error)

        try:
            review = self._review_output(task, output, output_dir)
            output["output_review"] = review
            self._set_stage(task, "output_review", "completed", blocked=review.get("blocked", False))
        except Exception as e:
            task.status = "failed"
            task.error = f"Output review failed: {e}"
            task.completed_at = datetime.now(timezone.utc)
            self._set_stage(task, "output_review", "failed", error=str(e))
            output = self._result_metadata(task, output, execution_duration)
            return PipelineResult(task_id=task_id, task_type=task.task_type, success=False, output=output, duration_ms=execution_duration, error=task.error)

        output = self._result_metadata(task, output, execution_duration)
        if output["output_review"].get("blocked"):
            task.status = "failed"
            task.error = "Output review blocked critical findings"
            task.completed_at = datetime.now(timezone.utc)
            task.result = output
            return PipelineResult(task_id=task_id, task_type=task.task_type, success=False, output=output, duration_ms=execution_duration, error=task.error)

        task.status = "completed"
        task.completed_at = datetime.now(timezone.utc)
        task.result = output
        return PipelineResult(task_id=task_id, task_type=task.task_type, success=True, output=output, duration_ms=execution_duration)

    def get_task(self, task_id: str) -> PipelineTask | None:
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[PipelineTask]:
        return list(self._tasks.values())

    def _generate_script(self, task_type: str, filename: str, pipeline_type: str) -> str:
        """Generate Python processing script for sandbox execution."""
        if task_type == "ocr":
            return self._ocr_script(filename)
        elif task_type == "asr":
            return self._asr_script(filename)
        elif task_type == "video":
            return self._video_script(filename)
        elif task_type == "document":
            return self._document_script(filename)
        elif task_type == "dicom":
            return self._dicom_script(filename)
        else:
            return self._generic_script(filename)

    def _ocr_script(self, filename: str) -> str:
        return f'''"""OCR processing script — runs inside sandbox."""
import json
import os
from pathlib import Path

INPUT = Path("/workspace/input")
OUTPUT = Path("/workspace/output")

results = {{"files": [], "total_pages": 0, "total_chars": 0}}

for img_file in INPUT.glob("*"):
    if img_file.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".pdf"):
        try:
            import pytesseract
            from PIL import Image
            if img_file.suffix.lower() == ".pdf":
                # PDF: convert to images then OCR
                try:
                    from pdf2image import convert_from_path
                    images = convert_from_path(str(img_file))
                    text = ""
                    for i, img in enumerate(images):
                        page_text = pytesseract.image_to_string(img, lang="chi_sim+eng")
                        text += page_text + "\\n"
                        results["total_pages"] += 1
                except Exception:
                    text = pytesseract.image_to_string(Image.open(img_file), lang="chi_sim+eng")
                    results["total_pages"] += 1
            else:
                img = Image.open(img_file)
                text = pytesseract.image_to_string(img, lang="chi_sim+eng")
                results["total_pages"] += 1

            results["files"] .append({{"filename": img_file.name, "chars": len(text)}})
            results["total_chars"] += len(text)

            # Save extracted text
            out_file = OUTPUT / f"{{img_file.stem}}.txt"
            out_file.write_text(text)
        except ImportError:
            # pytesseract not available — extract metadata only
            results["files"].append({{"filename": img_file.name, "chars": 0, "note": "OCR engine not available"}})
        except Exception as e:
            results["files"].append({{"filename": img_file.name, "error": str(e)}})

with open(OUTPUT / "result.json", "w") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(json.dumps(results, ensure_ascii=False))
'''

    def _asr_script(self, filename: str) -> str:
        return f'''"""ASR processing script — runs inside sandbox."""
import json
import os
from pathlib import Path

INPUT = Path("/workspace/input")
OUTPUT = Path("/workspace/output")

results = {{"files": [], "total_duration_sec": 0, "total_chars": 0}}

for audio_file in INPUT.glob("*"):
    if audio_file.suffix.lower() in (".wav", ".mp3", ".flac", ".ogg", ".m4a"):
        try:
            # Try whisper (OpenAI)
            import whisper
            model = whisper.load_model("base")
            result = model.transcribe(str(audio_file), language="zh")
            text = result["text"]
            duration = result.get("segments", [{{}}])[-1].get("end", 0) if result.get("segments") else 0

            results["files"].append({{"filename": audio_file.name, "chars": len(text), "duration_sec": duration}})
            results["total_duration_sec"] += duration
            results["total_chars"] += len(text)

            out_file = OUTPUT / f"{{audio_file.stem}}.txt"
            out_file.write_text(text)
        except ImportError:
            # Whisper not available — try vosk
            try:
                import wave
                import json as json_mod
                from vosk import Model, KaldiRecognizer
                wf = wave.open(str(audio_file), "rb")
                model = Model(lang="cn")
                rec = KaldiRecognizer(model, wf.getframerate())
                text = ""
                while True:
                    data = wf.readframes(4000)
                    if len(data) == 0:
                        break
                    if rec.AcceptWaveform(data):
                        result = json_mod.loads(rec.Result())
                        text += result.get("text", "")
                final = json_mod.loads(rec.FinalResult())
                text += final.get("text", "")
                results["files"].append({{"filename": audio_file.name, "chars": len(text)}})
                results["total_chars"] += len(text)
                out_file = OUTPUT / f"{{audio_file.stem}}.txt"
                out_file.write_text(text)
            except Exception:
                results["files"].append({{"filename": audio_file.name, "chars": 0, "note": "ASR engine not available"}})
        except Exception as e:
            results["files"].append({{"filename": audio_file.name, "error": str(e)}})

with open(OUTPUT / "result.json", "w") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(json.dumps(results, ensure_ascii=False))
'''

    def _video_script(self, filename: str) -> str:
        return f'''"""Video processing script — runs inside sandbox."""
import json
import os
from pathlib import Path

INPUT = Path("/workspace/input")
OUTPUT = Path("/workspace/output")

results = {{"files": [], "total_frames": 0, "total_duration_sec": 0}}

for video_file in INPUT.glob("*"):
    if video_file.suffix.lower() in (".mp4", ".avi", ".mov", ".mkv"):
        try:
            import subprocess
            # Extract metadata via ffprobe
            probe = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(video_file)],
                capture_output=True, text=True, timeout=30
            )
            if probe.returncode == 0:
                info = json.loads(probe.stdout)
                duration = float(info.get("format", {{}}).get("duration", 0))
                streams = info.get("streams", [])
                video_streams = [s for s in streams if s.get("codec_type") == "video"]
                audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

                file_result = {{
                    "filename": video_file.name,
                    "duration_sec": duration,
                    "video_streams": len(video_streams),
                    "audio_streams": len(audio_streams),
                }}

                # Extract keyframes (1 per 10 seconds)
                if video_streams:
                    r_frame_rate = video_streams[0].get("r_frame_rate", "1/1")
                    num, den = r_frame_rate.split("/")
                    fps = int(num) / int(den)
                    frame_interval = max(1, int(fps * 10))
                    frame_dir = OUTPUT / video_file.stem
                    frame_dir.mkdir(exist_ok=True)
                    subprocess.run([
                        "ffmpeg", "-i", str(video_file), "-vf", f"select=not(mod(n\\\\,{frame_interval}))",
                        "-vsync", "vfr", str(frame_dir / "frame_%04d.jpg"),
                    ], capture_output=True, timeout=60)
                    frame_count = len(list(frame_dir.glob("*.jpg")))
                    file_result["extracted_frames"] = frame_count
                    results["total_frames"] += frame_count

                results["files"].append(file_result)
                results["total_duration_sec"] += duration
            else:
                results["files"].append({{"filename": video_file.name, "error": "ffprobe failed"}})
        except Exception as e:
            results["files"].append({{"filename": video_file.name, "error": str(e)}})

with open(OUTPUT / "result.json", "w") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(json.dumps(results, ensure_ascii=False))
'''

    def _document_script(self, filename: str) -> str:
        return f'''"""Document processing script — runs inside sandbox."""
import json
import os
from pathlib import Path

INPUT = Path("/workspace/input")
OUTPUT = Path("/workspace/output")

results = {{"files": [], "total_pages": 0, "total_chars": 0}}

for doc_file in INPUT.glob("*"):
    ext = doc_file.suffix.lower()
    try:
        if ext == ".pdf":
            # PDF text extraction
            try:
                import subprocess
                result = subprocess.run(["pdftotext", str(doc_file), "-"], capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    text = result.stdout
                    pages = text.count("\\f") + 1
                    results["files"].append({{"filename": doc_file.name, "chars": len(text), "pages": pages}})
                    results["total_pages"] += pages
                    results["total_chars"] += len(text)
                    out_file = OUTPUT / f"{{doc_file.stem}}.txt"
                    out_file.write_text(text)
            except Exception as e:
                results["files"].append({{"filename": doc_file.name, "error": str(e)}})

        elif ext in (".docx", ".doc"):
            try:
                from docx import Document
                doc = Document(str(doc_file))
                text = "\\n".join(p.text for p in doc.paragraphs)
                results["files"].append({{"filename": doc_file.name, "chars": len(text)}})
                results["total_chars"] += len(text)
                out_file = OUTPUT / f"{{doc_file.stem}}.txt"
                out_file.write_text(text)
            except ImportError:
                results["files"].append({{"filename": doc_file.name, "chars": 0, "note": "python-docx not available"}})
            except Exception as e:
                results["files"].append({{"filename": doc_file.name, "error": str(e)}})

        elif ext in (".txt", ".md", ".csv"):
            text = doc_file.read_text()
            results["files"].append({{"filename": doc_file.name, "chars": len(text)}})
            results["total_chars"] += len(text)
            out_file = OUTPUT / f"{{doc_file.stem}}.txt"
            out_file.write_text(text)

    except Exception as e:
        results["files"].append({{"filename": doc_file.name, "error": str(e)}})

with open(OUTPUT / "result.json", "w") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(json.dumps(results, ensure_ascii=False))
'''

    def _dicom_script(self, filename: str) -> str:
        return f'''"""DICOM processing script — strips sensitive tags inside sandbox."""
import json
from pathlib import Path

INPUT = Path("/workspace/input")
OUTPUT = Path("/workspace/output")

SENSITIVE_TAGS = [
    "PatientName", "PatientID", "PatientBirthDate", "PatientSex",
    "PatientAddress", "PatientTelephoneNumbers", "OtherPatientIDs",
    "OtherPatientNames", "InstitutionName", "InstitutionAddress",
    "ReferringPhysicianName", "PerformingPhysicianName", "OperatorsName",
    "StudyID", "AccessionNumber",
]

results = {{"files": [], "removed_tags": [], "partial_success": False}}

for dcm_file in INPUT.glob("*"):
    if dcm_file.suffix.lower() not in (".dcm", ".dicom", ""):
        continue
    try:
        import pydicom
        ds = pydicom.dcmread(str(dcm_file), force=True)
        removed = []
        for tag in SENSITIVE_TAGS:
            if hasattr(ds, tag):
                delattr(ds, tag)
                removed.append(tag)
        out_file = OUTPUT / f"{{dcm_file.stem or 'dicom'}}_sanitized.dcm"
        ds.save_as(str(out_file))
        results["files"].append({{
            "filename": dcm_file.name,
            "sanitized_file": out_file.name,
            "removed_tags": removed,
        }})
        results["removed_tags"].extend(removed)
    except ImportError:
        results["partial_success"] = True
        results["files"].append({{
            "filename": dcm_file.name,
            "chars": 0,
            "note": "DICOM engine not available",
        }})
    except Exception as e:
        results["partial_success"] = True
        results["files"].append({{"filename": dcm_file.name, "error": str(e)}})

with open(OUTPUT / "result.json", "w") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(json.dumps(results, ensure_ascii=False))
'''

    def _generic_script(self, filename: str) -> str:
        return f'''"""Generic file processing — metadata extraction."""
import json
import os
from pathlib import Path

INPUT = Path("/workspace/input")
OUTPUT = Path("/workspace/output")

results = {{"files": []}}
for f in INPUT.iterdir():
    if f.is_file():
        results["files"].append({{
            "filename": f.name,
            "size_bytes": f.stat().st_size,
            "suffix": f.suffix,
        }})

with open(OUTPUT / "result.json", "w") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(json.dumps(results, ensure_ascii=False))
'''

    def _parse_output(self, output_dir: Path) -> dict:
        """Parse pipeline output directory."""
        result_file = output_dir / "result.json"
        if result_file.exists():
            try:
                return json.loads(result_file.read_text())
            except json.JSONDecodeError:
                pass

        # Fallback: list output files
        files = []
        for f in output_dir.rglob("*"):
            if f.is_file():
                files.append({"path": str(f.relative_to(output_dir)), "size": f.stat().st_size})
        return {"files": files}

    @staticmethod
    def _coerce_retries(value: Any) -> int:
        try:
            retries = int(value)
        except (TypeError, ValueError):
            return 0
        return max(0, min(retries, 5))

    @staticmethod
    def _clean_output_dir(output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        for child in output_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

    @staticmethod
    def _set_stage(task: PipelineTask, stage: str, status: str, **detail: Any) -> None:
        task.stage_status[stage] = {
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **detail,
        }

    def _result_metadata(self, task: PipelineTask, output: dict[str, Any], duration_ms: int) -> dict[str, Any]:
        enriched = dict(output)
        enriched["stage_status"] = task.stage_status
        enriched["retry_count"] = task.retry_count
        enriched["attempts"] = task.attempts
        enriched["duration_ms"] = duration_ms
        if task.artifacts:
            enriched["artifacts"] = task.artifacts
        return enriched

    def _persist_artifacts(self, task: PipelineTask, output_dir: Path) -> list[dict[str, Any]]:
        """Encrypt processing artifacts into object storage and return a manifest."""
        from app.services.storage_service import storage_service

        manifest: list[dict[str, Any]] = []
        base = output_dir.resolve()
        for artifact in sorted(output_dir.rglob("*")):
            if not artifact.is_file():
                continue
            if artifact.is_symlink():
                raise ValueError(f"Artifact symlink is not allowed: {artifact}")
            resolved = artifact.resolve()
            if not (resolved == base or str(resolved).startswith(str(base) + "/")):
                raise ValueError(f"Artifact escapes output directory: {artifact}")
            rel = artifact.relative_to(output_dir).as_posix()
            data = artifact.read_bytes()
            object_name = f"unstructured/{task.task_id}/{rel}"
            content_type = mimetypes.guess_type(rel)[0] or "application/octet-stream"
            upload = storage_service.upload(data, object_name, content_type=content_type)
            manifest.append({
                "path": rel,
                "storage_path": upload["path"],
                "checksum": upload["checksum"],
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "content_type": content_type,
                "encrypted": upload.get("encrypted", True),
                "key_id": upload.get("key_id"),
            })
        return manifest

    def _review_output(self, task: PipelineTask, output: dict[str, Any], output_dir: Path) -> dict[str, Any]:
        """Review output metadata and textual artifacts before releasing result metadata."""
        from app.services.output_security import inspect_text_output, inspection_to_report, safe_json_dumps

        review_text = self._collect_review_text(output, output_dir)
        result = inspect_text_output(
            review_text or safe_json_dumps(output),
            user_id=str(task.options.get("user_id") or "pipeline"),
            session_id=task.task_id,
            sandbox_mode=task.options.get("sandbox_mode") or "application",
            detail={"task_type": task.task_type, "artifact_count": len(task.artifacts)},
        )
        return inspection_to_report(result)

    def _collect_review_text(self, output: dict[str, Any], output_dir: Path, max_bytes: int = 64 * 1024) -> str:
        from app.services.output_security import safe_json_dumps

        chunks = [safe_json_dumps(self._redact_manifest_for_review(output))]
        remaining = max_bytes - len(chunks[0].encode("utf-8", errors="ignore"))
        if remaining <= 0:
            return chunks[0]

        for artifact in sorted(output_dir.rglob("*")):
            if not artifact.is_file() or artifact.is_symlink():
                continue
            if not self._is_text_artifact(artifact):
                continue
            rel = artifact.relative_to(output_dir).as_posix()
            try:
                text = artifact.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            snippet_bytes = text.encode("utf-8", errors="ignore")[:remaining]
            snippet = snippet_bytes.decode("utf-8", errors="ignore")
            if not snippet:
                continue
            chunks.append(f"\n--- artifact:{rel} ---\n{snippet}")
            remaining -= len(snippet_bytes)
            if remaining <= 0:
                break
        return "".join(chunks)

    @staticmethod
    def _is_text_artifact(path: Path) -> bool:
        if path.suffix.lower() in {".txt", ".json", ".csv", ".md", ".log", ".xml"}:
            return True
        content_type = mimetypes.guess_type(path.name)[0] or ""
        return content_type.startswith("text/")

    @staticmethod
    def _redact_manifest_for_review(output: dict[str, Any]) -> dict[str, Any]:
        redacted = dict(output)
        if "artifacts" in redacted:
            redacted["artifacts"] = [
                {
                    "path": item.get("path"),
                    "size": item.get("size"),
                    "content_type": item.get("content_type"),
                    "encrypted": item.get("encrypted"),
                }
                for item in redacted.get("artifacts", [])
                if isinstance(item, dict)
            ]
        return redacted


pipeline = UnstructuredPipeline()
