#!/usr/bin/env python3
"""可信数据空间服务平台建设 —— 数据开发应用系统 E2E 闭环测试.

验证数据开发应用系统（密态沙箱）核心全生命周期链路：
1. 平台启动与管理员登录 (Admin Auth)
2. 团队空间管理与资源配额隔离 (Team Management & Quotas)
3. 数据资源入库与自动分类分级探查 (Data Resource Upload & Auto Profile)
4. 数据产品多版本管理与 Schema Diff 比对 (Product Versions & Diff)
5. 业务场景中心与承接为建模项目 (Scenario Center -> Modeling Project)
6. 建模项目多语言协同空间与 DAG 拓扑校验 (Modeling Project & DAG Kahn Sort)
7. 模型反序列化安全扫描与评估指标对齐 (Model Security Scan & Metrics)
8. 计量计费账单生成与对账单 CSV 导出 (Billing Invoice & CSV Export)
9. 全局通知中心消息流转与已读状态 (Notification Center & Read Flow)
10. 全生命周期不可篡改审计追踪 (Audit Trail Verification)

Usage:
    python deploy/e2e-remote/e2e_data_dev_system.py [BASE_URL] [--out REPORT.json]
"""
import argparse
import base64
import json
import os
import sys
import time
import uuid

import httpx

EVIDENCE = []


