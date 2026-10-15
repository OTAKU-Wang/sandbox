#!/usr/bin/env python3
"""SS-13 RT-03：Cube 节点 TEE 硬件探测（节点侧独立件，零 app 依赖，仅标准库）。

spec ss-13 §2.2（TEE 作为正交属性）+ §四 RT-03：
  - 复用 app/services/tee_capability.py 的 tdx / sev_snp 探测矩阵
    （设备 OR 命令在场 = 该 provider 有硬件信号；先 tdx 后 sev_snp），
    在节点 bring-up 侧自持一份（节点脚本不依赖 CDS 后端可导入性）；
  - sgx 对 MicroVM 路径不适用（SGX 是进程级 enclave）——如实不纳入探测；
  - 无硬件 = "none"（诚实降级铁律/禁伪造：无 TEE 硬件的节点如实上报 none，
    绝不猜测或伪造 provider）。

用法：
  tee_probe.py [--json] [--capabilities-file PATH]

  --json               stdout 输出完整 JSON（probe 结果）
  --capabilities-file  将 {"tee": ...} 合并写入节点 capabilities JSON
                       （SS-10 既暂存位：节点 capabilities 随 bring-up 上报；
                       既有键保留，仅 upsert "tee"；文件缺失则新建）
  默认 stdout：单行 tee=<tdx|sev_snp|none>（供 preflight/verify/runbook 抓取）

退出码：0 = 探测完成（无论结果值）；1 = 参数错误。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# 矩阵与 app/services/tee_capability.py:33-38 保持一致（镜像而非导入：
# 节点侧不假设 CDS 后端在 PATH 上可导入）。
PROVIDERS: dict[str, dict[str, tuple[str, ...]]] = {
    "tdx": {
        "devices": ("/dev/tdx_guest", "/dev/tdx-guest"),
        "commands": ("tdx-attest", "tdx_guest_test", "trustauthority-cli"),
    },
    "sev_snp": {
        "devices": ("/dev/sev-guest", "/dev/sev"),
        "commands": ("snpguest",),
    },
}


def probe(
    which=shutil.which,
    exists=os.path.exists,
    providers: dict[str, dict[str, tuple[str, ...]]] | None = None,
    device_root: str | Path = "/",
) -> dict:
    """探测本机 TEE 硬件信号。

    返回 {"tee": "tdx"|"sev_snp"|"none", "providers": {...证据...}}。
    which/exists 可注入（单测 mock 设备/命令双条件）。
    device_root 供测试钩子重定向设备路径（preflight 的 CDS_CUBE_MOCK_ROOT）；
    非 "/" 时仅探测 <device_root><设备名>，不兼探真实 /dev（避免 mock 模式串扰真实硬件信号）。
    """
    spec = providers or PROVIDERS
    order = ("tdx", "sev_snp")  # spec ss-13 §2.2：先 tdx 后 sev_snp
    evidence_map: dict[str, list[str]] = {}
    result = "none"
    for name in order:
        p = spec.get(name)
        if not p:
            continue
        evidence: list[str] = []
        for dev in p.get("devices", ()):
            # 注意：Path.joinpath 遇绝对路径会丢弃根，须剥前导 / 后拼接
            cands = [Path(device_root) / dev.lstrip("/") if device_root != "/" else Path(dev)]
            for c in cands:
                if exists(str(c)):
                    evidence.append(f"device:{dev}")
                    break
        for cmd in p.get("commands", ()):
            found = which(cmd)
            if found:
                evidence.append(f"command:{found}")
        evidence_map[name] = evidence
        if evidence:
            result = name
            break  # tdx 优先；命中即止
    return {"tee": result, "providers": evidence_map}


def write_capabilities(path: str | Path, result: dict) -> Path:
    """把 tee 结果 upsert 进节点 capabilities JSON（既有键保留）。"""
    p = Path(path)
    doc: dict = {}
    if p.exists():
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                doc = loaded
        except (ValueError, OSError):
            doc = {"_capabilities_file_recovered_from_corrupt": True}
    doc["tee"] = result["tee"]
    if result["tee"] != "none":
        doc["tee_evidence"] = [
            f"{name}:{ev}"
            for name, evs in result["providers"].items()
            for ev in evs
        ]
    doc["tee_probed_at"] = datetime.now(timezone.utc).isoformat()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="CDS cube-node TEE hardware probe (SS-13 RT-03)")
    ap.add_argument("--json", action="store_true", help="输出完整 probe JSON")
    ap.add_argument("--capabilities-file", default=os.environ.get("CDS_CUBE_CAPABILITIES_FILE", ""),
                    help="将 tee 结果 upsert 进该 capabilities JSON（默认取 CDS_CUBE_CAPABILITIES_FILE）")
    args = ap.parse_args(argv)

    result = probe(device_root=os.environ.get("CDS_CUBE_TEE_DEVICE_ROOT") or "/")
    if args.capabilities_file:
        try:
            write_capabilities(args.capabilities_file, result)
        except OSError as e:
            # 探测结果仍如实上报（stdout 单行不受影响）；写入失败如实告警不静默
            print(f"warn: capabilities 写入失败 {args.capabilities_file}: {e}", file=sys.stderr)
    if args.json:
        # --json 模式下机器可读输出走 stderr，保持 stdout 恒为单行 tee=...
        print(json.dumps({"capabilities_file": args.capabilities_file or None, **result},
                         ensure_ascii=False, indent=2), file=sys.stderr)
    print(f"tee={result['tee']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
