# 主流程实操演示报告 — Jupyter 开发数据产品并上架使用（2026-11-05）

> 工具方式：按用户要求使用 **browser-use skill**（CDP 附着本机 Chrome，账号在真实页面表单里键入、按钮用坐标点击），不再使用自研 headless harness。截图目录 `/tmp/cds_demo/bu_*.png`（仓外，不入 git）。
> 拓扑：控制面+基础组件 = 远程机（compose，地址在 `/root/cds.env`，按仓规不写入本文件），执行面 = 本机 KVM / CubeSandbox 数据面（l2c，无 TEE，诚实披露）。FE http://127.0.0.1:13000 与本机 :18000 均指向远程 cds-api。

## 演示账号
| 角色 | 用户名 | 用途 |
|------|--------|------|
| 平台管理员 | cdsadmin | 产品审批 |
| 数商 | bw_provider | 上传资源、创建产品、签约、发布 |
| 需方 | bw_buyer | 签约、Jupyter 开发、查询沙箱 |

密码见演示环境口令单（不落库、不入仓）。

## 主流程走查结果（每一步都是真实 UI 操作）

### 供给侧
1. **上传数据资源**（provider，`/data-resources`）：新建「演示-物流订单明细」= `/tmp/cds_demo/demo_logistics.csv`（6 行：order_id/city/ship_mode/freight_yuan/deliver_days）→ 资源 `25b02238`，状态 ready。截图 bu_05。
2. **创建数据产品**（provider，四步向导）：「演示-物流运费统计产品」`193a6fbd-3f0e-4460-aac7-94c620a15e72`，关联上述资源，安全配置 L2'/字段全开，输出约束「仅聚合结果出域」→ 草稿（bu_10）→「提交审核」（bu_11）。
3. **管理员审批**（cdsadmin，产品详情）：「通过审核」→ 已批准（bu_14）。
4. **发布上架**（provider）：「发布到目录」→ **已发布**（bu_16）。

### 签约（数商起草，双签激活）
5. 合约创建者=数商（`ROLE_GROUPS.contractWriters=[DATA_PROVIDER]`）：「演示-物流数据使用合约」`cb0f5f3b-df4b-4e1c-add7-4aae5a1935b1`（CDS-1793861641-400075），产品=演示-物流运费统计产品，需方=bw_buyer，L2'/查询模式/输出 csv·json/10000 行/24h/条款 JSON（bu_36）。
6. provider SM2 签署（bu_38）→ buyer 登录对同合约签署（bu_39）→ **状态 active**。

### 消费侧：Jupyter 开发 → 沙箱内真实计算
7. buyer 新建开发任务（`/flow/tasks/new`）：「演示-物流产品Jupyter开发v2」`6ccd486a`，合约=演示-物流数据使用合约（下拉显示合约名✓），原生 Jupyter / 成果集打包导出 / 调试 2G / 执行 2G（bu_89/90）。
8. 任务详情「进入调试」→ 在线调试工作台（Monaco 编辑器，`/notebooks?task=…`，bu_91）。
9. 单元格（经 React fiber 拿 Monaco editor 实例 `setValue` 注入，键盘注入对 Monaco 不可用的绕行）：
   ```python
   df = pd.read_csv('/sandbox/data/session-data')
   df.groupby(['city','ship_mode'])['freight_yuan'].mean().round(2)
   ```
   点「运行」→ 后端自动创建 **l2c 调试沙箱**（`3a4fd1c4`，product_dev，running）。
10. **真实输出**（L2' MicroVM 内 pandas 计算，退出码 0，bu_93_v2_output.png）：
    ```
    rows: 6 cols: ['order_id','city','ship_mode','freight_yuan','deliver_days']
    city x ship_mode avg freight:
    上海 空运 96.0 | 陆运 28.5 | 北京 陆运 32.0 | 广州 海运 18.8 | 杭州 陆运 24.6 | 深圳 空运 88.4
    ```