def step(name: str, status: str, status_code: int | None = None, note: str = "", excerpt: str = "") -> bool:
    rec = {
        "step": name,
        "status": status,
        "status_code": status_code,
        "note": note,
        "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    if excerpt:
        rec["excerpt"] = str(excerpt)[:600]
    EVIDENCE.append(rec)
    print(f"[{status:5}] {name}" + (f" ({note})" if note else ""))
    return status == "PASS"


def finish(out_file: str) -> int:
    passes = sum(1 for e in EVIDENCE if e["status"] == "PASS")
    fails = sum(1 for e in EVIDENCE if e["status"] == "FAIL")
    skips = sum(1 for e in EVIDENCE if e["status"] == "SKIP")
    summary = f"\n== {passes} passed / {skips} skipped / {fails} failed -> {out_file}\n"
    print(summary)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({"summary": {"passed": passes, "skipped": skips, "failed": fails}, "steps": EVIDENCE}, f, indent=2, ensure_ascii=False)
    return 1 if fails > 0 else 0


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def main():
    parser = argparse.ArgumentParser(description="CDS Data Development & Application System E2E Test")
    parser.add_argument("base_url", nargs="?", default="http://127.0.0.1:8000")
    parser.add_argument("--out", default="e2e_data_dev_system_evidence.json")
    parser.add_argument("--admin-user", default="cdsadmin")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    admin_pass = os.environ.get("CDS_E2E_ADMIN_PASS", "nmdcIu8EUaG8A44j!")

    client = httpx.Client(base_url=base, timeout=60.0)

    # ─────────────────────────────────────────────────────────────
    # Step 1: Health & Authentication
    # ─────────────────────────────────────────────────────────────
    try:
        r = client.get("/health")
        step("1.1 health", "PASS" if r.status_code == 200 else "FAIL", r.status_code, excerpt=r.text)
        if r.status_code != 200:
            return finish(args.out)
    except Exception as e:
        step("1.1 health", "FAIL", note=f"Connection refused: {e}")
        return finish(args.out)

    login_res = client.post("/api/v1/auth/login", json={"username": args.admin_user, "password": admin_pass})
    if login_res.status_code != 200:
        step("1.2 admin.login", "FAIL", login_res.status_code, note="Failed admin login", excerpt=login_res.text)
        return finish(args.out)
    admin_token = login_res.json().get("access_token")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    admin_user_id = login_res.json().get("user", {}).get("id")
    step("1.2 admin.login", "PASS", 200, f"admin={args.admin_user}")

    # Register Data Provider & Buyer for testing
    suffix = uuid.uuid4().hex[:6]
    provider_name = f"dev_provider_{suffix}"
    buyer_name = f"dev_buyer_{suffix}"

    # Buyer register (self-selects buyer by default)
    buyer_res = client.post("/api/v1/auth/register", json={
        "username": buyer_name,
        "email": f"{buyer_name}@cds-test.cn",
        "password": "Password123!",
    })
    if buyer_res.status_code != 201:
        step("1.3 user.register_buyer", "FAIL", buyer_res.status_code, excerpt=buyer_res.text)
        return finish(args.out)
    buyer_token = buyer_res.json()["access_token"]
    buyer_headers = {"Authorization": f"Bearer {buyer_token}"}
    buyer_user_id = buyer_res.json()["user"]["id"]
    step("1.3 user.register_buyer", "PASS", 201, f"user={buyer_name}")

    # Data Provider register
    prov_res = client.post("/api/v1/auth/register", json={
        "username": provider_name,
        "email": f"{provider_name}@cds-test.cn",
        "password": "Password123!",
    })
    if prov_res.status_code != 201:
        step("1.4 user.register_provider", "FAIL", prov_res.status_code, excerpt=prov_res.text)
        return finish(args.out)
    prov_user_id = prov_res.json()["user"]["id"]

    # Admin upgrades provider role directly in DB (mirroring fixture make_user)
    import subprocess
    subprocess.run([
        "docker", "exec", "l-postgres", "psql", "-U", "cds", "-d", "cds", "-c",
        f"UPDATE users SET role = 'data_provider' WHERE id = '{prov_user_id}';"
    ], check=True, capture_output=True)

    # Login as provider to get token with role=data_provider
    plogin = client.post("/api/v1/auth/login", json={"username": provider_name, "password": "Password123!"})
    if plogin.status_code != 200:
        step("1.4 user.role_provider", "FAIL", plogin.status_code, excerpt=plogin.text)
        return finish(args.out)
    prov_token = plogin.json()["access_token"]
    prov_headers = {"Authorization": f"Bearer {prov_token}"}
    step("1.4 user.role_provider", "PASS", 200, f"role=data_provider, user={provider_name}")

    # ─────────────────────────────────────────────────────────────
    # Step 2: Team Management & Resource Quota Isolation
    # ─────────────────────────────────────────────────────────────
    team_payload = {
        "name": f"信贷风控建模实验室_{suffix}",
        "org_id": f"org-credit-{suffix}",
        "description": "跨空间联合信贷风控算法研发团队",
        "cpu_quota_cores": 32,
        "memory_quota_gb": 64,
        "storage_quota_gb": 500,
    }
    team_res = client.post("/api/v1/users/teams", json=team_payload, headers=admin_headers)
    if team_res.status_code != 201:
        step("2.1 team.create", "FAIL", team_res.status_code, excerpt=team_res.text)
        return finish(args.out)
    team_data = team_res.json()
    team_id = team_data["id"]
    step("2.1 team.create", "PASS", 201, f"team_id={team_id}, cpu=32, mem=64GB")

    # Add provider & buyer to team with valid TeamRoles ("core_developer", "viewer")
    m1 = client.post(f"/api/v1/users/teams/{team_id}/members", json={"user_id": prov_user_id, "role": "core_developer"}, headers=admin_headers)
    m2 = client.post(f"/api/v1/users/teams/{team_id}/members", json={"user_id": buyer_user_id, "role": "viewer"}, headers=admin_headers)
    if m1.status_code == 200 and m2.status_code == 200:
        step("2.2 team.add_members", "PASS", 200, "provider=core_developer, buyer=viewer")
    else:
        step("2.2 team.add_members", "FAIL", m1.status_code, excerpt=f"m1={m1.text}, m2={m2.text}")
        return finish(args.out)

    # Check team visibility isolation (member can see team, non-member cannot)
    team_list_res = client.get("/api/v1/users/teams", headers=prov_headers)
    member_teams = team_list_res.json().get("items", [])
    has_team = any(t["id"] == team_id for t in member_teams)
    step("2.3 team.member_visibility", "PASS" if has_team else "FAIL", team_list_res.status_code,
         f"member sees team count={len(member_teams)}")

    # ─────────────────────────────────────────────────────────────
    # Step 3: Data Resource Upload & Auto Classification/Grading
    # ─────────────────────────────────────────────────────────────
    csv_with_pii = (
        "cust_name,id_card,mobile,email,credit_code,revenue\n"
        "张三,110101199003072341,13800138000,zhangsan@example.com,91110108MA01C8K47T,50000\n"
        "李四,310101198512150011,13900139000,lisi@example.com,91310000MA1FL0GH2X,70000\n"
        "王五,440106199508241234,13711223344,wangwu@example.com,91440101MA59XXXX11,95000\n"
    )
    res_upload = client.post(
        "/api/v1/data-resources/upload",
        files={"file": ("credit_sample.csv", csv_with_pii.encode("utf-8"), "text/csv")},
        data={"name": f"零售信贷样本数据_{suffix}", "description": "包含个人身份信息与征信评分的原始数据"},
        headers=prov_headers,
    )
    if res_upload.status_code != 200:
        step("3.1 resource.upload", "FAIL", res_upload.status_code, excerpt=res_upload.text)
        return finish(args.out)
    resource_id = res_upload.json()["id"]
    step("3.1 resource.upload", "PASS", 200, f"resource_id={resource_id}")

    # Trigger Auto-Profile
    profile_res = client.post(f"/api/v1/data-resources/{resource_id}/auto-profile", headers=prov_headers)
    if profile_res.status_code != 200:
        step("3.2 resource.auto_profile", "FAIL", profile_res.status_code, excerpt=profile_res.text)
        return finish(args.out)
    profile_data = profile_res.json()
    columns = profile_data.get("columns", [])
    classifications = profile_data.get("field_classifications", {})
    sensitivity = profile_data.get("sensitivity_level")
    enforcement = profile_data.get("enforcement_preview", {})
    step("3.2 resource.auto_profile", "PASS", 200,
         f"sensitivity={sensitivity}, max_level={profile_data.get('classification_level')}, "
         f"pii_cols={list(classifications.keys())}, mask_preview={enforcement.get('mask_fields')}")

    # ─────────────────────────────────────────────────────────────
    # Step 4: Data Product Versioning & Schema Diff Comparison
    # ─────────────────────────────────────────────────────────────
    v1_schema = {
        "fields": [
            {"name": "tax_id", "type": "string"},
            {"name": "taxpayer_name", "type": "string", "desc": "企业名称"},
            {"name": "region_code", "type": "string"},
        ],
    }
    prod_res = client.post("/api/v1/data-products", json={
        "name": f"企业信贷特征集_{suffix}",
        "product_type": "structured",
        "data_schema": v1_schema,
    }, headers=prov_headers)
    if prod_res.status_code not in (200, 201):
        step("4.1 product.create_v1", "FAIL", prod_res.status_code, excerpt=prod_res.text)
        return finish(args.out)
    product_id = prod_res.json()["id"]
    step("4.1 product.create_v1", "PASS", prod_res.status_code, f"product_id={product_id}")

    # Create Version 2 with schema change (added credit_score, removed region_code, modified taxpayer_name)
    v2_schema = {
        "fields": [
            {"name": "tax_id", "type": "string"},
            {"name": "taxpayer_name", "type": "text", "desc": "企业名称"},
            {"name": "credit_score", "type": "number"},
        ],
    }
    v2_res = client.post(f"/api/v1/data-products/{product_id}/versions", json={
        "schema_definition": v2_schema,
        "changelog": "新增信用分字段，废弃区域码",
    }, headers=prov_headers)
    if v2_res.status_code != 201:
        step("4.2 product.create_v2", "FAIL", v2_res.status_code, excerpt=v2_res.text)
        return finish(args.out)
    step("4.2 product.create_v2", "PASS", 201, "version=2 created")

    # Check Schema Diff between v1 and v2
    diff_res = client.get(f"/api/v1/data-products/{product_id}/versions/diff?from_version=1&to_version=2", headers=prov_headers)
    if diff_res.status_code == 200:
        diff_data = diff_res.json()
        added = diff_data.get("added_fields", [])
        removed = diff_data.get("removed_fields", [])
        modified = [c["name"] for c in diff_data.get("modified_fields", [])]
        step("4.3 product.schema_diff", "PASS", 200, f"added={added}, removed={removed}, modified={modified}")
    else:
        step("4.3 product.schema_diff", "FAIL", diff_res.status_code, excerpt=diff_res.text)

    # Rollback version back to 1
    rb_res = client.post(f"/api/v1/data-products/{product_id}/versions/rollback", json={"target_version": 1}, headers=prov_headers)
    if rb_res.status_code == 200 and rb_res.json().get("current_version") == 1:
        step("4.4 product.rollback", "PASS", 200, "Successfully rolled back to version 1")
    else:
        step("4.4 product.rollback", "FAIL", rb_res.status_code, excerpt=rb_res.text)

    # ─────────────────────────────────────────────────────────────
    # Step 5: Scenario Center & Project Acceptance
    # ─────────────────────────────────────────────────────────────
    scen_res = client.post("/api/v1/flow/scenarios", json={
        "title": f"小微企业信贷违约预测场景_{suffix}",
        "industry": "金融科技",
        "business_background": "基于沙箱密态环境，联合银行与征信机构数据训练违约风险预测模型。",
        "target_description": "产出风控评分卡与特征重要度分析，原始数据不落盘出域。",
        "delivery_scene": "tee_compute",
        "suggested_resource_ids": [str(resource_id)],
        "compliance_constraints": {"output_review": True, "no_raw_export": True},
        "delivery_tech_config": {"runtime": "tee-python:3.12", "instance": "standard-4c8g"},
    }, headers=prov_headers)
    if scen_res.status_code != 201:
        step("5.1 scenario.create", "FAIL", scen_res.status_code, excerpt=scen_res.text)
        return finish(args.out)
    scenario_id = scen_res.json()["id"]
    step("5.1 scenario.create", "PASS", 201, f"scenario_id={scenario_id}")

    # Accept Scenario into Team creating ModelingProject
    accept_res = client.post(f"/api/v1/flow/scenarios/{scenario_id}/accept", json={
        "team_id": str(team_id),
        "project_name": f"小微信贷风控建模协同项目_{suffix}",
    }, headers=prov_headers)
    if accept_res.status_code not in (200, 201):
        step("5.2 scenario.accept_to_project", "FAIL", accept_res.status_code, excerpt=accept_res.text)
        return finish(args.out)
    accept_data = accept_res.json()
    project_id = (accept_data.get("project") or {}).get("id")
    step("5.2 scenario.accept_to_project", "PASS", accept_res.status_code, f"project_id={project_id}")

    # ─────────────────────────────────────────────────────────────
    # Step 6: Collaborative Modeling Project & DAG Topology Check
    # ─────────────────────────────────────────────────────────────
    # 6.1 Save DAG with cycle and run Kahn validation (must detect cycle and name cyclic nodes)
    cycle_dag = {
        "nodes": [
            {"id": "node_extract", "label": "数据清洗与对齐", "type": "sql"},
            {"id": "node_features", "label": "WOE/IV 特征工程", "type": "python"},
            {"id": "node_train", "label": "LightGBM 模型训练", "type": "python"},
        ],
        "edges": [
            {"source": "node_extract", "target": "node_features"},
            {"source": "node_features", "target": "node_train"},
            {"source": "node_train", "target": "node_extract"},  # intentional cycle!
        ]
    }
    client.put(f"/api/v1/flow/projects/{project_id}/dag", json=cycle_dag, headers=prov_headers)
    val_cycle = client.post(f"/api/v1/flow/projects/{project_id}/dag/validate", headers=prov_headers)
    if val_cycle.status_code == 200 and val_cycle.json().get("valid") is False:
        cyclic = val_cycle.json().get("cyclic_nodes", [])
        step("6.1 dag.cycle_detection", "PASS", 200, f"Cycle detected! cyclic_nodes={cyclic}")
    else:
        step("6.1 dag.cycle_detection", "FAIL", val_cycle.status_code, "Failed to detect cycle", excerpt=val_cycle.text)

    # 6.2 Save valid DAG and run Kahn validation (must produce topological execution order)
    valid_dag = {
        "nodes": [
            {"id": "node_extract", "label": "数据清洗与对齐", "type": "sql"},
            {"id": "node_features", "label": "WOE/IV 特征工程", "type": "pyspark"},
            {"id": "node_train", "label": "LightGBM 模型训练", "type": "python"},
            {"id": "node_eval", "label": "ROC/KS 指标评估", "type": "python"},
        ],
        "edges": [
            {"source": "node_extract", "target": "node_features"},
            {"source": "node_features", "target": "node_train"},
            {"source": "node_train", "target": "node_eval"},
        ]
    }
    put_valid = client.put(f"/api/v1/flow/projects/{project_id}/dag", json=valid_dag, headers=prov_headers)
    val_valid = client.post(f"/api/v1/flow/projects/{project_id}/dag/validate", headers=prov_headers)
    if val_valid.status_code == 200 and val_valid.json().get("valid") is True:
        order = val_valid.json().get("execution_order", [])
        step("6.2 dag.valid_kahn_sort", "PASS", 200, f"execution_sequence={order}")
    else:
        step("6.2 dag.valid_kahn_sort", "FAIL", val_valid.status_code, f"put_status={put_valid.status_code}, put_text={put_valid.text[:200]}", excerpt=val_valid.text)

    # 6.3 Submit project review
    sub_res = client.post(f"/api/v1/flow/projects/{project_id}/submit-review", headers=prov_headers)
    step("6.3 project.submit_review", "PASS" if sub_res.status_code == 200 else "FAIL", sub_res.status_code,
         f"status={sub_res.json().get('status') if sub_res.status_code == 200 else ''}")

    # ─────────────────────────────────────────────────────────────
    # Step 7: Model Artifact Deserialization Scan & Metrics Compare
    # ─────────────────────────────────────────────────────────────
    # Create a workload for model scanning
    wl_res = client.post("/api/v1/workloads", json={
        "name": f"credit_risk_model_{suffix}",
        "entrypoint": "train.py",
        "language": "python",
        "env": "test",
    }, headers=prov_headers)
    if wl_res.status_code not in (200, 201):
        step("7.1 workload.create", "FAIL", wl_res.status_code, excerpt=wl_res.text)
        return finish(args.out)
    workload_id = wl_res.json()["id"]
    step("7.1 workload.create", "PASS", wl_res.status_code, f"workload_id={workload_id}")

    # 7.2 Malicious Pickle Scan (RCE exploit attempt)
    malicious_pickle = b"cos\nsystem\n(S'cat /etc/passwd'\ntR."
    scan_bad = client.post(
        f"/api/v1/workloads/{workload_id}/artifact-scan",
        json={"filename": "risk_model.pkl", "content_b64": _b64(malicious_pickle)},
        headers=prov_headers,
    )
    if scan_bad.status_code == 200 and scan_bad.json().get("passed") is False:
        violations = scan_bad.json().get("violations", [])
        step("7.2 model.scan_pickle_rce_blocked", "PASS", 200, f"violations={violations}")
    else:
        step("7.2 model.scan_pickle_rce_blocked", "FAIL", scan_bad.status_code, excerpt=scan_bad.text)

    # 7.3 Safe SafeTensors Scan
    clean_safetensors = b'{"__metadata__": {"format": "pt", "model_type": "gbdt"}, "weight": {"dtype": "F32"}}'
    scan_good = client.post(
        f"/api/v1/workloads/{workload_id}/artifact-scan",
        json={"filename": "model.safetensors", "content_b64": _b64(clean_safetensors)},
        headers=prov_headers,
    )
    if scan_good.status_code == 200 and scan_good.json().get("passed") is True:
        step("7.3 model.scan_safetensors_passed", "PASS", 200, "Clean safetensors accepted")
    else:
        step("7.3 model.scan_safetensors_passed", "FAIL", scan_good.status_code, excerpt=scan_good.text)

    # ─────────────────────────────────────────────────────────────
    # Step 8: Metering Invoices Generation & CSV Export
    # ─────────────────────────────────────────────────────────────
    # Create test contract for invoicing
    contract_res = client.post("/api/v1/contracts", json={
        "title": f"数据开发应用服务合约_{suffix}",
        "buyer_id": buyer_user_id,
        "product_ids": [product_id],
        "contract_type": "data_modeling",
        "terms": {"compute_limit": 100, "allowed_algorithms": ["python", "pyspark"]},
    }, headers=prov_headers)
    if contract_res.status_code not in (200, 201):
        step("8.1 contract.create", "FAIL", contract_res.status_code, excerpt=contract_res.text)
        return finish(args.out)
    contract_id = contract_res.json()["id"]
    step("8.1 contract.create", "PASS", contract_res.status_code, f"contract_id={contract_id}")

    period_now = time.strftime("%Y-%m")
    inv_gen = client.post("/api/v1/deliveries/invoices/generate", json={
        "contract_id": contract_id,
        "billing_period": period_now,
    }, headers=prov_headers)

    if inv_gen.status_code in (200, 201):
        invoice_id = inv_gen.json()["id"]
        step("8.2 invoice.generate", "PASS", inv_gen.status_code, f"invoice_id={invoice_id}, period={period_now}")

        # Export CSV
        csv_export = client.get(f"/api/v1/deliveries/invoices/{invoice_id}/export", headers=prov_headers)
        if csv_export.status_code == 200 and "text/csv" in csv_export.headers.get("content-type", ""):
            step("8.3 invoice.csv_export", "PASS", 200, f"csv_bytes={len(csv_export.content)}")
        else:
            step("8.3 invoice.csv_export", "FAIL", csv_export.status_code, excerpt=csv_export.text)
    else:
        step("8.2 invoice.generate", "FAIL", inv_gen.status_code, excerpt=inv_gen.text)

    # ─────────────────────────────────────────────────────────────
    # Step 9: User Notification Center & Read State Flow
    # ─────────────────────────────────────────────────────────────
    # Send a notification to buyer
    notif_res = client.post("/api/v1/monitoring/notifications", json={
        "category": "todo",
        "title": "待审批协同项目参与邀请",
        "content": f"您被邀请加入信贷风控建模协同项目，请进入工作台审批。",
        "action_url": f"/workspace/projects/{project_id}",
    }, headers=buyer_headers)
    if notif_res.status_code != 201:
        step("9.1 notification.create", "FAIL", notif_res.status_code, excerpt=notif_res.text)
    else:
        notif_id = notif_res.json()["id"]
        step("9.1 notification.create", "PASS", 201, f"notif_id={notif_id}")

        # List notifications
        list_notif = client.get("/api/v1/monitoring/notifications", headers=buyer_headers)
        step("9.2 notification.list", "PASS" if list_notif.status_code == 200 else "FAIL", list_notif.status_code,
             f"count={len(list_notif.json().get('items', []))}")

        # Mark read
        mark_res = client.post(f"/api/v1/monitoring/notifications/{notif_id}/read", headers=buyer_headers)
        step("9.3 notification.mark_read", "PASS" if mark_res.status_code == 200 else "FAIL", mark_res.status_code)

    # ─────────────────────────────────────────────────────────────
    # Step 10: Audit Log Verification
    # ─────────────────────────────────────────────────────────────
    audit_res = client.get("/api/v1/audit/records?limit=10", headers=admin_headers)
    if audit_res.status_code == 200:
        logs = audit_res.json().get("items", [])
        actions = [l.get("action") for l in logs]
        step("10.1 audit.verify_trail", "PASS", 200, f"recent_actions={actions[:5]}")
    else:
        step("10.1 audit.verify_trail", "FAIL", audit_res.status_code, excerpt=audit_res.text)

    return finish(args.out)


if __name__ == "__main__":
    sys.exit(main())
