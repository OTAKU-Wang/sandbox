# cds-sdk — CDS 沙箱 Python SDK

同步、类型化的 httpx 客户端，覆盖 CDS 主链路：认证 → 合约 → 会话/任务 → 推理 →
证明包。零额外依赖（仅 `httpx`，服务端 requirements 已有）。
Round 40 交付会话面；**Round 45+（N9）扩到 auth / contracts / tasks / proof-bundle / inference**。

## 安装

```bash
pip install ./sdk          # 仓库内安装
# 或
pip install -e ./sdk       # 开发模式
```

## 快速开始（登录直连）

```python
from cds_sdk import CDSClient

client = CDSClient.login("http://127.0.0.1:8000", "alice", "secret")
# 或 client = CDSClient.register("http://...", "alice", "a@x.com", "secret", role="buyer")

# 会话 + 文件 + 执行 + 快照 + 生命周期 + 观测
session = client.create_session(
    data_product_id="<uuid>",
    contract_id="<contract-id>",
    sandbox_level="L3",
    template="python-analysis",
)
client.wait_for_status(session["id"], target=("running",))
client.upload_file(session["id"], "data.csv", b"col\n1\n")
result = client.execute(session["id"], "print(open('files/data.csv').read())")
print(result["output"])                    # 已过 T5 DLP 审查
client.create_snapshot(session["id"])
client.pause(session["id"]); client.resume(session["id"])
client.refresh(session["id"])              # 续一个 timeout 窗口
client.logs(session["id"], limit=50); client.usage(session["id"])
client.close()
```

## 示例一 · 认证与合约（provider/buyer 签署）

```python
from datetime import datetime, timezone
client = CDSClient.login("http://127.0.0.1:8000", "provider_a", "secret")

contracts = client.list_contracts(status="draft")
cid = contracts["items"][0]["id"]
cno = contracts["items"][0]["contract_no"]

# 构建规范签名原文（与后端 crypto_service.contract_sign_data 一致），
# 用你的 SM2 私钥签名后提交：
ts = datetime.now(timezone.utc).isoformat()
payload = client.sign_data(cid, cno, "data_provider", ts, purpose="statistical_analysis")
signature = sign_with_my_sm2_key(payload)          # hex r||s
client.sign_contract(cid, signature, ts)
client.activate_contract(cid)
```

## 示例二 · 买方全流程：任务 → 脱敏结果 → 证明包

```python
buyer = CDSClient.login("http://127.0.0.1:8000", "buyer_b", "secret")

session = buyer.create_session(
    data_product_id="<uuid>", contract_id="<contract-id>", sandbox_level="L3",
)
buyer.wait_for_status(session["id"], target=("running",))

task = buyer.create_task(session["id"], "query", code="print(1+1)", purpose="statistical_analysis")
buyer.submit_task(session["id"], task["task_id"])
res = buyer.get_task_result(session["id"], task["task_id"])   # 脱敏输出 + 判定结论门禁
print(res["output"], res["output_review"])

bundle = buyer.get_proof_bundle(session["id"])                # 监管证明包（不泄密钥/原文）
print(bundle["bundle_hash"])
```

## 示例三 · RAG 检索问答（语料不出域）

```python
rag_client = CDSClient.login("http://127.0.0.1:8000", "buyer_b", "secret")
task = rag_client.create_task(
    session["id"], "rag_query", rag_query="最近上架了什么数据产品",
)
rag_client.submit_task(session["id"], task["task_id"])
print(rag_client.get_task_result(session["id"], task["task_id"]))
```

## 示例四 · 推理服务沙箱（N5）

```python
client = CDSClient.login("http://127.0.0.1:8000", "buyer_b", "secret")

models = client.list_models()                 # 买方可合约内可见
model_id = models[0]["model_id"]

invoke = client.invoke_model(
    model_id, {"x": [[1.0, 2.0]]}, purpose="statistical_analysis",
)
# invoke → {task_id, session_id, status};轮询任务结果取输出：
res = client.get_task_result(invoke["session_id"], invoke["task_id"])
print(res["output"])                          # 已过输出网关（脱敏/行数/水印）
print(client.get_inference_metering())        # token/rows 计量
```

## 能力对照

| SDK 方法 | REST 端点 | 说明 |
|---|---|---|
| `CDSClient.login/register` | `POST /auth/login\|register` | 返回带 token 的客户端，持有 refresh_token |
| `refresh_token` | `POST /auth/refresh` | 交换新 access+refresh（N9） |
| `list_contracts` / `get_contract` | `GET /contracts[/{id}]` | 合约列表/详情 |
| `sign_data` / `sign_contract` | `POST /contracts/{id}/sign` | 规范签名原文构建 + SM2 签名提交（N9） |
| `activate_contract` / `terminate_contract` | `POST /contracts/{id}/activate\|terminate` | 激活（自动履约）/终止（级联回收会话） |
| `create_session(template=...)` | `POST /sandbox-sessions` | 模板预置文件 + 注入 env |
| `list_templates` | `GET /sandbox-sessions/session-templates` | 内置 `empty`/`python-analysis`/`duckdb-query` |
| `execute` | `POST /{id}/execute` | python/sql 批量执行 + 代码扫描 + 输出网关 |
| `exec_command` | `POST /{id}/exec` | shell 命令，硬超时，输出仍过 T5 审查 |
| `upload_file` / `download_file` | `POST\|GET /{id}/files[/{name}]` | critical 命中 409 永不释放，非 critical 自动脱敏 |
| `create_snapshot` / `rollback_snapshot` | `POST /{id}/snapshots...` | sha256 校验 + 保留驱逐 |
| `pause` / `resume` / `refresh` | `POST /{id}/pause\|resume\|refreshes` | 暂停保留 workspace/文件/快照/密钥 |
| `logs` / `usage` | `GET /{id}/logs\|usage` | 审计轨迹 tail + 工作区用量 |
| `create_task` / `submit_task` / `get_task` / `get_task_result` | `POST\|GET /sandbox-tasks...` | 任务创建（含 `rag_query`）/提交/查询/脱敏结果（N9） |
| `get_proof_bundle` | `GET /sandbox-sessions/{id}/proof-bundle` | 监管验收证明包（N9） |
| `list_models` / `invoke_model` | `GET /inference/models` · `POST /inference/{id}/invoke` | 推理模型注册查询 + 沙箱内调用（N5/N9） |
| `get_inference_metering` | `GET /inference/metering` | token/rows 推理计量（N5/N9） |

## 重试与错误

- `429` / `410` / `503` 自动重试（最多 `max_retries` 次，尊重 `Retry-After`）。
- 非 2xx 一律抛 `CDSError(status_code, detail)`；W2 错误契约响应抛 `CDSApiError`，
  含 `code` / `message` / `request_id` / `retry_after`。
