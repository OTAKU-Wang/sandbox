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


class UnstructuredDataProcessor:
    """Processes unstructured data: images, documents, audio/video."""

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
            pass
        except Exception:
            pass

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

        # PDF text extraction
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
            except Exception:
                pass

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
        except Exception:
            pass

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
        """Process XML: parse, extract structure."""
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)

        try:
            import xml.etree.ElementTree as ET
            tree = ET.parse(path)
            root = tree.getroot()
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
