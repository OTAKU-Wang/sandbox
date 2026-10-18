"""运维面静态门（2026-10 w5 运维/部署评审轮）。

覆盖 scripts/*.sh + scripts/remote-harness/*.sh + scripts/clickhouse-init.sql：
- `bash -n` 语法门（全量）；
- 关键不变量：fail-fast 在位（`set -e`，或带失败累计器的显式 `set -u`+累计+非零出口）、
  关键变量 `${VAR:?}` 必填、venv PATH 导出在位、致命路径不得 `exit 0`/静默吞错、
  哨兵行必须自带真实退出码、锁与原子写、供应链校验（URL 白名单/架构映射/sha 钩子）。
纯静态：只做语法检查与文本不变量断言，不执行安装/部署动作。
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
HARNESS = SCRIPTS / "remote-harness"
BASH = shutil.which("bash")

ALL_SH = sorted(SCRIPTS.glob("*.sh")) + sorted(HARNESS.glob("*.sh"))
CH_SQL = SCRIPTS / "clickhouse-init.sql"

# 车道/同步脚本刻意不用 `set -e`（逐步累计 + 哨兵协议），但必须具备失败累计与非零出口。
ACCUMULATORS = {"source_lane.sh", "fe_lane.sh", "sync_tree.sh"}

requires_bash = pytest.mark.skipif(BASH is None, reason="环境无 bash，静态语法门不适用")


def _read(name: str) -> str:
    for p in ALL_SH:
        if p.name == name:
            return p.read_text(encoding="utf-8")
    raise KeyError(name)


def _sql() -> str:
    return CH_SQL.read_text(encoding="utf-8")


@requires_bash
@pytest.mark.parametrize("script", ALL_SH, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_bash_n_all_scripts(script: Path) -> None:
    """全量 shell 语法门：bash -n 必须 RC=0。"""
    proc = subprocess.run(
        [BASH, "-n", script.as_posix()], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, f"bash -n failed for {script}:\n{proc.stderr}"


def test_all_shell_scripts_present() -> None:
    names = {p.name for p in ALL_SH}
    for expected in (
        "install-k3s.sh", "e2e-k8s.sh", "vault-init.sh", "fisco-init.sh",
        "prepare_tongsuo.sh", "gen-cube-stubs.sh", "cube-rootfs-convert.sh",
        "cube-upstream-check.sh", "build-cube-sandbox-image.sh", "k3d-kubectl.sh",
        "source_lane.sh", "fe_lane.sh", "sync_tree.sh",
    ):
        assert expected in names, f"运维脚本清单缺少 {expected}（评审轮文件集不完整？）"


@pytest.mark.parametrize("script", ALL_SH, ids=lambda p: p.name)
def test_fail_fast_or_accumulator_in_place(script: Path) -> None:
    text = script.read_text(encoding="utf-8")
    if script.name in ACCUMULATORS:
        assert "set -uo pipefail" in text, f"{script.name}: 累计器车道必须 set -u"
        assert "exit 1" in text or re.search(r'exit "\$rc"', text), (
            f"{script.name}: 累计器必须提供非零出口"
        )
        assert "FAILS" in text or "_RC" in text or "rc=" in text, (
            f"{script.name}: 缺少失败累计"
        )
    else:
        assert re.search(r"^set -e", text, re.M), f"{script.name}: 缺少 set -e fail-fast"


# ---------------------------------------------------------------- source_lane.sh

def test_source_lane_required_var_and_venv_path() -> None:
    src = _read("source_lane.sh")
    assert "${CDS_LANE_DB_URL:?" in src, "CDS_LANE_DB_URL 必须 ${VAR:?} 必填"
    assert re.search(r'export PATH="/root/cds-test/venv/bin:[^"]*"', src), (
        "venv PATH 导出不能删（docs/agent-handoff：删了整个 lane 会退化到系统 python）"
    )


def test_source_lane_fatal_paths_never_exit_zero() -> None:
    src = _read("source_lane.sh")
    assert 'STEP_FAIL no_tree"; exit 2' in src, "无源码树必须非零退出"
    assert 'STEP_FAIL runtime' in src and re.search(
        r'STEP_FAIL runtime[^\n]*\n.*exit 2|runtime \$v"; exit 2', src
    ), "python 版本不匹配必须非零退出，禁止 exit 0 假绿"
    assert not re.search(r"STEP_FAIL[^\n]{0,80}exit 0", src), "致命路径不得 exit 0"


def test_source_lane_accumulates_and_reports() -> None:
    src = _read("source_lane.sh")
    assert "FAILS=$((FAILS+1))" in src, "run() 必须累计失败数"
    assert "LANE_FAIL" in src, "必须有 LANE_FAIL 终态哨兵"
    assert re.search(r'echo "STEP_OK \$\{?name\}?\$?', src) or "STEP_OK ${name}" in src
    # 本轮日志先清理，SUMMARY 只报本轮跑过的 step
    assert re.search(r"rm -f[^\n]*gate\.log", src), "跨轮残留日志必须清理"
    assert "flock -n" in src, "同一 LOGDIR/DB 的并发 lane 必须加锁"


def test_source_lane_gate_parse_is_fail_closed() -> None:
    src = _read("source_lane.sh")
    assert "STEP_FAIL gate_parse" in src, "ci.yml gate 解析失败/空集必须报错，禁止空跑 pytest"
    assert "--" in src  # 解析层剔除 --ignore=... 之类选项 token
    assert re.search(r'0-9a-f', src), "tree manifest 必须做 32-hex 校验"
    # SS15 缺失视为失败，glob 用 nullglob+printf %q（禁止 ls 拼串注入面）
    assert "nullglob" in src and re.search(r"printf '%q", src)
    assert "STEP_FAIL ss15_missing" in src


def test_source_lane_tree_hash_hardened() -> None:
    src = _read("source_lane.sh")
    assert "-print0" in src and "xargs -0" in src, "tree 聚合必须 -print0/xargs -0"
    assert "xargs -0 -r" in src or re.search(r"-0\s+-r", src), "xargs 必须带 -r（空输入防挂起）"


# ---------------------------------------------------------------- fe_lane.sh

def test_fe_lane_exit_code_is_truthful() -> None:
    src = _read("fe_lane.sh")
    assert 'echo "FE_LANE_EXIT=$?"' not in src, "哨兵 echo 吞退出码的写法必须移除"
    assert 'FE_LANE_DONE rc=$FE_RC' in src, "哨兵必须携带聚合 rc"
    assert src.rstrip().endswith('exit "$rc"'), "外层必须透传真实退出码"
    assert "NPM_CI_RC=0" in src, "内层 set -u 下 RC 变量必须初始化"
    assert re.search(r"for v in \$NPM_CI_RC \$TSC_RC \$VITEST_RC \$BUILD_RC", src)


def test_fe_lane_cache_lock_and_local_binaries() -> None:
    src = _read("fe_lane.sh")
    assert "sha256sum" in src and "$SIG" in src, "node_modules 缓存键必须取自 lockfile 摘要"
    assert "flock -n" in src, "共享缓存并发运行必须加锁"
    assert "npx tsc" not in src and "npx vitest" not in src, "必须调用本地二进制，npx 会静默拉远端最新包"
    assert "node_modules/.bin/tsc" in src and "node_modules/.bin/vitest" in src
    assert "timeout -k 30" in src, "docker run 必须有外层超时"
    assert re.search(r'-f "\$FE/package\.json"', src), "远端目录/package.json 必须预检"


def test_fe_lane_log_summary_sanitized() -> None:
    src = _read("fe_lane.sh")
    assert "tr -cd" in src and "cut -c1-" in src, "日志摘要必须过滤控制字符并截断（哨兵伪造面收敛）"


# ---------------------------------------------------------------- sync_tree.sh

def test_sync_tree_local_root_and_agg_guarded() -> None:
    src = _read("sync_tree.sh")
    assert re.search(r'-z "\$L"', src) and re.search(r'-d "\$L"', src), "L 必须做非空+目录校验"
    assert "-print0" in src and "xargs -0 -r" in src, "聚合必须 -print0/xargs -0 -r"
    assert re.search(r"\|\s*wc -l", src), "空文件集必须显式拦截（双错即匹配的假 MATCH）"


def test_sync_tree_ssh_and_remote_value_hardened() -> None:
    src = _read("sync_tree.sh")
    assert "StrictHostKeyChecking=no" not in src, "禁止关闭主机密钥校验（用 accept-new 收敛）"
    assert "accept-new" in src
    assert "BatchMode=yes" in src and "ConnectTimeout" in src, "ssh 必须免交互+超时"
    assert "*[!0-9a-f]*" in src, "远端聚合值必须 32-hex 校验后才能使用/落盘"
    assert src.count("2>/dev/null") <= 2, "同步流诊断不得成堆进 /dev/null"


def test_sync_tree_manifest_write_and_signal_order() -> None:
    src = _read("sync_tree.sh")
    assert "SYNC_ALL_FAIL" in src and "mkdir -p" in src, "远端 $SRC 必须预建"
    m = src.index('"TREE_MD5_MATCH')
    v = src.index("SYNC_ALL_VERIFY_FAIL")
    assert m > v or src.count("TREE_MD5_MATCH") == 1 and m > src.index("for spec"), (
        "TREE_MD5_MATCH 不得先于内容抽查打印"
    )
    assert "flock -n" in src, "并发同步必须加锁"


# ---------------------------------------------------------------- install-k3s.sh

def test_install_k3s_supply_chain_and_root_gate() -> None:
    src = _read("install-k3s.sh")
    assert re.search(r'case "\$INSTALL_URL" in', src), "安装 URL 必须白名单/https 校验"
    assert "https://*" in src or '"https://' in src
    assert "INSTALL_K3S_VERSION" in src, "必须透传 INSTALL_K3S_VERSION 版本固定"
    assert "EUID" in src, "k3s 模式必须前置 root 校验"
    assert re.search(r'uname -m', src) and "aarch64" in src, "k3d 二进制必须按架构映射"
    assert "k3d-linux-amd64\"" not in src.replace("${K3D_ARCH}", ""), "禁止硬编码 amd64"


def test_install_k3s_no_silent_failures() -> None:
    src = _read("install-k3s.sh")
    assert not re.search(r"systemctl restart k3s[^\n]*\|\| *true", src), "k3s 重启失败不可吞"
    assert not re.search(r"cluster (start|restart)[^\n]*\|\| *true", src), "k3d 启停失败不可吞"
    assert "cluster get" in src, "集群存在性用 cluster get 精确判断"
    assert 'if ! "$K3D_BIN" kubeconfig merge' in src, "kubeconfig merge 失败必须退出"
    assert "need_cmd kubectl" in src, "deploy 前必须校验 kubectl 可用"
    assert "head -1" not in src, "pipefail 下禁用 head -1（SIGPIPE）"
    assert "images ls" in src, "本地镜像导入后必须校验"
    assert "KUBECTL_CMD=(" in src and '"${KUBECTL_CMD[@]}"' in src, "多词命令必须用数组"
    assert 'KUBECTL="k3s kubectl"' not in src


# ---------------------------------------------------------------- gen-cube-stubs.sh

def test_gen_stubs_json_candidates_and_pyi_kept() -> None:
    src = _read("gen-cube-stubs.sh")
    assert '"$1" "${@:2}"' in src, "_json_field 必须把全部候选键传给 python"
    assert re.search(r'_pb2_grpc\.pyi"', src), "want 清单必须显式列出 _pb2_grpc.pyi"
    assert "_pb2.pyi" in src, "want 清单必须包含 .pyi（否则 prune 静默清空类型存根）"


def test_gen_stubs_pin_gate_and_atomicity() -> None:
    src = _read("gen-cube-stubs.sh")
    assert re.search(r'0-9a-fA-F', src), "PIN 必须做 hex 形状校验（参数注入面收敛）"
    assert "status --porcelain" in src and "|| true" not in src.split("status --porcelain")[1][:60], (
        "脏工作副本检查不得 fail-open"
    )
    assert 'mv -f "$TMP/_VERSION.json"' in src
    assert src.index('"$TMP/_VERSION.json" "$STUBS/_VERSION.json"') > src.index("import self-check ok"), (
        "_VERSION.json 必须 import 自检通过后原子落盘"
    )
    assert '"$PY" -B' in src or "PYTHONDONTWRITEBYTECODE" in src
    assert "__pycache__" in src, "stub_hash / __init__ 补全必须剪掉 __pycache__"
    assert "sed -i" not in src, "GNU sed -i 不跨平台，必须临时文件+mv"


# ---------------------------------------------------------------- cube-upstream-check.sh

def test_upstream_check_git_errors_are_fatal() -> None:
    src = _read("cube-upstream-check.sh")
    assert re.search(r'if ! CHANGED="\$\(', src), "git diff 失败必须 die，不得报 no drift"
    assert re.search(r'rev-parse HEAD\)" \|\| die', src), "HEAD 取不到必须 die"
    assert "BASH_VERSINFO" in src, "mapfile 需要 bash>=4，必须显式守卫"


def test_upstream_check_pin_resolution_visible_and_nested() -> None:
    src = _read("cube-upstream-check.sh")
    assert "pin source" in src, "pin 生效来源必须披露（manifest|_VERSION|env|fallback）"
    assert re.search(r'cubesandbox\.git_sha|\(?"cubesandbox",\s*"git_sha"', src), (
        "manifest 的 git_sha 嵌套在 cubesandbox 下，顶层查找是死代码"
    )
    assert "falling back" in src or "WARN" in src
    assert re.search(r'python3 not found', src), "manifest 在场而解释器缺失必须硬错误"


def test_upstream_check_diff_hygiene() -> None:
    src = _read("cube-upstream-check.sh")
    assert "--no-renames" in src, "重命名必须拆成删+增，否则契约删除被记为 OTHER"
    assert "--no-ext-diff" in src and "--no-textconv" in src, "外部仓库 diff 必须关闭 external diff/textconv"
    assert "tr -d '\\r'" in src or "tr -d \"\\\\r\"" in src, "清单行必须去 CRLF"
    assert 'core.quotepath=false' in src


# ---------------------------------------------------------------- cube-rootfs-convert.sh

def test_rootfs_convert_cleanup_and_mount_safety() -> None:
    src = _read("cube-rootfs-convert.sh")
    assert 'mount -o loop "$EXT4"' not in src, "禁止对 losetup 已关联的文件再 -o loop（双关联）"
    assert 'mount "$LOOP" "$MNT"' in src
    assert "umount -l" in src, "瞬时 busy 的 umount 必须有 lazy 兜底"
    assert "/proc/mounts" in src, "卸载失败时不得 rm -rf 活挂载点"
    assert re.search(r"for c in [^\n]*tar", src), "tar/du/stat 等依赖必须预检"


def test_rootfs_convert_publish_is_atomic_and_validated() -> None:
    src = _read("cube-rootfs-convert.sh")
    assert ".artifact.ext4." in src, "发布必须先写 OUT_DIR 内临时名再同 FS rename"
    assert "flock -n" in src, "同一 OUT_DIR 并发构建必须加锁"
    assert "df -Pk" in src, "工作盘空间必须预检（/tmp 常是 tmpfs）"
    assert "e2fsck.log" in src, "e2fsck 输出必须落盘可诊断"
    assert 'case "$IMAGE_REF" in' in src and "-*)" in src, "IMAGE_REF 必须以 -- 与前置校验隔离"
    assert "rootfs" in src and re.search(r"空|empty", src), "近空 rootfs 导出必须拒绝（no simulated artifact）"
    assert 'docker rm -f "$CTR"' in src, "export 成功后立即回收容器"


# ---------------------------------------------------------------- prepare_tongsuo.sh

def test_prepare_tongsuo_fail_closed_and_invariants() -> None:
    src = _read("prepare_tongsuo.sh")
    assert "set -euo pipefail" in src
    # 既有测试锁定的失败文案必须原样保留
    for marker in ("源包不存在", "sha256 不匹配", "没有钉住 sha", "READY"):
        assert marker in src, f"prepare_tongsuo 既有契约文案丢失: {marker}"
    line = next(ln for ln in src.splitlines() if ln.startswith("TONGSUO_TARBALL_SHA256="))
    value = line.split("=", 1)[1].strip()
    assert len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    assert "TONGSUO_TARBALL_SHA256:-" not in src
    assert "160000" in src and re.search(r"0-9a-f\{40\}", src), "gitlink mode/sha 必须校验"
    assert "ext::" in src, "必须拦截 ext:: 传输协议（已知 RCE 向量）"
    assert "***@" in src, "带凭据 URL 打印前必须脱敏"
    assert ".tongsuo-pin" in src, "tarball 落地必须留可复核的 pin 标记"
    assert "mktemp" in src, "并发临时目录必须唯一化"


# ---------------------------------------------------------------- vault-init.sh

def test_vault_init_reachability_and_parse_guards() -> None:
    src = _read("vault-init.sh")
    assert "sealedok" in src or ("-eq 2" in src), "sealed 时 vault status rc=2 必须视为可达"
    assert "cannot parse" in src, "已初始化标志解析失败必须报错，禁止默认未初始化"
    assert "${ROOT_TOKEN:0:8}" not in src, "bash 子串扩展在 /bin/sh(dash/ash) 下 Bad substitution"
    assert "cut -c1-8" in src


def test_vault_init_credentials_never_lost_silently() -> None:
    src = _read("vault-init.sh")
    assert '[[:space:]]*:[[:space:]]*' in src or "[[:space:]]*:[[:space:]]*" in src, (
        "kubectl -o json 是缩进美化格式，sed 模式必须容忍冒号两侧空白"
    )
    assert "VAULT_UNSEAL_KEY=" in src and "VAULT_ROOT_TOKEN=" in src, (
        "Secret 写不进去时必须显式打印密钥（单份额 unseal key，丢失=数据不可恢复）"
    )
    assert not re.search(r"operator init[^\n]*2>/dev/null", src), "init 失败原因不得丢进 /dev/null"
    assert '"$UNSEAL_KEY" | vault operator unseal' in src or 'UNSEAL_KEY" | vault operator unseal' in src, (
        "unseal key 走 stdin，不进 argv/ps"
    )
    assert "still sealed" in src, "unseal 后必须复核 sealed 状态"
    assert "path is already in use" in src, "幂等分支只能吞 already-exists 类错误"
    assert 'VAULT_TOKEN=""' not in src or "die" in src.split("VAULT_TOKEN=\"\"")[0].rsplit("if", 1)[-1]


# ---------------------------------------------------------------- e2e-k8s.sh

def test_e2e_k8s_uses_real_sdk_surface() -> None:
    src = _read("e2e-k8s.sh")
    assert "access_token=" not in src, "CDSClient 没有 access_token 参数（TypeError 假路径）"
    assert re.search(r"CDSClient\([^)]*token=", src, re.S), "必须按 SDK 真实签名传 token"
    assert "stream_exec" in src, "W10 流式链路必须用 stream_exec，REST exec_command 不算覆盖"
    assert "ontainersReady" in src, "就绪必须看 ContainersReady 条件，phase=Running 不等于 ready"
    assert "Admin@12345" not in src, "默认口令不得入仓"
    assert "CDS_E2E_PASS" in src and "required" in src.lower()
    assert "timeout_seconds=300" in src, "会话预算必须覆盖 pod 冷启动最坏路径"
    assert "REPO_ROOT" in src, "sys.path 不得依赖相对 CWD"


def test_e2e_k8s_teardown_is_verified_before_pass() -> None:
    src = _read("e2e-k8s.sh")
    assert "pod still present after teardown" in src or "teardown" in src
    assert src.index("PASS:") > src.index("finally"), "PASS 只能在 finally（teardown+pod 删除核验）之后打印"
    assert "describe" in src and "stdout" in src, "超时诊断必须打印 kubectl describe 输出"


# ---------------------------------------------------------------- fisco-init.sh

def test_fisco_init_security_and_idempotence() -> None:
    src = _read("fisco-init.sh")
    assert "umask 077" in src, "密钥落盘基目录必须 0700（/tmp 世界可写劫持面）"
    assert "FISCO_CONSOLE_SHA256" in src, "上游 tarball 必须提供校验和钩子"
    assert "--no-same-owner" in src, "解包必须 --no-same-owner"
    assert "--connect-timeout" in src and "--max-time" in src, "就绪探测 curl 必须有超时"
    assert '"result"' in src, "只凭 curl -f 会把 200 空响应当就绪"
    assert "audit-registry.address" in src, "部署地址必须落盘且重复运行幂等"
    assert "deploy.log" in src and "contract address" in src, "console 部署结果必须核验（console.sh 失败也返回 0）"
    assert 'if [ ! -f "$CONTRACT_DIR/AuditRegistry.sol" ]' in src or "-f \"$CONTRACT_DIR/AuditRegistry.sol\"" in src
    assert '-x "${CONSOLE_DIR}/console.sh"' in src, "幂等判断必须看关键文件可执行，不是目录存在"


# ---------------------------------------------------------------- build-cube-sandbox-image.sh

def test_build_cube_image_ca_guard_and_traceability() -> None:
    src = _read("build-cube-sandbox-image.sh")
    assert "PRIVATE KEY" in src, "CA 探针必须拒绝私钥形状（防烘焙进可分发镜像）"
    assert 'cds.cube-egress-ca.sha256' in src, "镜像必须带 CA sha label（CB-05 审计链路）"
    assert "--build-arg CUBE_EGRESS_CA=cube-egress-ca.crt" in src, "构建上下文必须显式传参"
    assert re.search(r'PYTHON[^"]*not executable|PYTHON=.*die', src), "显式 PYTHON 失效必须硬报错"
    assert re.search(r'-f "\$ROOT/Dockerfile\.cube-sandbox"', src) or "Dockerfile 缺失" in src
    assert "command -v docker" in src, "docker 必须前置预检"


# ---------------------------------------------------------------- k3d-kubectl.sh

def test_k3d_kubectl_wrappers_safe() -> None:
    src = _read("k3d-kubectl.sh")
    assert "set -euo pipefail" in src
    assert "docker exec -i" in src, "必须转发 stdin（kubectl apply -f - 依赖）"
    assert "K3D_CONTAINER" in src and "CLUSTER_NAME" in src, "容器名必须随集群名参数化"
    assert ".State.Running" in src or "docker inspect" in src, "容器不存在/未运行必须显式报错"


# ---------------------------------------------------------------- clickhouse-init.sql

def test_clickhouse_all_tables_have_retention() -> None:
    sql = _sql()
    for table in ("policy_decisions", "cdc_events", "data_product_versions", "certificate_events"):
        seg = sql.split(f"cds_audit.{table}")[1].split(";")[0]
        assert "TTL" in seg, f"{table} 缺保留策略（无限增长违反数据最小化）"
    assert "ttl_only_drop_parts" in sql, "月度分区表须用分区级过期，避免行级 TTL mutation 风暴"


def test_clickhouse_audit_indexes_and_drift_notice() -> None:
    sql = _sql()
    audit_seg = sql.split("cds_audit.audit_events")[1].split(";")[0]
    assert audit_seg.count("INDEX") >= 5, "高基数回溯列（session/resource/request/contract/sandbox）需要跳数索引"
    assert "Nullable(Boolean)" in sql, "chain_valid 默认 true 是 fail-open，必须可空表达未校验"
    assert "chain_valid Boolean DEFAULT true" not in sql
    assert "configmaps.yaml" in sql, "k8s 8 列副本漂移必须就地登记（修复归 k8s 工作包）"
