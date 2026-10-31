#!/usr/bin/env python3
"""接口可用性 dry run（2026-10-31，用户指令：「使用 dry run 进行测试，先检验接口的可用性」）。

口径（写死在脚本里，别在报告里另说一套）：
- **只读**：枚举 openapi 全量路由，真调所有 GET；全部写方法（POST/PUT/PATCH/DELETE）
  一律不碰、只统计数量——巡检 trigger、janitor、会话创建等有副作用的接口绝不借 dry run 之名触发。
- 唯一放行的写调用是鉴权本身：DB 直造 admin（与 tests/conftest.py 同款哈希+签发），
  真调一次 `POST /auth/login` 和一次 `POST /auth/refresh` 证明登录链可用。
- 环境口径 = 测试/dev 形状：TESTING=1 + conftest 同款 setdefault 开关；SQLite 内存库
  StaticPool + create_all；**带完整 lifespan**（真实 boot：披露行、后台件都起），
  与 uvicorn 真实部署同形——这是和 conftest ASGITransport 的关键差别，lifespan 不起
  会有假红。
- 响应分类：OK(2xx) / AUTH(401,403) / EMPTY(404，路由活着资源未播种) / VALID(422) /
  ENV-DEGRADED（仅「连接被拒/无法解析/流式不收敛」这一类外部后端缺失，逐个点名）/
  **RED（5xx、或信封缺失、或其余异常）**——RED 才是「接口不可用」，其余都是「可用」。
- 流式/长连接 GET（stream/events/ws）不真调（读了就不返回），标 STREAM-SKIP。
- 顺带锁 W2 契约：所有 4xx/5xx 的 JSON 体必须带 {code, message}。

产物：stdout 摘要 + .gate-scratch/dryrun_api.json（全量逐条）。
退出码：有 RED 即 1（可直接接 CI 的信息位；提硬门前先看一轮存量）。
"""
import asyncio
import json
import os
import sys
import time
import uuid
from collections import Counter
from pathlib import Path

# 以脚本目录运行时 sys.path 里只有 scripts/——显式把仓根钉进来（app 包在仓根）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---- conftest 同款环境钉（import app 之前） ----
os.environ["TESTING"] = "1"
for k, v in {
    "CDS_KMS_REQUIRE_ATTESTATION": "false",
    "CDS_DEV_SANDBOX_REQUIRE_CONTRACT": "false",
    "CDS_K8S_USE_PYTHON_CLIENT": "false",
    "CDS_TRAINING_REQUIRE_TORCH": "false",
    "CDS_LEGACY_L2_ENABLED": "true",
    "CDS_CUBE_DATAPLANE_ENABLED": "true",
    "CDS_SANDBOX_LEGACY_ENABLED": "true",
    "CDS_ALLOW_ADMIN_SELF_REGISTER": "true",
    "CDS_CUBE_NODE_TLS_CA": "/etc/cds/cube-node/test-node-ca.pem",
    "CDS_CUBE_TEMPLATE_SIGNING_KEY": "test-signing-key",
    "CDS_CUBE_RESULTS_DIR": "/tmp/cds-test-sandbox-results",
    "CDS_TRAINING_ALLOW_SIMULATION": "true",
    # dry run 走真实 lifespan boot，比 conftest 多一层启动校验——JWT 密钥与 dev 口径
    # （docker-compose / local_deploy 的硬检）同款必须给，值是本次运行内自造的临时密钥。
    "CDS_JWT_SECRET_KEY": "dryrun-" + uuid.uuid4().hex + uuid.uuid4().hex,
    # 主引擎口径：lifespan/启动件按 settings.database_url 自建行（monkeypatch 引擎
    # 接不住 import 期已捕获的引用），所以数据库必须在 import app 之前就用 env 钉成
    # SQLite 文件库（文件而非 :memory:：默认连接池多连接，内存库每连接各一份）。
    "CDS_DATABASE_URL": "sqlite+aiosqlite:///./dryrun_api.db",
}.items():
    os.environ.setdefault(k, v)

import httpx  # noqa: E402
import uvicorn  # noqa: E402

from app.core.database import Base  # noqa: E402
import app.core.database as db_module  # noqa: E402
from app.main import app  # noqa: E402

PORT = 8731
BASE = f"http://127.0.0.1:{PORT}"
REQ_TIMEOUT = 8.0
STREAM_HINTS = ("stream", "/events", "/ws", "/sse", "/download")

# env 已把主引擎钉成 SQLite 文件库；每轮全新（防上一轮播种干扰读数）
_dbfile = Path("dryrun_api.db")
if _dbfile.exists():
    _dbfile.unlink()
engine = db_module.engine
Session = db_module.async_session


def _sample_param(name: str) -> str:
    """路径参数播种：uuid 型 id 给随机 uuid（404=路由活着）；其余给中性串（422/404 均可用）。"""
    return str(uuid.uuid4())