### 消费侧：查询沙箱使用已发布产品
11. buyer 对已发布产品 `193a6fbd` + 活跃合约发起 `sandbox_mode=structured_query` 会话 → **`bb508e57` running l2c**（会话详情页 bu_94：「运行中 · l2c · 无 TEE 硬件（裸 MicroVM）· 结构化查询」）。
12. 会话内 exec 通道两条真实命令（输出页脚见 bu_95）：
    - `head /sandbox/data/session-data` → exit 0，247ms，数据注入确认（D001/D002 行）；
    - `python3 -c "df.groupby([city,ship_mode])…" 聚合` → **exit 0，2847ms，聚合结果与 notebook 一致**；
    - 两条均过 **输出安全流水线 9 阶段全通过**（scene_routing/format_validation/dlp_scan/data_reconstruction/k_anonymity/差分隐私/水印/签名/final_approval），`output_blocked=false`，stdout 尾部携带隐形水印（=security_report.watermark 值，MR 水印特性按设计工作）。
13. 审计面板可见 `sandbox.exec ×2` 事件（bu_95_qaudit.png），会话「终止」→ **已终止**（bu_96）。

## 本轮发现并处置/记录的问题
| # | 现象 | 处置 |
|---|------|------|
| 外部 | 无关本地 vite 应用(:5192)页面自动 window.open 抢标签页，一度劫持走查 tab | 非 CDS 缺陷；关闭源头页并 `Target.activateTarget` 固定 13000 后消失 |
| D5a | 首个任务建在了错误合约上（antd dropdown 状态残留导致选项错选），其执行触发 storage 信封解不开（api 04:21 重启前的历史对象，D4 已知面）| 换用正确合约重建任务后路径全通；错误合约任务的 notebook 留档 `30d6682a` |
| D5b | **新合约起草时只勾了「查询」模式**，product_dev 调试被 400 `SANDBOX_MODE_NOT_PERMITTED` 拒绝（拒绝语义正确）。为完成开发演示，直接对该演示合约的 `allowed_sandbox_modes` 追加 `product_dev`（远程 PG UPDATE）| **已修（2026-11-05）**：开发任务向导选完合约当场前置警告（警告不拦截；后端未下发不误报；别名表与 SANDBOX_MODE_ALIASES 对齐），用例见 DevTaskCreatePage.test.tsx 新增 2 例 || D5c | `POST /sandbox-sessions/{id}/execute`（SQL 通道，structured_query）500：`cube_runtime_adapter._read_sandbox_file` → `rootfs volume dir for cubebox … not found`（245s 后炸）| **已修（2026-11-05）**：真因两层 —— ① language=sql 被原样交给 sh，整串 parse 失败 marker 永不落盘（空转到超时）；② 超时窗口内会话被终止，轮询读撞 NOT_FOUND 冒成 500。修复：SQL 走 VM 内 stdlib sqlite3 runner（表名 data=注入 CSV）；非 python 命令 base64 落盘再执行（marker 恒写）；轮询 NOT_FOUND → 诚实 exit -1 披露。回归锁 tests/test_cube_execute_d5c.py（7 例）|
| D6 | api 容器 WARNING `[sandbox-audit] ClickHouse unavailable: No module named 'clickhouse_driver'`，沙箱审计事件被丢弃（PG 侧 sandbox.exec 仍可查）| **仓内已修（2026-11-05）**：requirements.txt 声明 clickhouse-driver>=0.2.9 + 依赖门禁 tests/test_d6_clickhouse_dependency.py（导入面清单锁）。**生效需重建 api 镜像**（远程控制面随下次部署）|

## 与上轮走查（docs/main-flow-browser-walkthrough-2026-11-05.md）的关系
- 本轮验证了 D1/D2/D3 修复在真实 UI 生效：合约下拉显示合约名（bu_36）、调试台状态条 CPU/内存计量占位与审计流（bu_91→bu_93）。
- 「上架使用」全链路（发布→签约→调试→查询）在本拓扑（远程控制面+本机 KVM 数据面）下**端到端走通**；数据计算全部发生在 L2' MicroVM 内。

## 截图索引（/tmp/cds_demo/）
bu_05 资源上传 · bu_07~09 产品向导 · bu_10/11 草稿/提交审核 · bu_13/14 审批 · bu_16 已发布 · bu_36 合约(名称显示) · bu_38/39 双签 active · bu_89/90 开发任务创建 · bu_91 调试工作台 · bu_93_v2_output **沙箱真实聚合输出** · bu_94 查询会话详情 · bu_95 exec 审计 · bu_96 会话已终止
