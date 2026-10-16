import { Alert, Card, Collapse, Divider, Space, Steps, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';

const { Title, Paragraph, Text } = Typography;

interface Endpoint {
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  path: string;
  note: string;
}

interface FlowStep {
  title: string;
  who: string;
  summary: string;
  endpoints: Endpoint[];
  pitfalls?: string[];
}

const METHOD_COLORS: Record<Endpoint['method'], string> = {
  GET: 'blue',
  POST: 'green',
  PUT: 'orange',
  PATCH: 'purple',
  DELETE: 'red',
};

// 全部路径逐条对过 app.main:app 的真实路由表（GET /openapi.json），不存在的不写。
const FLOW_STEPS: FlowStep[] = [
  {
    title: '数据资源',
    who: '数据提供方',
    summary: '原始数据入库并留痕：上传→抽样查看→脱敏。资源是产品的前置，未绑定资源的发布请求会被拒。',
    endpoints: [
      { method: 'POST', path: '/api/v1/data-resources/upload', note: 'multipart 上传原始数据' },
      { method: 'GET', path: '/api/v1/data-resources/', note: '我的资源列表' },
      { method: 'POST', path: '/api/v1/data-resources/{resource_id}/sample', note: '抽样预览' },
      { method: 'POST', path: '/api/v1/data-resources/{resource_id}/desensitize', note: '脱敏处理' },
    ],
  },
  {
    title: '数据产品',
    who: '数据提供方 → 平台审核',
    summary:
      '状态机是硬的：draft →(submit) reviewing →(approve，仅管理员) approved →(publish) published。跳过一步就是 400。',
    endpoints: [
      { method: 'POST', path: '/api/v1/data-products', note: '建产品（须关联 resource_id）' },
      { method: 'POST', path: '/api/v1/data-products/{id}/test-data/sample', note: '测试数据：抽样' },
      { method: 'POST', path: '/api/v1/data-products/{id}/submit', note: 'draft → reviewing' },
      { method: 'POST', path: '/api/v1/data-products/{id}/approve', note: 'reviewing → approved（管理员）' },
      { method: 'POST', path: '/api/v1/data-products/{id}/publish', note: 'approved → published（进目录）' },
      { method: 'GET', path: '/api/v1/catalog', note: '买方按目录选数据' },
    ],
    pitfalls: ['未关联数据资源就 publish → 400 "Product must be linked to a data resource before publishing"'],
  },
  {
    title: '合约签署',
    who: '提供方 + 买方，平台见证',
    summary:
      '合约决定一切下游边界：允许的沙箱级别与模式、交付方式、自动计算频率、沙箱资源规格、DP 预算与最大输出行数。',
    endpoints: [
      { method: 'POST', path: '/api/v1/contracts', note: '建草稿（含 delivery_methods / sandbox_resource_spec）' },
      { method: 'POST', path: '/api/v1/contracts/{id}/flow-config', note: '交付方式 + 自动计算 + 资源规格' },
      { method: 'POST', path: '/api/v1/contracts/{id}/sign', note: '双方 SM2 签名' },
      { method: 'POST', path: '/api/v1/contracts/{id}/activate', note: '生效' },
      { method: 'GET', path: '/api/v1/contracts/{id}/policy', note: '读执行策略' },
      { method: 'GET', path: '/api/v1/contracts/{id}/dp-budget', note: '差分隐私预算余量' },
    ],
    pitfalls: ['允许沙箱级别固定为 l2c（MicroVM 单一路径），历史级别不再可选'],
  },
  {
    title: '开发任务 / Notebook 测试沙箱',
    who: '买方开发者',
    summary:
      '先在能力探测上确认数据面可用，再开 Notebook 与测试沙箱写代码。数据面不可用时后端 fail-closed，不降级到进程隔离。',
    endpoints: [
      { method: 'GET', path: '/api/v1/flow/runtime-status', note: '执行前预检：available / message' },
      { method: 'POST', path: '/api/v1/notebooks', note: '建开发任务（绑定合约 + 数据产品）' },
      { method: 'POST', path: '/api/v1/notebooks/{id}/dev-session', note: '起测试沙箱（返回 sandbox_level=l2c）' },
      { method: 'POST', path: '/api/v1/notebooks/{id}/cells/execute', note: '执行单元格（≤256KB 入 / ≤1MB 出）' },
      { method: 'POST', path: '/api/v1/notebooks/{id}/run', note: '整本跑（600s 上限）' },
      { method: 'GET', path: '/api/v1/notebooks/{id}/jupyter', note: 'Jupyter 代理地址（未配置则 url=null）' },
      { method: 'POST', path: '/api/v1/dev-sandbox/sessions', note: '轻量替代：模板化开发沙箱' },
    ],
    pitfalls: [
      'available=false 时执行入口一律 400 RUNTIME_UNAVAILABLE——这是 fail-closed，不是故障',
      '代码先过静态扫描（FATAL → 422），出沙箱再过出口审查，两侧都写审计',
    ],
  },
  {
    title: '固化发布',
    who: '买方开发者',
    summary:
      'Notebook 里的验证结果不能直接当生产用：必须把入口代码固化成工作负载，并在测试沙箱跑一次留证，发布门禁比较的是哈希。',
    endpoints: [
      { method: 'POST', path: '/api/v1/workloads', note: '固化入口代码 entrypoint' },
      { method: 'POST', path: '/api/v1/workloads/{id}/test', note: '测试沙箱执行，产 test-run' },
      { method: 'GET', path: '/api/v1/workloads/{id}/test-runs', note: '测试记录（report 只在单条详情里）' },
      { method: 'POST', path: '/api/v1/workloads/{id}/publish', note: '发布为生产版本（is_latest）' },
      { method: 'POST', path: '/api/v1/workloads/{id}/retire', note: '下线' },
      { method: 'POST', path: '/api/v1/workloads/{id}/rollback', note: '回滚到上一生产版本' },
    ],
    pitfalls: ['tested_hash ≠ entrypoint_hash → 412 WORKLOAD_HASH_MISMATCH：改过代码必须重跑 test'],
  },
  {
    title: '生产执行',
    who: '买方 / 平台调度',
    summary:
      '按合约频率自动跑或手动触发一次。手动触发是同步长请求——一次完整沙箱计算跑完才返回，客户端超时要给足。',
    endpoints: [
      { method: 'GET', path: '/api/v1/compute/schedule', note: '调度总览（下次执行时间等）' },
      { method: 'POST', path: '/api/v1/compute/runs', note: '手动触发，体只有 {contract_id}' },
      { method: 'GET', path: '/api/v1/compute/runs/{run_id}', note: '单次运行详情' },
      { method: 'POST', path: '/api/v1/compute/runs/{run_id}/cancel', note: '取消排队中的运行' },
    ],
    pitfalls: ['无法指定工作负载版本或运行参数：生产执行用的就是 is_latest 那份固化代码'],
  },
  {
    title: '三通道交付',
    who: '买方消费',
    summary: '数据集（拉）· OpenAPI（在线调用）· 结果推送（平台推）。通道必须先在合约里选定，否则对应端点拒绝创建。',
    endpoints: [
      { method: 'POST', path: '/api/v1/deliveries/dataset/bundle', note: '成果打包（水印 + 出口审查），返回 download_url' },
      { method: 'GET', path: '/api/v1/deliveries/dataset/{bundle_id}/download', note: '下载（同一 Bearer，按合约归属校验）' },
      { method: 'POST', path: '/api/v1/deliveries/openapi/endpoints', note: '发布在线端点（可设 quota_total / 限流）' },
      { method: 'GET', path: '/api/v1/deliveries/openapi/contract/{contract_id}', note: 'OpenAPI 文档 + 端点清单' },
      { method: 'POST', path: '/open/v1/{slug}', note: '调用端点（见下方 curl）' },
      { method: 'POST', path: '/api/v1/deliveries/webhooks', note: '注册结果推送（SSRF 校验 + 密钥）' },
      { method: 'POST', path: '/api/v1/deliveries/webhooks/{id}/test', note: '发一条 HMAC 签名的测试事件' },
      { method: 'GET', path: '/api/v1/deliveries/webhooks/{id}/deliveries', note: '推送记录（终态不重投）' },
    ],
    pitfalls: [
      '交付包只能凭创建响应里的 bundle_id 取——没有列表端点，请自行保存',
      '加固前生成的历史包下载返回 410 BUNDLE_REQUIRES_REVIEW，重新生成即可',
      '推送失败退避 30→3600s、连续失败 10 次自动暂停；4xx 不重试',
    ],
  },
  {
    title: '存证验真',
    who: '监管 / 运营 / 平台',
    summary:
      '每条审计记录带 SM2 签名；锚定把一批记录聚成 SM3 Merkle 根上链，验真时按同一事件串重算哈希并重建根再问链。',
    endpoints: [
      { method: 'GET', path: '/api/v1/audit/records', note: '审计记录查询（权威源是 PG，ClickHouse 仅旁路）' },
      { method: 'POST', path: '/api/v1/audit/anchor', note: '按 record_ids 锚定，返回 merkle_root / tx_hash' },
      { method: 'GET', path: '/api/v1/audit/verify/{record_id}', note: '验真（未上链时返回 verified=false + 原因）' },
      { method: 'GET', path: '/api/v1/audit/merkle-proof/{record_id}', note: 'Merkle 路径（未上链 → 400）' },
      { method: 'GET', path: '/api/v1/audit/compliance-report', note: '合规报告' },
    ],
    pitfalls: [
      '这三个端点在 scope 之外还要求 OPERATOR / REGULATOR / ADMIN 角色',
      '响应里的 backend / is_consortium_chain / chain_anchoring 是真实档位披露：本地防篡改日志 ≠ 联盟链上链',
    ],
  },
];

const AUTH_NOTES = [
  '所有控制面请求走 `Authorization: Bearer <token>`：登录 JWT，或平台 API Key `cds_ak_…`（创建时由管理员在「系统设置 → API Key」签发，明文只在创建/轮转时出现一次）。',
  'API Key 带功能域 scope（如 `data:read`、`sessions:write`、`gateway:write`）；调用以签发人身份执行，权限不够即 403 API_KEY_SCOPE_DENIED。',
  '调用 OpenAPI 结果通道需要 `gateway` 组的 write scope，且调用身份必须是端点归属方或合约当事方。',
  '错误统一为 W2 信封 `{code, message, detail, request_id}`；429 可能带 `Retry-After`，按它退避。',
];

const OPENAPI_CURL = `curl -X POST "$CDS_BASE_URL/open/v1/<slug>" \\
  -H "Authorization: Bearer $CDS_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"customer_id": "C-0001", "as_of": "2026-10-16"}'`;

const OPENAPI_RESPONSE = `{
  "result": { "score": 712, "features_hash": "9f2c…" },
  "output_rows": 1,
  "contract_id": "0f0e…",
  "endpoint": "risk-score",
  "workload_hash": "3ab1…",
  "request_sha256": "c81d4f2e9a7b0e55",
  "duration_ms": 812
}`;

const WEBHOOK_VERIFY = `# 验签（平台 → 你的接收端）
expected = hmac_sha256(secret, f"{X-CDS-Timestamp}.{原始请求体字节}").hexdigest()
accept if hmac.compare_digest(expected, X-CDS-Signature-Sha256)`;

const endpointColumns: ColumnsType<Endpoint> = [
  {
    title: '方法',
    dataIndex: 'method',
    width: 84,
    render: (method: Endpoint['method']) => <Tag color={METHOD_COLORS[method]}>{method}</Tag>,
  },
  {
    title: '路径',
    dataIndex: 'path',
    render: (path: string) => <Text code>{path}</Text>,
  },
  { title: '说明', dataIndex: 'note' },
];

export default function FlowGuide() {
  return (
    <div>
      <Title level={4}>开发指引</Title>
      <Paragraph type="secondary">
        端到端八步：数据资源 → 数据产品 → 合约签署 → 开发任务（测试沙箱）→ 固化发布 → 生产执行 → 三通道交付 → 存证验真。
        下面每一条路径都是后端当前真实存在的路由；未列出的能力就是还没有。
      </Paragraph>

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="先读这一条：沙箱只有 MicroVM 单一路径"
        description="执行面统一是 CubeSandbox 微虚拟机（级别 l2c）。没有可挑选的「更轻的隔离档」：数据面不可用时后端直接拒绝执行（400 RUNTIME_UNAVAILABLE），而不是悄悄降级。执行前用 GET /api/v1/flow/runtime-status 预检。"
      />

      <Card title="鉴权与错误约定" size="small" style={{ marginBottom: 16 }}>
        <ul style={{ margin: 0, paddingLeft: 20 }}>
          {AUTH_NOTES.map((note) => (
            <li key={note}>
              <Text>{note.split('`').map((part, idx) => (idx % 2 ? <Text code key={idx}>{part}</Text> : part))}</Text>
            </li>
          ))}
        </ul>
      </Card>

      <Card size="small" style={{ marginBottom: 16 }}>
        <Steps
          direction="vertical"
          size="small"
          current={FLOW_STEPS.length - 1}
          items={FLOW_STEPS.map((step, idx) => ({
            title: `${idx + 1}. ${step.title}`,
            description: (
              <div style={{ paddingBottom: 8 }}>
                <Paragraph type="secondary" style={{ marginBottom: 8 }}>
                  {step.summary}
                </Paragraph>
                <Table<Endpoint>
                  size="small"
                  rowKey="path"
                  columns={endpointColumns}
                  dataSource={step.endpoints}
                  pagination={false}
                  showHeader={false}
                />
                {step.pitfalls?.length ? (
                  <ul style={{ marginTop: 8, marginBottom: 0, paddingLeft: 18 }}>
                    {step.pitfalls.map((p) => (
                      <li key={p}>
                        <Text type="warning">{p}</Text>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ),
          }))}
        />
      </Card>

      <Divider orientation="left">OpenAPI 结果通道：可直接粘贴的调用</Divider>
      <Card size="small" style={{ marginBottom: 16 }}>
        <Paragraph>
          <Text code>{'CDS_API_KEY'}</Text> 填平台 API Key（<Text code>cds_ak_…</Text>，需{' '}
          <Text code>gateway:write</Text>）或你的登录 JWT；<Text code>{'CDS_BASE_URL'}</Text> 形如{' '}
          <Text code>https://your-cds-host</Text>。请求体是任意 JSON 对象，会原样交给已发布的工作负载。
        </Paragraph>
        <Paragraph copyable={{ text: OPENAPI_CURL }}>
          <pre style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{OPENAPI_CURL}</pre>
        </Paragraph>
        <Divider dashed style={{ margin: '12px 0' }} />
        <Paragraph type="secondary" style={{ marginBottom: 4 }}>
          响应（<Text code>result</Text> 之外都是平台生成的运行元数据，可直接落库对账）：
        </Paragraph>
        <pre style={{ margin: 0 }}>{OPENAPI_RESPONSE}</pre>
        <Divider dashed style={{ margin: '12px 0' }} />
        <Paragraph style={{ marginBottom: 0 }}>
          失败语义：429 <Text code>RATE_LIMITED</Text> / <Text code>QUOTA_EXCEEDED</Text>（前者带{' '}
          <Text code>Retry-After</Text>，按头退避）；502 <Text code>ENDPOINT_DISABLED</Text> /{' '}
          <Text code>WORKLOAD_UNAVAILABLE</Text> 重试无意义，需人工处理；额度用尽后调用会一直 429，
          在「配额用量」页或 <Text code>GET /api/v1/deliveries/openapi/endpoints</Text> 核对{' '}
          <Text code>quota_total</Text> 与 <Text code>calls_used</Text>。
        </Paragraph>
      </Card>

      <Collapse
        size="small"
        items={[
          {
            key: 'webhook',
            label: '结果推送：如何验签',
            children: (
              <>
                <Paragraph>
                  平台推送带三个头：<Text code>X-CDS-Timestamp</Text>、<Text code>X-CDS-Event-Id</Text>、
                  <Text code>X-CDS-Signature-Sha256</Text>。签名密钥由你注册推送时自带，或走{' '}
                  <Text code>POST /api/v1/deliveries/webhooks/{'{id}'}/rotate-secret</Text> 轮换（只返回一次）。
                </Paragraph>
                <pre style={{ margin: 0 }}>{WEBHOOK_VERIFY}</pre>
                <Paragraph type="secondary" style={{ marginBottom: 0 }}>
                  必须用原始请求体字节验签，别先解析再重新序列化；重复投递用{' '}
                  <Text code>X-CDS-Event-Id</Text> 幂等去重。
                </Paragraph>
              </>
            ),
          },
          {
            key: 'gaps',
            label: '已知接口缺口（不要绕路猜）',
            children: (
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                <li>交付包没有列表端点：bundle_id 必须在创建响应处自持。</li>
                <li>生产执行不能指定工作负载版本或参数。</li>
                <li>API 响应里没有区块高度，只有 tx_hash / anchor_id / confirmed / backend。</li>
                <li>Jupyter 代理地址取决于部署是否配置了代理基址，未配置时 url 为 null。</li>
              </ul>
            ),
          },
        ]}
      />

      <Space style={{ marginTop: 16 }}>
        <Text type="secondary">配额与额度实时值见</Text>
        <Text code>/flow/quota</Text>
        <Text type="secondary">；本页只描述流程与接口，不含任何估算值。</Text>
      </Space>
    </div>
  );
}
