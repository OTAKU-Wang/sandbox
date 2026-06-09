"""Unstructured data pipeline — OCR/ASR/video processing inside sandbox isolation.

Processing scripts execute inside bwrap L3 sandbox for security.
Input files are mounted read-only, output written to isolated workspace.
"""
import uuid
import json
import shutil
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field

from app.services.sandbox_runtime import BwrapAdapter


@dataclass
class PipelineTask:
    task_id: str
    task_type: str  # ocr | asr | video | document
    input_path: str
    output_path: str | None = None
    status: str = "pending"  # pending | running | completed | failed
    result: dict | None = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


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
        task = PipelineTask(task_id=task_id, task_type=task_type, input_path=input_path)
        self._tasks[task_id] = task
        return task

    async def execute_task(self, task_id: str) -> PipelineResult:
        """Execute a submitted task inside sandbox."""
        task = self._tasks.get(task_id)
        if not task:
            return PipelineResult(task_id=task_id, task_type="unknown", success=False, output={}, duration_ms=0, error="Task not found")

        task.status = "running"
        task.started_at = datetime.now()

        # Provision sandbox workspace
        workspace = self.workspace_root / task_id
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "input").mkdir(exist_ok=True)
        (workspace / "output").mkdir(exist_ok=True)
        (workspace / "tmp").mkdir(exist_ok=True)

        # Copy input file
        src = Path(task.input_path)
        if not src.exists():
            task.status = "failed"
            task.error = f"Input file not found: {task.input_path}"
            return PipelineResult(task_id=task_id, task_type=task.task_type, success=False, output={}, duration_ms=0, error=task.error)

        if src.is_file():
            shutil.copy2(src, workspace / "input" / src.name)
        else:
            shutil.copytree(src, workspace / "input" / "data", dirs_exist_ok=True)

        # Generate processing script
        script = self._generate_script(task.task_type, src.name, task.task_type)
        script_file = workspace / "process.py"
        script_file.write_text(script)

        # Execute in sandbox
        try:
            start = datetime.now()
            result = await self._adapter.execute(
                f"bwrap-{task_id}", script, "python"
            )
            duration = int((datetime.now() - start).total_seconds() * 1000)

            if result["exit_code"] == 0:
                task.status = "completed"
                task.completed_at = datetime.now()
                # Parse output
                output = self._parse_output(workspace / "output")
                task.result = output
                task.output_path = str(workspace / "output")
                return PipelineResult(task_id=task_id, task_type=task.task_type, success=True, output=output, duration_ms=duration)
            else:
                task.status = "failed"
                task.error = result["output"]
                return PipelineResult(task_id=task_id, task_type=task.task_type, success=False, output={}, duration_ms=duration, error=result["output"])
        except Exception as e:
            task.status = "failed"
            task.error = str(e)
            return PipelineResult(task_id=task_id, task_type=task.task_type, success=False, output={}, duration_ms=0, error=str(e))

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


pipeline = UnstructuredPipeline()
