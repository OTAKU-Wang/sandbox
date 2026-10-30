"""Data processing services for unstructured and semi-structured data."""
import json
import csv
import io
from pathlib import Path
from dataclasses import dataclass


@dataclass
class ProcessingResult:
    success: bool
    records_processed: int
    output_path: str | None = None
    error: str | None = None
    metadata: dict | None = None


# OCR-A135: XML 输入的保护上限与 DTD 拒绝。`_process_xml` 的 path 来自待处理文件
# （上传/沙箱产出），expat 默认不取外部实体（所以没有 XXE 外带面），但内部实体声明
# 就足够 billion-laughs / quadratic blowup：几 KB 的文件可以展开成 GB。
MAX_XML_INPUT_BYTES = 8 * 1024 * 1024


class XmlInputRejected(ValueError):
    """XML 声明了 DTD/实体 —— 在解析前拒绝，而不是展开之后再失败。"""


def _guard_xml_input(data: bytes) -> None:
    """拒绝任何 DTD/实体声明（billion-laughs 与二次膨胀的唯一入口）。

    用 expat 的声明钩子而不是正则扫串：钩子在 prolog 阶段触发，因此声明永远先于任何
    实体展开被看到。良构性错误这里故意放过，交给 `ET.fromstring` 报统一的 parse error。
    """
    import xml.parsers.expat as expat

    def _reject(kind: str):
        def _handler(*_args):
            raise XmlInputRejected(
                f"{kind} declaration is refused (entity-expansion DoS guard, OCR-A135)"
            )
        return _handler

    parser = expat.ParserCreate()
    parser.StartDoctypeDeclHandler = _reject("<!DOCTYPE")
    parser.EntityDeclHandler = _reject("<!ENTITY")
    parser.ExternalEntityRefHandler = lambda *_args: False
    try:
        parser.Parse(data, True)
    except expat.ExpatError:
        return


