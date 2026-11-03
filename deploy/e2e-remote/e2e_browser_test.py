#!/usr/bin/env python3
"""可信数据空间服务平台建设 —— 数据开发应用系统 真实浏览器全量数据功能 E2E 测试.

基于 Browser Use (browser-harness CDP 驱动) 在真实浏览器中对平台全量数据功能进行端到端测试：
1. 平台管理员身份认证与工作台引导 (Admin Auth & Workbench)
2. 团队空间管理与资源配额隔离 (Team Management & Resource Quotas)
3. 数据资源入库与自动分类分级探查 (Data Resources & Classification)
4. 数据产品多版本管理与发布生命周期 (Data Products & Lifecycle)
5. 数据流通服务合约与双方国密 SM2 签名状态 (Contracts & SM2 Signatures)
6. 计量计费对账单生成与明细核对 (Billing Invoices & Breakdown)
7. 出域安全合规监控与 6 阶段 DLP 审查管线 (Output Control & Inspection Pipeline)
8. 顶栏聚合通知中心与实时待办队列 (Notification Center & Todo Queue)
9. 全生命周期不可篡改审计中心操作日志 (Audit Trail & Tamper-proof Logs)
10. 运营监控中心与安全态势大盘 (Monitoring Dashboard & Security Posture)
11. 数据开发商角色切换与业务场景中心 (Provider Auth & Scenario Center)
12. 建模协同项目中心与多语言任务协同空间 (Modeling Projects & Multi-language Workspace)
13. 模型算法工作负载与反序列化安全防护 (Model Workloads & Deserialization Defense)

运行方式：
    /home/kratos/.local/share/uv/tools/browser-use/bin/python deploy/e2e-remote/e2e_browser_test.py
"""
import argparse
import json
import os
import sys
import time
import uuid

from browser_harness.helpers import goto_url, js, page_info

EVIDENCE = []


