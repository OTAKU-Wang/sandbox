# cds-sdk — CDS 沙箱 Python SDK

Round 40 可用性交付：把 CDS 沙箱 REST 面（`/api/v1/sandbox-sessions`）包成一个
同步、类型化的 httpx 客户端。零额外依赖（仅 `httpx`，服务端 requirements 已有）。

## 安装

```bash
pip install ./sdk          # 仓库内安装
# 或
pip install -e ./sdk       # 开发模式
```

## 快速开始

```python
from cds_sdk import CDSClient, CDSError

with CDSClient("http://127.0.0.1:8000", token="<jwt-or-api-key>") as client:
    # 1. 会话：带模板创建（预置 analysis.py 等启动文件）
    session = client.create_session(
        data_product_id="<uuid>",
        contract_id="<contract-id>",          # 合约 buyer 才能建会话
        sandbox_level="L3",
        template="python-analysis",
    )
    client.wait_for_status(session["id"], target=("running",))

    # 2. 文件：上传输入（DEK 静态加密），沙箱内从 files/ 读取
    client.upload_file(session["id"], "data.csv", b"col\n1\n")
    print(client.list_files(session["id"]))

    # 3. 执行：批量代码（/execute，全量输出网关）或交互命令（/exec，短超时）
    result = client.execute(session["id"], "print(open('files/data.csv').read())")
    print(result["output"])                    # 已过 T5 DLP 审查
    probe = client.exec_command(session["id"], "ls -la files/")
    print(probe["exit_code"], probe["output"])

    # 4. 快照：改动前打快照，不满意一键回滚
    snap = client.create_snapshot(session["id"])
    client.rollback_snapshot(session["id"], snap["snapshot_id"])

    # 5. 生命周期：暂停/续期/恢复
    client.pause(session["id"])
    client.resume(session["id"])
    client.refresh(session["id"])              # 续一个 timeout 窗口

    # 6. 观测：日志轮询（follow 风格）与用量
    page = client.logs(session["id"], limit=50)
    newer = client.logs(session["id"], since=page["latest_created_at"])
    print(client.usage(session["id"]))
```

## 能力对照

| SDK 方法 | REST 端点 | 说明 |
|---|---|---|
| `create_session(template=...)` | `POST /sandbox-sessions` | 模板预置文件 + 注入 env |
| `list_templates` | `GET /sandbox-sessions/session-templates` | 内置 `empty`/`python-analysis`/`duckdb-query`，可用 `CDS_SESSION_TEMPLATES_JSON` 扩展 |
| `execute` | `POST /{id}/execute` | python/sql 批量执行 + 代码扫描 + 输出网关 |
| `exec_command` | `POST /{id}/exec` | shell 命令，硬超时（默认 120s），输出仍过 T5 审查 |
| `upload_file` / `download_file` | `POST|GET /{id}/files[/{name}]` | 下载非 critical 命中自动脱敏（`X-CDS-Output-Review: redacted`），critical 409 永不释放 |
| `create_snapshot` / `rollback_snapshot` | `POST /{id}/snapshots...` | sha256 完整性校验，超出保留数自动驱逐 |
| `pause` / `resume` / `refresh` | `POST /{id}/pause|resume|refreshes` | 暂停保留 workspace/文件/快照/密钥 |
| `logs` / `usage` | `GET /{id}/logs|usage` | 审计轨迹 tail + 工作区用量 |

错误全部抛 `CDSError(status_code, detail)`；终端态会话上的操作返回 4xx。
