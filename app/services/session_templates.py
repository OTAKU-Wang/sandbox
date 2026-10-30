"""Session template registry (Round 40 usability) — preinstalled presets.

A template seeds a freshly provisioned session workspace with starter files
and injects extra environment variables into every ``/execute`` and ``/exec``
call for that session. Template files are written through the same session
file store as uploads (registered in ``.files_index.json``); they are stored
UNencrypted (``encrypted=False``) so sandbox code can read them immediately
without DEK plumbing — template content is boilerplate, not tenant data.

The registry is extensible two ways:

- set ``CDS_SESSION_TEMPLATES_JSON`` to a JSON object merged over the
  built-ins (same shape: ``{"name": {"description", "files", "env"}}``);
- call :func:`register_template` programmatically (tests / embedders).
"""
from __future__ import annotations

import json
import logging

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_PYTHON_ANALYSIS_MAIN = '''"""Starter analysis script — reads uploaded inputs from ./files."""
import json
from pathlib import Path


def main() -> None:
    files = sorted(Path("files").glob("*")) if Path("files").exists() else []
    print(f"inputs: {[f.name for f in files]}")
    for f in files:
        print(f"--- {f.name} ({f.stat().st_size} bytes) ---")
        if f.suffix in {".txt", ".csv", ".json", ".md"}:
            text = f.read_text(encoding="utf-8", errors="replace")
            print(text[:2000])
        else:
            print("(binary file)")


if __name__ == "__main__":
    main()
'''

_DUCKDB_QUERY_MAIN = '''"""Starter DuckDB query script.

Workspace CSVs/Parquet in ./files can be queried directly; DEK-encrypted
files must be decrypted first via CDS_DEK_HEX (see docs: session files API).
"""
import duckdb

con = duckdb.connect("session.duckdb")
print(con.execute("select version()").fetchone()[0])
print("place input files in ./files, then e.g.:")
print("  con.execute(\\"select * from read_csv_auto('files/data.csv') limit 10\\")")
'''

BUILTIN_TEMPLATES: dict[str, dict] = {
    "empty": {
        "description": "No starter files — bare provisioned workspace.",
        "files": {},
        "env": {},
    },
    "python-analysis": {
        "description": "Python analysis skeleton: lists and previews files uploaded via the session files API.",
        "files": {
            "analysis.py": _PYTHON_ANALYSIS_MAIN,
            "requirements.txt": "# add your analysis dependencies here\n",
            "README.md": "# Session workspace\n\nUpload inputs with POST /sandbox-sessions/{id}/files,\nthen run: python analysis.py\n",
        },
        "env": {},
    },
    "duckdb-query": {
        "description": "DuckDB workspace: session.duckdb created on first query; pairs with the SM4 DuckDB encryption config.",
        "files": {
            "query.py": _DUCKDB_QUERY_MAIN,
            "README.md": "# DuckDB session\n\npython query.py\n",
        },
        "env": {"CDS_DUCKDB_MODE": "session"},
    },
}


def _str_map(raw, *, where: str) -> dict[str, str]:
    """Coerce one template sub-map; a wrong-shaped entry raises so its own
    template is skipped instead of breaking session creation (A136)."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be a JSON object, got {type(raw).__name__}")
    out: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(value, bool | int | float):
            value = str(value)
        elif not isinstance(value, str):
            logger.warning(
                "[SessionTemplates] skipping %s entry %r: value must be a string, got %s",
                where, key, type(value).__name__,
            )
            continue
        out[str(key)] = value
    return out


def _merged_registry() -> dict[str, dict]:
    registry = {k: dict(v) for k, v in BUILTIN_TEMPLATES.items()}
    raw = (get_settings().SESSION_TEMPLATES_JSON or "").strip()
    if not raw:
        return registry
    try:
        override = json.loads(raw)
    except Exception as e:
        logger.warning("[SessionTemplates] ignoring invalid CDS_SESSION_TEMPLATES_JSON: %s", e)
        return registry
    if not isinstance(override, dict):
        logger.warning(
            "[SessionTemplates] ignoring CDS_SESSION_TEMPLATES_JSON: expected a JSON object, got %s",
            type(override).__name__,
        )
        return registry
    for name, tpl in override.items():
        if not isinstance(tpl, dict):
            logger.warning(
                "[SessionTemplates] ignoring template %r: expected an object, got %s",
                name, type(tpl).__name__,
            )
            continue
        try:
            registry[str(name)] = {
                "description": str(tpl.get("description", "")),
                "files": _str_map(tpl.get("files"), where=f"{name}.files"),
                "env": _str_map(tpl.get("env"), where=f"{name}.env"),
                # SS-10 CB-05: optional reference to a CDS cube
                # template (id or name) this JSON template maps to.
                "cube_template_id": tpl.get("cube_template_id"),
            }
        except Exception as e:
            logger.warning(
                "[SessionTemplates] ignoring template %r from CDS_SESSION_TEMPLATES_JSON: %s",
                name, e,
            )
    return registry


def register_template(
    name: str,
    description: str,
    files: dict[str, str],
    env: dict[str, str] | None = None,
    cube_template_id: str | None = None,
) -> None:
    """Programmatically register a template (merged over built-ins)."""
    BUILTIN_TEMPLATES[str(name)] = {
        "description": str(description),
        "files": {str(k): str(v) for k, v in (files or {}).items()},
        "env": {str(k): str(v) for k, v in (env or {}).items()},
        "cube_template_id": cube_template_id,
    }


def list_templates() -> list[dict]:
    registry = _merged_registry()
    return [
        {
            "name": name,
            "description": tpl.get("description", ""),
            "files": sorted(tpl.get("files", {}).keys()),
            "env": sorted(tpl.get("env", {}).keys()),
        }
        for name, tpl in sorted(registry.items())
    ]


def get_template(name: str) -> dict | None:
    name = (name or "").strip()
    if not name or len(name) > 64:
        return None
    return _merged_registry().get(name)


def seed_workspace(workspace, template: dict) -> dict:
    """Write the template's starter files into the workspace files dir.

    Files go through the session file store (index-registered, size/count
    limits respected) but are NOT DEK-encrypted: templates are boilerplate,
    and keeping them plaintext lets sandbox code read them with zero setup.
    Returns a summary dict for the audit trail.
    """
    from app.services import session_files as sf  # POSIX-only chain (resource); deferred

    written: list[str] = []
    for filename, content in sorted((template.get("files") or {}).items()):
        meta = sf.write_file(workspace, filename, content.encode("utf-8"), dek=None)
        written.append(meta["filename"])
    return {"files": written, "env": dict(template.get("env") or {})}