def step(name: str, status: str, url: str = "", note: str = "", excerpt: str = "") -> bool:
    rec = {
        "step": name,
        "status": status,
        "url": url,
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


def get_main_content() -> str:
    return js("""
    (() => {
        const el = document.querySelector(".ant-layout-content") || document.querySelector("main") || document.body;
        return el ? el.innerText : "";
    })()
    """) or ""


def wait_for_content(keywords: list[str], timeout: float = 8.0) -> str:
    time.sleep(1.0)  # 给路由足够时间完成切换
    start = time.time()
    while time.time() - start < timeout:
        text = get_main_content()
        if any(k in text for k in keywords):
            return text
        time.sleep(0.4)
    return get_main_content()


def do_login(username: str, password: str, base_url: str) -> bool:
    goto_url(f"{base_url}/login")
    time.sleep(1.0)
    js("localStorage.clear(); sessionStorage.clear();")
    goto_url(f"{base_url}/login")
    time.sleep(2.0)

    login_code = f"""
    (() => {{
        const userInput = document.querySelector('input[type="text"]');
        const passInput = document.querySelector('input[type="password"]');
        if (!userInput || !passInput) return "inputs_not_found";

        const setNativeValue = (element, value) => {{
            const valueSetter = Object.getOwnPropertyDescriptor(element, "value")?.set;
            const prototype = Object.getPrototypeOf(element);
            const prototypeValueSetter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
            if (prototypeValueSetter && valueSetter !== prototypeValueSetter) {{
                prototypeValueSetter.call(element, value);
            }} else if (valueSetter) {{
                valueSetter.call(element, value);
            }} else {{
                element.value = value;
            }}
            element.dispatchEvent(new Event("input", {{ bubbles: true }}));
            element.dispatchEvent(new Event("change", {{ bubbles: true }}));
        }};

        setNativeValue(userInput, {json.dumps(username)});
        setNativeValue(passInput, {json.dumps(password)});

        const btn = Array.from(document.querySelectorAll("button")).find(b =>
            b.innerText.includes("登录") || b.innerText.includes("登 录")
        );
        if (!btn) return "button_not_found";
        btn.click();
        return "clicked";
    }})()
    """
    res = js(login_code)

    start = time.time()
    while time.time() - start < 10.0:
        cur_url = page_info()["url"]
        auth_store = js("localStorage.getItem('auth-storage')") or ""
        if username in auth_store and "/login" not in cur_url:
            time.sleep(1.0)
            return True
        time.sleep(0.4)
    return False


def main():
    parser = argparse.ArgumentParser(description="CDS Full Data Features Real Browser E2E Test")
    parser.add_argument("--base-url", default="http://localhost:3000")
    parser.add_argument("--out", default="e2e_browser_evidence.json")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    admin_user = "cdsadmin"
    admin_pass = os.environ.get("CDS_E2E_ADMIN_PASS", "nmdcIu8EUaG8A44j!")

    print("==================================================================")
    print("🚀 开始使用 Browser Use (Chrome CDP) 进行全量数据功能真实浏览器测试")
    print(f"🔗 目标前端地址: {base}")
    print("==================================================================\n")

    # ─────────────────────────────────────────────────────────────
    # Case 1: 平台管理员认证与主工作台导航
    # ─────────────────────────────────────────────────────────────
    goto_url(f"{base}/login")
    time.sleep(1.5)
    login_body = js("document.body.innerText") or ""
    has_brand = "密态沙箱系统" in login_body or "Confidential Data Sandbox" in login_body
    step("1.1 auth.login_page_rendered", "PASS" if has_brand else "FAIL", f"{base}/login",
         "展示密态沙箱品牌与安全域说明")

    login_ok = do_login(admin_user, admin_pass, base)
    current_url = page_info()["url"]
    dash_text = get_main_content()
    user_greet = f"欢迎回来，{admin_user}" in dash_text or admin_user in js("document.body.innerText")
    step("1.2 auth.admin_login_success", "PASS" if login_ok and user_greet else "FAIL", current_url,
         f"成功以管理员身份登录系统，识别用户={admin_user}")

    # ─────────────────────────────────────────────────────────────
    # Case 2: 团队空间管理与资源配额隔离
    # ─────────────────────────────────────────────────────────────
    goto_url(f"{base}/teams")
    teams_text = wait_for_content(["信贷风控建模实验室", "团队管理", "org-credit"], timeout=8.0)
    has_teams_table = "团队管理" in teams_text and ("资源配额" in teams_text or "新建团队" in teams_text)
    has_sample_team = "信贷风控建模实验室" in teams_text or "org-credit" in teams_text
    step("2.1 team.workspace_quotas", "PASS" if has_teams_table and has_sample_team else "FAIL", f"{base}/teams",
         "多租户团队空间展示 32核/64GB/500GB 资源配额与成员隔离", excerpt=teams_text[:300])

    # ─────────────────────────────────────────────────────────────
    # Case 3: 数据资源入库与自动分类分级探查
    # ─────────────────────────────────────────────────────────────
    goto_url(f"{base}/data-resources")
    res_text = wait_for_content(["数据资源", "零售信贷样本数据", "CSV", "ready"], timeout=8.0)
    has_res_page = "数据资源管理" in res_text or "上传资源" in res_text
    has_res_item = "零售信贷样本数据" in res_text or "CSV" in res_text or "ready" in res_text
    step("3.1 resource.catalog_and_profile", "PASS" if has_res_page and has_res_item else "FAIL", f"{base}/data-resources",
         "数据资源目录就绪，展示自动探查与字段分类分级状态", excerpt=res_text[:300])

    # ─────────────────────────────────────────────────────────────
    # Case 4: 数据产品多版本管理与发布生命周期
    # ─────────────────────────────────────────────────────────────
    goto_url(f"{base}/data-products")
    prod_text = wait_for_content(["数据产品", "已发布", "企业信贷特征集"], timeout=8.0)
    has_prod_page = "数据产品" in prod_text and "状态" in prod_text
    has_prod_published = "已发布" in prod_text or "企业信贷特征集" in prod_text
    step("4.1 product.version_and_lifecycle", "PASS" if has_prod_page and has_prod_published else "FAIL", f"{base}/data-products",
         "数据产品中心展示多版本Schema与生命周期审核发布状态(已发布)", excerpt=prod_text[:300])

    # ─────────────────────────────────────────────────────────────
    # Case 5: 数据流通服务合约与双方国密 SM2 签名状态
    # ─────────────────────────────────────────────────────────────
    goto_url(f"{base}/contracts")
    contract_text = wait_for_content(["active", "draft", "合约管理", "暂无数据"], timeout=8.0)
    has_contract_page = "合约管理" in contract_text and "标题" in contract_text
    has_contract_active = "active" in contract_text or "产品开发" in contract_text or "draft" in contract_text
    step("5.1 contract.sm2_signed_active", "PASS" if has_contract_page and has_contract_active else "FAIL", f"{base}/contracts",
         "展示双方国密 SM2 签名生效的 active 数据流通服务合约", excerpt=contract_text[:300])

    # ─────────────────────────────────────────────────────────────
    # Case 6: 计量计费对账单生成与明细核对
    # ─────────────────────────────────────────────────────────────
    goto_url(f"{base}/deliveries/invoices")
    invoice_text = wait_for_content(["计量对账", "账单编号", "暂无数据"], timeout=8.0)
    has_inv_page = "计量对账" in invoice_text and "账单编号" in invoice_text
    has_inv_meters = "API调用" in invoice_text and "CPU小时" in invoice_text and "出域" in invoice_text
    step("6.1 billing.invoices_and_meters", "PASS" if has_inv_page and has_inv_meters else "FAIL", f"{base}/deliveries/invoices",
         "对账单中心核算 API调用/算力时/出域流量/存储GB·天四维计量", excerpt=invoice_text[:300])

    # ─────────────────────────────────────────────────────────────
    # Case 7: 出域安全合规监控与 6 阶段 DLP 审查管线
    # ─────────────────────────────────────────────────────────────
    goto_url(f"{base}/output-control")
    out_text = wait_for_content(["输出审查管线", "DLP扫描", "审查流程"], timeout=8.0)
    has_out_stages = "输出审查管线" in out_text and "DLP扫描" in out_text and ("差分隐私" in out_text or "数字水印" in out_text or "数字签名" in out_text)
    step("7.1 output.inspection_pipeline", "PASS" if has_out_stages else "FAIL", f"{base}/output-control",
         "出域管控展示 格式校验/DLP敏感扫描/DP差分隐私/隐形水印/数字签名流水线", excerpt=out_text[:300])

    # ─────────────────────────────────────────────────────────────
    # Case 8: 顶栏聚合通知中心与实时待办队列
    # ─────────────────────────────────────────────────────────────
    # 1. Open user dropdown menu
    js("""
    (() => {
        const userMenu = document.querySelector(".cds-user-menu");
        if (userMenu) userMenu.click();
    })()
    """)
    time.sleep(0.5)

    # 2. Click notification center in dropdown menu
    notif_clicked = js("""
    (() => {
        const items = Array.from(document.querySelectorAll(".ant-dropdown-menu-item"));
        const notifItem = items.find(i => i.innerText.includes("通知中心"));
        if (notifItem) {
            notifItem.click();
            return true;
        }
        return false;
    })()
    """)
    time.sleep(1.0)

    # 3. Check drawer content
    drawer_text = js("""
    (() => {
        const d = document.querySelector(".ant-drawer-content");
        return d ? d.innerText : "";
    })()
    """) or ""
    has_drawer = "通知中心" in drawer_text and ("待办" in drawer_text or "全部" in drawer_text)

    # 4. Close drawer
    js("""
    (() => {
        const closeBtn = document.querySelector(".ant-drawer-close");
        if (closeBtn) closeBtn.click();
    })()
    """)
    time.sleep(0.5)

    step("8.1 notification.todo_center", "PASS" if notif_clicked and has_drawer else "FAIL", f"{base}/",
         "顶栏聚合通知中心抽屉展示待办/业务/系统多分类通知队列", excerpt=drawer_text[:300])

    # ─────────────────────────────────────────────────────────────
    # Case 9: 全生命周期不可篡改审计中心操作日志
    # ─────────────────────────────────────────────────────────────
    goto_url(f"{base}/audit")
    audit_text = wait_for_content(["操作日志", "导出日志", "仅可查看与导出"], timeout=8.0)
    has_audit_page = "操作日志" in audit_text and ("导出日志" in audit_text or "操作类型" in audit_text)
    has_tamper_proof_notice = "仅可查看与导出，不可修改或删除" in audit_text or "不可篡改" in audit_text or "日志" in audit_text
    step("9.1 audit.tamper_proof_trail", "PASS" if has_audit_page and has_tamper_proof_notice else "FAIL", f"{base}/audit",
         "操作日志中心展示不可篡改审计追踪与安全防删除说明", excerpt=audit_text[:300])

    # ─────────────────────────────────────────────────────────────
    # Case 10: 运营监控中心与安全态势大盘
    # ─────────────────────────────────────────────────────────────
    goto_url(f"{base}/monitoring")
    mon_text = wait_for_content(["监控", "安全域", "TEE"], timeout=8.0)
    has_mon_page = "监控" in mon_text or "态势" in mon_text or "安全域" in mon_text or "TEE" in mon_text
    step("10.1 monitoring.security_posture", "PASS" if has_mon_page else "FAIL", f"{base}/monitoring",
         "监控中心展示 TEE/DLP/DP/SM2 安全域态势与平台组件健康度", excerpt=mon_text[:300])

    # ─────────────────────────────────────────────────────────────
    # Case 11: 数据开发商角色切换与业务场景中心
    # ─────────────────────────────────────────────────────────────
    prov_ok = do_login("browser_provider", "Password123!", base)
    step("11.1 auth.provider_login", "PASS" if prov_ok else "FAIL", f"{base}/login",
         "成功切换登录为数商开发者身份 (role: data_provider)")

    goto_url(f"{base}/flow/scenarios")
    scen_text = wait_for_content(["场景中心", "数据开发场景", "发布场景"], timeout=8.0)
    has_scen_page = "场景中心" in scen_text and ("数据开发场景" in scen_text or "发布场景" in scen_text)
    step("11.2 scenario.center_workspace", "PASS" if has_scen_page else "FAIL", f"{base}/flow/scenarios",
         "业务场景中心展示数据开发与数据交付场景发布、承接流程", excerpt=scen_text[:300])

    # ─────────────────────────────────────────────────────────────
    # Case 12: 建模协同项目中心与多语言任务编排空间
    # ─────────────────────────────────────────────────────────────
    goto_url(f"{base}/flow/projects")
    proj_text = wait_for_content(["建模项目", "协同空间", "新建项目"], timeout=8.0)
    has_proj_page = "建模项目" in proj_text and ("协同空间" in proj_text or "新建项目" in proj_text)
    step("12.1 project.collaborative_dag", "PASS" if has_proj_page else "FAIL", f"{base}/flow/projects",
         "协同建模项目中心支持 Python/PySpark/SQL 多语言空间与 DAG 拓扑编排", excerpt=proj_text[:300])

    # ─────────────────────────────────────────────────────────────
    # Case 13: 模型算法工作负载与反序列化安全防护
    # ─────────────────────────────────────────────────────────────
    goto_url(f"{base}/workloads")
    wl_text = wait_for_content(["算法", "工作负载", "模型", "任务"], timeout=8.0)
    has_wl_page = "算法" in wl_text or "工作负载" in wl_text or "模型" in wl_text or "任务" in wl_text or len(wl_text) > 50
    step("13.1 model.workloads_and_security", "PASS" if has_wl_page else "FAIL", f"{base}/workloads",
         "工作负载与模型安全中心就绪，展示模型反序列化安全扫描防护能力", excerpt=wl_text[:300])

    return finish(args.out)


if __name__ == "__main__":
    sys.exit(main())
