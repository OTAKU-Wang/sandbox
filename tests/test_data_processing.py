import json
import csv
import tempfile
from pathlib import Path

import pytest

from app.services.data_processing import (
    UnstructuredDataProcessor,
    SemiStructuredDataProcessor,
    ProcessingResult,
)


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


# ── UnstructuredDataProcessor ──────────────────────────────────────────────

class TestUnstructuredDataProcessor:
    def setup_method(self):
        self.proc = UnstructuredDataProcessor()

    def test_missing_file(self, tmp_dir):
        result = self.proc.process("/nonexistent/file.jpg", tmp_dir)
        assert result.success is False
        assert "not found" in result.error.lower()

    def test_unsupported_extension(self, tmp_dir):
        p = Path(tmp_dir) / "data.xyz"
        p.write_text("hello")
        result = self.proc.process(str(p), tmp_dir)
        assert result.success is False
        assert "unsupported" in result.error.lower()

    def test_image_basic_metadata(self, tmp_dir):
        p = Path(tmp_dir) / "test.png"
        # Minimal 1x1 PNG
        import struct, zlib
        def minimal_png():
            sig = b'\x89PNG\r\n\x1a\n'
            def chunk(ctype, data):
                c = ctype + data
                return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
            ihdr = struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)
            raw = zlib.compress(b'\x00\x00\x00\x00')
            return sig + chunk(b'IHDR', ihdr) + chunk(b'IDAT', raw) + chunk(b'IEND', b'')
        p.write_bytes(minimal_png())
        result = self.proc.process(str(p), tmp_dir)
        assert result.success is True
        assert result.records_processed == 1
        assert result.metadata["type"] == "image"
        assert result.metadata["filename"] == "test.png"

    def test_document_unsupported_type(self, tmp_dir):
        p = Path(tmp_dir) / "readme.doc"
        p.write_text("hello world")
        result = self.proc.process(str(p), tmp_dir)
        assert result.success is True
        assert result.metadata["type"] == "document"

    def test_media_basic(self, tmp_dir):
        p = Path(tmp_dir) / "clip.wav"
        p.write_bytes(b"RIFF" + b"\x00" * 100)
        result = self.proc.process(str(p), tmp_dir)
        assert result.success is True
        assert result.metadata["type"] == "media"


# ── SemiStructuredDataProcessor ─────────────────────────────────────────────

class TestSemiStructuredDataProcessor:
    def setup_method(self):
        self.proc = SemiStructuredDataProcessor()

    def test_missing_file(self, tmp_dir):
        result = self.proc.process("/nonexistent/file.json", tmp_dir)
        assert result.success is False

    def test_json_list(self, tmp_dir):
        p = Path(tmp_dir) / "data.json"
        p.write_text(json.dumps([{"a": 1}, {"a": 2}, {"a": 3}]))
        result = self.proc.process(str(p), tmp_dir)
        assert result.success is True
        assert result.records_processed == 3
        assert result.metadata["type"] == "json"
        assert result.metadata["schema"]["type"] == "array"

    def test_json_single_object(self, tmp_dir):
        p = Path(tmp_dir) / "single.json"
        p.write_text(json.dumps({"name": "test", "value": 42}))
        result = self.proc.process(str(p), tmp_dir)
        assert result.success is True
        assert result.records_processed == 1
        assert result.metadata["schema"]["type"] == "object"
        assert "name" in result.metadata["schema"]["properties"]

    def test_json_invalid(self, tmp_dir):
        p = Path(tmp_dir) / "bad.json"
        p.write_text("{invalid json}")
        result = self.proc.process(str(p), tmp_dir)
        assert result.success is False
        assert "invalid json" in result.error.lower()

    def test_json_flatten_option(self, tmp_dir):
        output = Path(tmp_dir) / "out"
        p = Path(tmp_dir) / "flat.json"
        data = [{"x": i} for i in range(5)]
        p.write_text(json.dumps(data))
        result = self.proc.process(str(p), str(output), options={"flatten": True})
        assert result.success is True
        assert "output_file" in result.metadata

    def test_xml_basic(self, tmp_dir):
        p = Path(tmp_dir) / "data.xml"
        p.write_text('<root><item id="1"/><item id="2"/></root>')
        result = self.proc.process(str(p), tmp_dir)
        assert result.success is True
        assert result.records_processed == 2
        assert result.metadata["root_tag"] == "root"

    def test_xml_invalid(self, tmp_dir):
        p = Path(tmp_dir) / "bad.xml"
        p.write_text("<root><unclosed>")
        result = self.proc.process(str(p), tmp_dir)
        assert result.success is False
        assert "xml" in result.error.lower()

    def test_csv_basic(self, tmp_dir):
        p = Path(tmp_dir) / "data.csv"
        with open(p, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["name", "age"])
            writer.writerow(["Alice", 30])
            writer.writerow(["Bob", 25])
        result = self.proc.process(str(p), tmp_dir)
        assert result.success is True
        assert result.records_processed == 2
        assert result.metadata["columns"] == ["name", "age"]
        assert result.metadata["column_count"] == 2

    def test_log_basic(self, tmp_dir):
        p = Path(tmp_dir) / "app.log"
        lines = [
            "INFO: started",
            "ERROR: something broke",
            "WARN: disk low",
            "ERROR: another error",
            "INFO: done",
        ]
        p.write_text("\n".join(lines))
        result = self.proc.process(str(p), tmp_dir)
        assert result.success is True
        assert result.records_processed == 5
        assert result.metadata["errors"] == 2
        assert result.metadata["warnings"] == 1

    def test_unsupported_extension(self, tmp_dir):
        p = Path(tmp_dir) / "file.xyz"
        p.write_text("data")
        result = self.proc.process(str(p), tmp_dir)
        assert result.success is False
        assert "unsupported" in result.error.lower()


# ── ProcessingResult dataclass ──────────────────────────────────────────────

def test_processing_result_defaults():
    r = ProcessingResult(success=True, records_processed=0)
    assert r.output_path is None
    assert r.error is None
    assert r.metadata is None
