"""CI supply-chain & honesty policy locks (2026-10 ops review round, w5).

Machine locks over `.github/workflows/*.yml` + `ci/*` for findings that were
previously only prose in `ci/known-failures.md`:

1. every workflow declares least-privilege `permissions` (GITHUB_TOKEN must
   never inherit the repository default write-all);
2. third-party actions are pinned to full commit SHAs (mutable tags can be
   moved by an upstream account takeover);
3. no `${{ secrets.* }}` is injected into any step — the leak scan runs the
   checksum-pinned gitleaks binary, so no token ever reaches third-party
   code (the old gitleaks-action@v2 did exactly that);
4. the gate-test list in ci.yml is complete on disk and its count matches
   the number declared in ci/known-failures.md (gate coverage may never
   shrink silently);
5. the full-suite "known failures must not grow" budget is mechanically
   enforced by ci/check_full_suite_budget.py (no bare `|| true`, no
   `if: always()` re-run after a red gate);
6. the CPU-torch step has a deterministic final state (requirements.txt
   must not be able to silently reinstall the CUDA wheel afterwards);
7. cube-node-smoke calls install.sh via an absolute path with the TLS/token
   envs its 红线 require, keeps loop-device state idempotent + torn down,
   and protects the smoke TLS material;
8. cube-upstream-watch fails closed (a failed clone can no longer report a
   green "no drift"), passes step outputs through `env:`, validates the
   clone URL, and documents exit code 3.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WF_DIR = ROOT / ".github" / "workflows"
CI_DIR = ROOT / "ci"

WORKFLOWS = sorted(WF_DIR.glob("*.yml"))


def _load(name: str) -> dict:
    text = (WF_DIR / name).read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    assert isinstance(doc, dict), f"{name}: not a mapping"
    return doc


def _effective_permissions(wf_name: str, doc: dict) -> dict[str, dict]:
    """Map job_id -> flattened {scope: level} after workflow-level defaults."""
    wf_perm = doc.get("permissions")
    out: dict[str, dict] = {}
    for job_id, job in (doc.get("jobs") or {}).items():
        if "permissions" in job:
            perm = job["permissions"]
        else:
            perm = wf_perm
            assert perm is not None, (
                f"{wf_name}: job '{job_id}' has neither job-level nor "
                "workflow-level permissions — GITHUB_TOKEN would inherit the "
                "repository default (write-all on classic repos)"
            )
        if isinstance(perm, str):
            out[job_id] = {"__all__": perm}
        else:
            out[job_id] = dict(perm or {})
    return out


# The exact least-privilege grants each job needs. Widening any scope here is
# a deliberate, review-visible change; adding a new job forces a new entry.
EXPECTED_PERMISSIONS = {
    "ci.yml": {
        "backend": {"contents": "read"},
        "migration-pg": {"contents": "read"},
        "frontend": {"contents": "read"},
        "secrets": {"contents": "read"},
    },
    "cube-node-smoke.yml": {"node-smoke": {"contents": "read"}},
    "cube-upstream-watch.yml": {"contract-check": {"contents": "read"}},
}


def test_all_workflows_parse_as_yaml() -> None:
    assert WORKFLOWS, "no workflows found — did .github/workflows move?"
    for wf in WORKFLOWS:
        yaml.safe_load(wf.read_text(encoding="utf-8"))


def test_every_job_runs_with_declared_least_privilege() -> None:
    problems: list[str] = []
    for wf_name, expected_jobs in EXPECTED_PERMISSIONS.items():
        doc = _load(wf_name)
        effective = _effective_permissions(wf_name, doc)
        for job_id in expected_jobs:
            if job_id not in effective:
                problems.append(f"{wf_name}: expected job '{job_id}' vanished")
                continue
            got = effective[job_id]
            want = expected_jobs[job_id]
            if got != want:
                problems.append(
                    f"{wf_name}:{job_id}: permissions {got!r} != least-privilege {want!r}"
                )
        extra = set(effective) - set(expected_jobs)
        for job_id in sorted(extra):
            problems.append(
                f"{wf_name}: new job '{job_id}' without a pinned expectation in "
                "EXPECTED_PERMISSIONS (grant it explicitly, keep it minimal)"
            )
    assert not problems, "\n".join(problems)


def test_no_write_all_grants_anywhere() -> None:
    problems = []
    for wf in WORKFLOWS:
        doc = _load(wf.name)
        for job_id, perm in _effective_permissions(wf.name, doc).items():
            if perm.get("__all__") not in (None, "read", "none"):
                problems.append(f"{wf.name}:{job_id}: blanket {perm!r}")
            for scope, level in perm.items():
                if scope != "__all__" and level not in ("read", "none"):
                    problems.append(
                        f"{wf.name}:{job_id}: scope '{scope}' granted '{level}' — "
                        "every job here is read-only; justify any write in review"
                    )
    assert not problems, "\n".join(problems)


def test_third_party_actions_pinned_to_full_commit_sha() -> None:
    sha_re = re.compile(r"^[0-9a-f]{40}$")
    problems = []
    for wf in WORKFLOWS:
        doc = _load(wf.name)
        for job_id, job in (doc.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                uses = (step or {}).get("uses")
                if not uses or "@" not in uses:
                    continue
                action, ref = uses.rsplit("@", 1)
                org = action.split("/")[0]
                if org == "actions":  # GitHub first-party majors are tolerated
                    continue
                if not sha_re.match(ref):
                    problems.append(
                        f"{wf.name}:{job_id}: third-party action '{action}' pinned to "
                        f"mutable ref '{ref}' — pin the full commit SHA"
                    )
    assert not problems, "\n".join(problems)


def test_no_secret_interpolation_into_steps() -> None:
    """No `${{ secrets.* }}` may reach any step's with/env — a token passed to a
    third-party action is an exfiltration path (old gitleaks-action@v2)."""
    needle = re.compile(r"\$\{\{[^}]*\bsecrets\.", re.IGNORECASE)
    problems = []
    for wf in WORKFLOWS:
        doc = _load(wf.name)
        for job_id, job in (doc.get("jobs") or {}).items():
            for field in ("env",):
                if needle.search(json.dumps(job.get(field) or {})):
                    problems.append(f"{wf.name}:{job_id}: secrets in job env")
            for idx, step in enumerate(job.get("steps") or []):
                blob = json.dumps({k: step.get(k) for k in ("with", "env")})
                if needle.search(blob):
                    label = step.get("name") or step.get("uses") or idx
                    problems.append(f"{wf.name}:{job_id} step '{label}': secrets interpolation")
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# gate-list lock (ci.yml is the single source; ci/known-failures.md quotes its
# count; the remote lane parses the same step — do not break that shape)
# ---------------------------------------------------------------------------

GATE_STEP_PREFIX = "Gate tests"


def _gate_files() -> list[str]:
    doc = _load("ci.yml")
    steps = doc["jobs"]["backend"]["steps"]
    runs = [s.get("run", "") for s in steps if str(s.get("name", "")).startswith(GATE_STEP_PREFIX)]
    assert len(runs) == 1, "ci.yml must carry exactly one 'Gate tests' step"
    files = re.findall(r"tests/[A-Za-z0-9_./-]+\.py", runs[0])
    seen: dict[str, None] = {}
    for f in files:
        seen.setdefault(f)
    return list(seen)


def test_gate_step_shape_survives_remote_lane_parser() -> None:
    """scripts/remote-harness/source_lane.sh extracts the list from the first
    multi-line `pytest ... \\` block — the Gate step must keep that shape and
    must remain the first such block in the file."""
    text = (WF_DIR / "ci.yml").read_text(encoding="utf-8")
    first = None
    for lineno, ln in enumerate(text.splitlines(), 1):
        if "pytest" in ln and ln.rstrip().endswith("\\"):
            first = lineno
            break
    assert first is not None, "no line-continuation pytest block found at all"
    doc = _load("ci.yml")
    steps = doc["jobs"]["backend"]["steps"]
    gate_idx = next(
        i for i, s in enumerate(steps) if str(s.get("name", "")).startswith(GATE_STEP_PREFIX)
    )
    gate_run = steps[gate_idx]["run"]
    assert "pytest" in gate_run and gate_run.rstrip().splitlines()[0].endswith("\\")
    # everything containing pytest *before* the gate step must not be a
    # multi-line pytest invocation
    earlier = "\n".join(steps[i].get("run", "") for i in range(gate_idx))
    assert not any(
        "pytest" in ln and ln.rstrip().endswith("\\") for ln in earlier.splitlines()
    ), "a step before 'Gate tests' now contains a multi-line pytest call — "
    "remote-lane parsing would grab the wrong block"


def test_gate_files_exist_on_disk() -> None:
    missing = [f for f in _gate_files() if not (ROOT / f).exists()]
    assert not missing, f"gate list references files that do not exist: {missing}"


def test_gate_count_matches_known_failures_doc() -> None:
    count = len(_gate_files())
    doc = (CI_DIR / "known-failures.md").read_text(encoding="utf-8")
    m = re.search(r"当前\s*\*\*(\d+)\s*项\*\*", doc)
    assert m, "ci/known-failures.md lost its '当前 **N** 项' gate-count declaration"
    declared = int(m.group(1))
    assert declared == count, (
        f"ci.yml gate list has {count} files but ci/known-failures.md declares "
        f"{declared} — gate coverage drifted; update ci.yml and this sentence together"
    )


# ---------------------------------------------------------------------------
# full-suite "failure budget must not grow" machine gate
# ---------------------------------------------------------------------------

BUDGET_SCRIPT = CI_DIR / "check_full_suite_budget.py"
BUDGET_BASELINE = CI_DIR / "full-suite-baseline.json"


def _junit(path: Path, failures: int, errors: int, skipped: int, tests: int) -> Path:
    ET.ElementTree(
        ET.fromstring(
            f'<testsuites><testsuite tests="{tests}" failures="{failures}" '
            f'errors="{errors}" skipped="{skipped}"/></testsuites>'
        )
    ).write(path, encoding="utf-8")
    return path


def _run_budget(report: Path, baseline: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(BUDGET_SCRIPT), str(report), str(baseline)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**__import__("os").environ, "GITHUB_STEP_SUMMARY": ""},
    )


def test_budget_script_exists_and_is_wired_into_ci() -> None:
    assert BUDGET_SCRIPT.exists(), "ci/check_full_suite_budget.py missing — soft gate unimplemented"
    baseline = json.loads(BUDGET_BASELINE.read_text(encoding="utf-8"))
    assert "established" in baseline
    doc = _load("ci.yml")
    steps = doc["jobs"]["backend"]["steps"]
    assert any("check_full_suite_budget.py" in s.get("run", "") for s in steps), (
        "full-suite budget script is not invoked by ci.yml"
    )
    assert any("upload-artifact" in str(s.get("uses", "")) for s in steps), (
        "report.xml is never uploaded — failure details must survive the run"
    )
    suite = next(s for s in steps if "Full suite" in str(s.get("name", "")))
    run = suite.get("run", "")
    assert "|| true" not in run, "bare `|| true` swallows the suite exit code"
    assert suite.get("if") not in ("always()", "${{ always() }}"), (
        "if: always() re-runs the full suite after a red gate — wasted budget; "
        "gate failure must skip the informational layer"
    )


def test_budget_script_fails_on_growth(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps({"established": True, "failures": 2, "errors": 0}), encoding="utf-8"
    )
    over = _junit(tmp_path / "over.xml", failures=3, errors=0, skipped=0, tests=10)
    eq = _junit(tmp_path / "eq.xml", failures=2, errors=0, skipped=0, tests=10)
    err_over = _junit(tmp_path / "err.xml", failures=0, errors=3, skipped=0, tests=10)
    assert _run_budget(over, baseline).returncode == 1
    assert _run_budget(eq, baseline).returncode == 0
    assert _run_budget(err_over, baseline).returncode == 1


def test_budget_script_honest_when_baseline_unestablished(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"established": False}), encoding="utf-8")
    rep = _junit(tmp_path / "r.xml", failures=99, errors=0, skipped=0, tests=100)
    proc = _run_budget(rep, baseline)
    assert proc.returncode == 0, proc.stderr
    assert "::warning::" in proc.stdout, (
        "an unestablished baseline must loudly say it only counts, not silently pass"
    )
    # missing report on an established lane is an error, not a shrug
    baseline.write_text(
        json.dumps({"established": True, "failures": 0, "errors": 0}), encoding="utf-8"
    )
    assert _run_budget(tmp_path / "nope.xml", baseline).returncode == 1


# ---------------------------------------------------------------------------
# deterministic CPU-torch final state
# ---------------------------------------------------------------------------

def test_torch_cpu_wheel_is_the_final_state() -> None:
    doc = _load("ci.yml")
    steps = doc["jobs"]["backend"]["steps"]
    req_idx = next(
        i for i, s in enumerate(steps) if "requirements.txt" in s.get("run", "")
    )
    cpu_idx = [
        i
        for i, s in enumerate(steps)
        if "download.pytorch.org/whl/cpu" in s.get("run", "")
        and "force-reinstall" in s.get("run", "")
    ]
    assert cpu_idx, "no same-version --force-reinstall step pinning the CPU wheel"
    assert max(cpu_idx) > req_idx, (
        "the CPU-wheel guarantee must run AFTER requirements.txt, otherwise a "
        "re-resolution may silently reinstall the CUDA wheel from PyPI"
    )
    guard = steps[max(cpu_idx)]["run"]
    assert "torch.version.cuda" in guard, "final state must assert a CPU build explicitly"


# ---------------------------------------------------------------------------
# frontend lane actually checks the frontend
# ---------------------------------------------------------------------------

def test_frontend_runs_tests_and_lint() -> None:
    doc = _load("ci.yml")
    steps = doc["jobs"]["frontend"]["steps"]
    runs = [s.get("run", "") for s in steps]
    assert any("npm test" in r for r in runs), "frontend job must run vitest"
    lint = [s for s in steps if "npm run lint" in s.get("run", "")]
    assert lint, "frontend job must at least run lint (informational baseline)"
    assert lint[0].get("continue-on-error") is True, (
        "lint is an informational baseline (13 known errors, 2026-10-16 FE lane): "
        "continue-on-error + warning, never a silent drop"
    )


# ---------------------------------------------------------------------------
# cube-node-smoke
# ---------------------------------------------------------------------------

SMOKE = "cube-node-smoke.yml"
SMOKE_INSTALL_NEEDLES = (
    "CDS_CUBE_TLS_CERT",
    "CDS_CUBE_TLS_KEY",
    "CDS_CUBE_TLS_CA",
    "CDS_CUBE_AUDIT_SERVICE_TOKEN",
)


def _smoke_steps() -> list[dict]:
    return _load(SMOKE)["jobs"]["node-smoke"]["steps"]


def test_smoke_install_uses_absolute_path_and_required_env() -> None:
    install = next(s for s in _smoke_steps() if "install" in str(s.get("name", "")))
    run = install["run"]
    assert "deploy/cube-node/install.sh" in run
    assert re.search(r"(REPO_DIR|GITHUB_WORKSPACE)\S*\}?/deploy/cube-node/install\.sh", run), (
        "install.sh is invoked by a relative path — after `cd /tmp/cds-smoke-tls` "
        "it can never resolve (set -e fails the step)"
    )
    for needle in SMOKE_INSTALL_NEEDLES:
        assert needle in run, f"install step must export {needle} — install.sh dies without it"
    assert "umask 077" in run or "chmod 600 tls.key" in run, (
        "smoke TLS key lands world-readable in shared /tmp"
    )


def test_smoke_loop_setup_is_idempotent_and_torn_down() -> None:
    steps = _smoke_steps()
    setup = next(s for s in steps if "XFS" in str(s.get("name", "")))
    run = setup["run"]
    assert "blkid" in run, (
        "mkfs.xfs -f must be guarded by a filesystem probe — re-running after a "
        "runner reboot would otherwise reformat a data-bearing loop image"
    )
    teardown = [
        s
        for s in steps
        if s.get("if") in ("always()", "${{ always() }}")
        and "umount" in s.get("run", "")
        and "losetup -d" in s.get("run", "")
    ]
    assert teardown, "no if: always() teardown step — loop device + image leak on shared runners"
    assert "/tmp/cds-smoke-tls" in teardown[0]["run"], "smoke TLS dir must be removed on teardown"


def test_smoke_records_node_ip_input() -> None:
    doc = _load(SMOKE)
    assert "node_ip" in (doc.get("on") or doc.get(True, {})).get("workflow_dispatch", {}).get(
        "inputs", {}
    )
    body = json.dumps(_smoke_steps())
    assert "GITHUB_STEP_SUMMARY" in body and "node_ip" in body, (
        "node_ip was a declared-but-dead input; it must be recorded verbatim"
    )


# ---------------------------------------------------------------------------
# cube-upstream-watch
# ---------------------------------------------------------------------------

WATCH = "cube-upstream-watch.yml"


def _watch_run(step_name_sub: str) -> str:
    steps = _load(WATCH)["jobs"]["contract-check"]["steps"]
    return next(s["run"] for s in steps if step_name_sub in str(s.get("name", "")))


def test_watch_clone_failure_is_fail_closed() -> None:
    run = _watch_run("Clone upstream")
    assert "clone-failed" not in run, (
        "a marker-file skip path lets clone failures report green — the weekly "
        "monitor would silently stop monitoring (fail-open)"
    )
    assert re.search(r"exit 1", run), "clone failure must fail the job"
    assert "GIT_ALLOW_PROTOCOL" in run, "clone must pin the transport protocol"
    assert "https://" in run and re.search(r'case\s+"?\$?REPO', run), (
        "CUBE_UPSTREAM_REPO must be validated (reject '-…' options and ext:: transports)"
    )
    assert " -- " in run, "git clone needs a `--` separator before the URL"


def test_watch_gate_step_passes_output_via_env_and_handles_exit3() -> None:
    doc = _load(WATCH)
    steps = doc["jobs"]["contract-check"]["steps"]
    gate = next(s for s in steps if "Gate" in str(s.get("name", "")))
    assert "${{" not in gate["run"], (
        "step outputs are untrusted context — interpolate via env:, not into the script body"
    )
    assert "CHECK_CODE" in (gate.get("env") or {}), "gate step must read the code from env"
    run = gate["run"]
    assert re.search(r"^\s*3\)", run, re.M), "exit 3 needs its own documented branch"
    assert re.search(r'"\s*"\s*\)|""\)', run) or re.search(r"\$\{CHECK_CODE:-\}", run), (
        "an empty output must be an explicit error branch, not the catch-all"
    )
    header = (WF_DIR / WATCH).read_text(encoding="utf-8").split("jobs:")[0]
    assert re.search(r"exit\s*3|= 3", header), "workflow header must document exit code 3"