class UnstructuredDataProcessor:
    """Processes unstructured data: images, documents, audio/video."""

    @staticmethod
    def _record_extraction_error(metadata: dict, stage: str, error: str) -> None:
        metadata["partial_success"] = True
        metadata.setdefault("extraction_errors", []).append({"stage": stage, "error": error})

    def process(self, file_path: str, output_dir: str, options: dict | None = None) -> ProcessingResult:
        """Process an unstructured file."""
        path = Path(file_path)
        if not path.exists():
            return ProcessingResult(success=False, records_processed=0, error="File not found")

        ext = path.suffix.lower()
        opts = options or {}

        if ext in (".jpg", ".jpeg", ".png", ".bmp", ".tiff"):
            return self._process_image(path, output_dir, opts)
        elif ext in (".pdf", ".doc", ".docx"):
            return self._process_document(path, output_dir, opts)
        elif ext in (".mp4", ".avi", ".mov", ".wav", ".mp3"):
            return self._process_media(path, output_dir, opts)
        else:
            return ProcessingResult(success=False, records_processed=0, error=f"Unsupported file type: {ext}")

    def _process_image(self, path: Path, output_dir: str, opts: dict) -> ProcessingResult:
        """Process image: extract metadata, optional OCR."""
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)

        metadata = {
            "filename": path.name,
            "size_bytes": path.stat().st_size,
            "type": "image",
        }

        # Extract EXIF metadata if available
        try:
            from PIL import Image
            img = Image.open(path)
            metadata["width"] = img.width
            metadata["height"] = img.height
            metadata["format"] = img.format
            if hasattr(img, "_getexif") and img._getexif():
                metadata["exif"] = dict(img._getexif())
        except ImportError:
            self._record_extraction_error(metadata, "image_metadata", "Pillow is not installed")
        except Exception as e:
            self._record_extraction_error(metadata, "image_metadata", str(e))

        return ProcessingResult(success=True, records_processed=1, metadata=metadata)

    def _process_document(self, path: Path, output_dir: str, opts: dict) -> ProcessingResult:
        """Process document: extract text, metadata."""
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)

        metadata = {
            "filename": path.name,
            "size_bytes": path.stat().st_size,
            "type": "document",
        }

        if path.suffix.lower() == ".pdf":
            try:
                import subprocess
                result = subprocess.run(["pdftotext", str(path), "-"], capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    text = result.stdout
                    metadata["text_length"] = len(text)
                    metadata["pages"] = text.count("\f") + 1
                    # Save extracted text
                    out_file = output / f"{path.stem}.txt"
                    out_file.write_text(text)
                    metadata["output_file"] = str(out_file)
                    metadata["text_extraction"] = "ok"
                else:
                    stderr = (result.stderr or "").strip()
                    self._record_extraction_error(
                        metadata,
                        "pdf_text",
                        stderr or f"pdftotext exited with {result.returncode}",
                    )
                    metadata["text_extraction"] = "failed"
            except FileNotFoundError:
                self._record_extraction_error(metadata, "pdf_text", "pdftotext is not installed")
                metadata["text_extraction"] = "unavailable"
            except Exception as e:
                self._record_extraction_error(metadata, "pdf_text", str(e))
                metadata["text_extraction"] = "failed"
        else:
            metadata["text_extraction"] = "not_supported"

        return ProcessingResult(success=True, records_processed=1, metadata=metadata)

    def _process_media(self, path: Path, output_dir: str, opts: dict) -> ProcessingResult:
        """Process audio/video: extract metadata, optional transcription."""
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)

        metadata = {
            "filename": path.name,
            "size_bytes": path.stat().st_size,
            "type": "media",
        }

        # FFprobe metadata extraction
        try:
            import subprocess
            result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                probe = json.loads(result.stdout)
                metadata["duration"] = float(probe.get("format", {}).get("duration", 0))
                metadata["format"] = probe.get("format", {}).get("format_name")
                metadata["streams"] = len(probe.get("streams", []))
                metadata["media_probe"] = "ok"
            else:
                stderr = (result.stderr or "").strip()
                self._record_extraction_error(
                    metadata,
                    "media_probe",
                    stderr or f"ffprobe exited with {result.returncode}",
                )
                metadata["media_probe"] = "failed"
        except FileNotFoundError:
            self._record_extraction_error(metadata, "media_probe", "ffprobe is not installed")
            metadata["media_probe"] = "unavailable"
        except Exception as e:
            self._record_extraction_error(metadata, "media_probe", str(e))
            metadata["media_probe"] = "failed"

        return ProcessingResult(success=True, records_processed=1, metadata=metadata)


