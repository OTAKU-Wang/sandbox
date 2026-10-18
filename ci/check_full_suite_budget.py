#!/usr/bin/env python3
"""W7 soft gate mechanism: full-suite failure budget vs ci/full-suite-baseline.json.

The rule "已知失败不得增长" lived in ci/known-failures.md as prose only: the
old step ended in `|| true`, the junit report was never uploaded and nothing
compared the counts. This script closes that machine-side gap:

  * baseline established   -> failures+errors above the budget = exit 1 (red);
  * baseline NOT established -> the GitHub lane has no measured number yet (the
    authoritative full-suite numbers stay on the remote py3.12+PG16 lane, rows
    of ci/known-failures.md). Counting and an explicit ::warning:: only — no
    fabricated baseline (honest-degradation rule), the mechanism goes live the
    moment ci/full-suite-baseline.json is established from a green GitHub run;
  * missing junit report   -> red once established (a suite that dies before
    writing report.xml must not report as "nothing to check").

Usage: check_full_suite_budget.py [report.xml] [baseline.json]
Stdlib only; safe to run from any CWD.
"""
from __future__ import annotations

import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _count(report: Path) -> dict[str, int]:
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    root = ET.parse(report).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    for ts in suites:
        for key in totals:
            totals[key] += int(ts.get(key, 0) or 0)
    return totals


def main(argv: list[str]) -> int:
    report = Path(argv[1]) if len(argv) > 1 else Path("report.xml")
    baseline_path = (
        Path(argv[2])
        if len(argv) > 2
        else Path(__file__).resolve().parent / "full-suite-baseline.json"
    )
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    established = bool(baseline.get("established"))
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")

    if not report.exists():
        msg = f"no junit report at {report} — full suite crashed or was skipped before writing it"
        if established:
            print(f"::error::{msg} — failure budget cannot be verified on an established baseline")
            return 1
        print(f"::warning::{msg} — baseline unestablished, counting only")
        return 0

    totals = _count(report)
    line = (
        f"full suite: {totals['tests']} tests, {totals['failures']} failures, "
        f"{totals['errors']} errors, {totals['skipped']} skipped"
    )
    print(line)
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write(f"- {line}\n")
        except OSError:
            pass

    if not established:
        print(
            "::warning::GitHub-lane failure baseline is NOT established "
            "(ci/full-suite-baseline.json established=false; authoritative numbers "
            "remain the remote py3.12+PG16 lane in ci/known-failures.md) — "
            "counting only, no budget enforced"
        )
        return 0

    budget = int(baseline.get("failures", 0)) + int(baseline.get("errors", 0))
    bad = totals["failures"] + totals["errors"]
    if bad > budget:
        print(
            f"::error::full-suite failure budget exceeded: {bad} > {budget} "
            "(ci/full-suite-baseline.json — the rule is must-not-grow: fix the "
            "regression, or re-baseline with written justification in the PR)"
        )
        return 1
    print(f"failure budget OK ({bad} <= {budget})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