async def main() -> int:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 不再 override get_db：env 钉定的主引擎即 SQLite，lifespan 与 API 走同一真源

    # 播种 admin（与 conftest admin_headers 同款），并留密码走真 login
    from app.services.auth_service import hash_password, create_access_token
    from app.models.user import User

    uid = uuid.uuid4()
    async with Session() as s:
        admin = User(
            username=f"dryrun_admin_{uid.hex[:6]}",
            email=f"dryrun_{uid.hex[:6]}@example.com",
            hashed_password=hash_password("dryrun-pass-123"),
            role="admin",
        )
        s.add(admin)
        await s.commit()  # 必须 commit：API 用新 session 读；只 flush 会在 context 退出时回滚 ⇒ 登录必 401
        await s.refresh(admin)
        admin_id, admin_role = str(admin.id), admin.role
    direct_token = create_access_token(uuid.UUID(admin_id), admin_role)

    cfg = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning", lifespan="on")
    server = uvicorn.Server(cfg)
    serve_task = asyncio.create_task(server.serve())
    try:
        # 等真实 boot 完成（lifespan 全量执行）
        for _ in range(100):
            try:
                async with httpx.AsyncClient(timeout=2) as c:
                    if (await c.get(f"{BASE}/health")).status_code == 200:
                        break
            except Exception:
                await asyncio.sleep(0.3)
        else:
            print("BOOT-FAIL: /health 20s 不起，后续读数无效")
            return 2

        results: list[dict] = []
        auth_ok = {"login": None, "refresh": None}
        async with httpx.AsyncClient(timeout=REQ_TIMEOUT) as c:
            # —— 唯一放行的两次写：鉴权链本身 ——
            r = await c.post(f"{BASE}/api/v1/auth/login", json={
                "username": admin.username, "password": "dryrun-pass-123"})
            auth_ok["login"] = r.status_code
            body = r.json() if r.status_code == 200 else {}
            token = body.get("access_token") or direct_token
            # refresh 探针用登录签发的真 refresh_token（access token 走 refresh 接口本就 401，混了会误判接口坏）
            r2 = await c.post(f"{BASE}/api/v1/auth/refresh",
                              json={"refresh_token": body.get("refresh_token") or ""})
            auth_ok["refresh"] = r2.status_code
            headers = {"Authorization": f"Bearer {token}"}

            spec = (await c.get(f"{BASE}/openapi.json")).json()
            get_paths = sorted(p for p, ops in spec["paths"].items() if "get" in ops)
            write_routes = sum(
                len([m for m in ops if m in ("post", "put", "patch", "delete")])
                for ops in spec["paths"].values()
            )

            for p in get_paths:
                if any(h in p for h in STREAM_HINTS):
                    results.append({"path": p, "class": "STREAM-SKIP", "status": None})
                    continue
                url = p
                # 路径参数播种：uuid 型 id 给随机 uuid（未播种资源→404=路由活着），
                # 非 uuid 参数由服务端 422 如实兜住（VALID=接口在且校验在）。
                import re
                url = re.sub(r"\{[^/]+\}", lambda m: str(uuid.uuid4()), url)
                t0 = time.monotonic()
                try:
                    r = await c.get(BASE + url, headers=headers)
                    dt = round(time.monotonic() - t0, 2)
                    code = r.status_code
                    cls = None
                    envelope_ok = True
                    if 200 <= code < 300:
                        cls = "OK"
                    elif code in (401, 403):
                        cls = "AUTH"
                    elif code == 404:
                        cls = "EMPTY"
                    elif code == 422:
                        cls = "VALID"
                    elif code >= 500:
                        cls = "RED"
                    else:
                        cls = "OK"  # 3xx 等中性
                    if code >= 400:
                        try:
                            body = r.json()
                            envelope_ok = isinstance(body, dict) and "code" in body and "message" in body
                        except Exception:
                            envelope_ok = False
                        if not envelope_ok and cls != "RED":
                            cls = "RED"
                    results.append({"path": p, "class": cls, "status": code,
                                    "secs": dt, "envelope_ok": envelope_ok})
                except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as e:
                    results.append({"path": p, "class": "ENV-DEGRADED", "err": f"{type(e).__name__}"})
                except asyncio.TimeoutError:
                    results.append({"path": p, "class": "ENV-DEGRADED", "err": "timeout>8s（流式或外呼不收敛）"})
                except httpx.TimeoutException:
                    results.append({"path": p, "class": "ENV-DEGRADED", "err": "timeout>8s"})
                except Exception as e:
                    results.append({"path": p, "class": "RED", "err": f"{type(e).__name__}: {e}"})

            # /health /metrics 若不在 openapi（root 挂载），补测
            for extra in ("/health", "/metrics"):
                if not any(x["path"] == extra for x in results):
                    try:
                        r = await c.get(BASE + extra)
                        results.append({"path": extra, "class": "OK" if r.status_code == 200 else "RED",
                                        "status": r.status_code})
                    except Exception as e:
                        results.append({"path": extra, "class": "RED", "err": str(e)})
    finally:
        server.should_exit = True
        await serve_task

    tally = Counter(x["class"] for x in results)
    report = {
        "generated_by": "scripts/api_dryrun_smoke.py",
        "note": "只读 dry run；写接口未触发",
        "auth_chain": auth_ok,
        "get_routes": len(results),
        "write_routes_not_touched": write_routes,
        "tally": dict(tally),
        "reds": [x for x in results if x["class"] == "RED"],
        "env_degraded": [x for x in results if x["class"] == "ENV-DEGRADED"],
        "results": results,
    }
    out = Path(".gate-scratch/dryrun_api.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1))

    print(f"GET 接口 {len(results)} 个（写接口 {write_routes} 个未触碰）；登录链 login={auth_ok['login']} refresh={auth_ok['refresh']}")
    for k, v in sorted(tally.items()):
        print(f"  {k:14s} {v}")
    for x in report["reds"][:40]:
        print(f"  RED {x['path']} -> {x.get('status', x.get('err'))}")
    print(f"明细：{out}")
    return 1 if tally.get("RED") else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