class SemiStructuredDataProcessor:
    """Processes semi-structured data: JSON, XML, logs, CSV."""

    def process(self, file_path: str, output_dir: str, options: dict | None = None) -> ProcessingResult:
        path = Path(file_path)
        if not path.exists():
            return ProcessingResult(success=False, records_processed=0, error="File not found")

        ext = path.suffix.lower()
        opts = options or {}

        if ext == ".json":
            return self._process_json(path, output_dir, opts)
        elif ext == ".xml":
            return self._process_xml(path, output_dir, opts)
        elif ext == ".csv":
            return self._process_csv(path, output_dir, opts)
        elif ext in (".log", ".txt"):
            return self._process_log(path, output_dir, opts)
        else:
            return ProcessingResult(success=False, records_processed=0, error=f"Unsupported file type: {ext}")

    def _process_json(self, path: Path, output_dir: str, opts: dict) -> ProcessingResult:
        """Process JSON: validate, flatten, extract schema."""
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)

        try:
            data = json.loads(path.read_text())
            count = len(data) if isinstance(data, list) else 1

            # Extract schema
            schema = self._extract_json_schema(data)

            metadata = {
                "filename": path.name,
                "record_count": count,
                "type": "json",
                "schema": schema,
            }

            # Save flattened version if requested
            if opts.get("flatten") and isinstance(data, list):
                flat_file = output / f"{path.stem}_flat.json"
                flat_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
                metadata["output_file"] = str(flat_file)

            return ProcessingResult(success=True, records_processed=count, metadata=metadata)
        except json.JSONDecodeError as e:
            return ProcessingResult(success=False, records_processed=0, error=f"Invalid JSON: {e}")

    def _process_xml(self, path: Path, output_dir: str, opts: dict) -> ProcessingResult:
        """Process XML: parse, extract structure.

        Input is attacker-influenced, so it is size-capped and DTD-free by policy
        before anything touches the tree builder (OCR-A135).
        """
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)

        try:
            size = path.stat().st_size
        except OSError as e:
            return ProcessingResult(success=False, records_processed=0, error=f"XML read error: {e}")
        if size > MAX_XML_INPUT_BYTES:
            return ProcessingResult(
                success=False, records_processed=0,
                error=f"XML input too large: {size} bytes exceeds {MAX_XML_INPUT_BYTES}",
            )

        try:
            import xml.etree.ElementTree as ET
            data = path.read_bytes()
            if len(data) > MAX_XML_INPUT_BYTES:
                return ProcessingResult(
                    success=False, records_processed=0,
                    error=f"XML input too large: {len(data)} bytes exceeds {MAX_XML_INPUT_BYTES}",
                )
            try:
                _guard_xml_input(data)
            except XmlInputRejected as e:
                return ProcessingResult(success=False, records_processed=0, error=f"XML rejected: {e}")
            root = ET.fromstring(data)
            count = len(list(root))

            metadata = {
                "filename": path.name,
                "record_count": count,
                "type": "xml",
                "root_tag": root.tag,
            }

            return ProcessingResult(success=True, records_processed=count, metadata=metadata)
        except Exception as e:
            return ProcessingResult(success=False, records_processed=0, error=f"XML parse error: {e}")

    def _process_csv(self, path: Path, output_dir: str, opts: dict) -> ProcessingResult:
        """Process CSV: count rows, extract headers."""
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)

        try:
            with open(path, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                headers = next(reader, None)
                count = sum(1 for _ in reader)

            metadata = {
                "filename": path.name,
                "record_count": count,
                "type": "csv",
                "columns": headers,
                "column_count": len(headers) if headers else 0,
            }

            return ProcessingResult(success=True, records_processed=count, metadata=metadata)
        except Exception as e:
            return ProcessingResult(success=False, records_processed=0, error=f"CSV error: {e}")

    def _process_log(self, path: Path, output_dir: str, opts: dict) -> ProcessingResult:
        """Process log files: count lines, extract patterns."""
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)

        try:
            text = path.read_text()
            lines = text.splitlines()

            metadata = {
                "filename": path.name,
                "line_count": len(lines),
                "type": "log",
                "size_bytes": len(text),
            }

            # Pattern extraction
            error_count = sum(1 for l in lines if "ERROR" in l.upper())
            warn_count = sum(1 for l in lines if "WARN" in l.upper())
            metadata["errors"] = error_count
            metadata["warnings"] = warn_count

            return ProcessingResult(success=True, records_processed=len(lines), metadata=metadata)
        except Exception as e:
            return ProcessingResult(success=False, records_processed=0, error=f"Log error: {e}")

    def _extract_json_schema(self, data, depth=0, max_depth=3) -> dict:
        """Extract a simple JSON schema."""
        if depth >= max_depth:
            return {"type": "object"}

        if isinstance(data, dict):
            return {"type": "object", "properties": {k: self._extract_json_schema(v, depth + 1) for k, v in list(data.items())[:10]}}
        elif isinstance(data, list):
            if data:
                return {"type": "array", "items": self._extract_json_schema(data[0], depth + 1)}
            return {"type": "array"}
        elif isinstance(data, bool):
            return {"type": "boolean"}
        elif isinstance(data, int):
            return {"type": "integer"}
        elif isinstance(data, float):
            return {"type": "number"}
        else:
            return {"type": "string"}


unstructured_processor = UnstructuredDataProcessor()
semi_structured_processor = SemiStructuredDataProcessor()
