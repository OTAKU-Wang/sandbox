import { useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Radio,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { CopyOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import {
  apiKeysApi,
  type ApiKey,
  type ApiKeyActivity,
  type ApiKeyCreated,
  type ApiKeyCreatePayload,
} from '../services/apiKeysApi';
import { QueryErrorAlert, tableEmpty } from '../components/Feedback/QueryFeedback';

const { Title, Text } = Typography;

// SS-12 §4.2 功能组（中文标签 + 英文 group 名），与后端 app/core/scopes.py 的 GROUP_PREFIXES 一致
const SCOPE_GROUPS: { name: string; label: string }[] = [
  { name: 'sessions', label: '会话' },
  { name: 'contracts', label: '契约' },
  { name: 'data', label: '数据' },
  { name: 'pipeline', label: '计算管线' },
  { name: 'audit', label: '审计合规' },
  { name: 'output', label: '输出治理' },
  { name: 'infra', label: '基础设施' },
  { name: 'certs', label: '证书' },
  { name: 'federation', label: '联邦互联' },
  { name: 'gateway', label: '数据网关' },
  { name: 'agent', label: 'Agent 面' },
  { name: 'meta', label: '元信息' },
];

const GROUP_NAMES = SCOPE_GROUPS.map((g) => g.name);
const WRITE_GROUPS = ['sessions', 'contracts', 'data', 'pipeline', 'output', 'gateway', 'agent'];
const READONLY_SCOPES = GROUP_NAMES.map((g) => `${g}:read`);
const OPERATOR_SCOPES = [...READONLY_SCOPES, ...WRITE_GROUPS.map((g) => `${g}:write`)];
const FULL_SCOPES = GROUP_NAMES.map((g) => `${g}:*`);

const PRESET_LABELS: Record<string, string> = {
  readonly: '只读',
  operator: '业务操作',
  full: '全量',
};

const MAX_SCOPE_BADGES = 4;

const EXPIRY_OPTIONS = [
  { label: '30 天', value: 30 },
  { label: '90 天（默认）', value: 90 },
  { label: '180 天', value: 180 },
  { label: '365 天', value: 365 },
  { label: '永不过期', value: 0 },
];

function detectPreset(scopes: string[]): string | null {
  if (!scopes.length) return null;
  const sorted = [...scopes].sort().join(',');
  const eq = (target: string[]) => sorted === [...target].sort().join(',');
  if (eq(FULL_SCOPES)) return 'full';
  if (eq(OPERATOR_SCOPES)) return 'operator';
  if (eq(READONLY_SCOPES)) return 'readonly';
  return null;
}

function isExpired(key: ApiKey): boolean {
  return key.status !== 'revoked' && !!key.expires_at && new Date(key.expires_at).getTime() < Date.now();
}

function formatLastUsed(iso: string | null) {
  if (!iso) {
    return (
      <Tooltip title="该密钥尚未被任何 agent 调用过">
        <span>从未使用</span>
      </Tooltip>
    );
  }
  const diffMs = Date.now() - new Date(iso).getTime();
  const days = diffMs / 86400000;
  if (days > 30) return <span>{new Date(iso).toLocaleDateString('zh-CN')}</span>;
  if (days >= 1) return <span>{Math.floor(days)} 天前</span>;
  const minutes = Math.floor(diffMs / 60000);
  if (minutes >= 60) return <span>{Math.floor(minutes / 60)} 小时前</span>;
  if (minutes >= 1) return <span>{minutes} 分钟前</span>;
  return <span>刚刚</span>;
}

function formatDateTime(iso: string) {
  return new Date(iso).toLocaleString('zh-CN', { hour12: false });
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const textarea = document.createElement('textarea');
      textarea.value = text;
      textarea.style.position = 'fixed';
      textarea.style.opacity = '0';
      document.body.appendChild(textarea);
      textarea.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(textarea);
      return ok;
    } catch {
      return false;
    }
  }
}

function CopyButton({ text, label = '复制' }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    const ok = await copyText(text);
    if (ok) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } else {
      message.error('复制失败，请手动选择文本复制');
    }
  };
  return (
    <Button size="small" icon={<CopyOutlined />} onClick={() => { void handleCopy(); }}>
      {copied ? '已复制' : label}
    </Button>
  );
}

// 一次性展示面板：SS-12 §4.7.3 —— 红色警示条先于成功态文案；片段用前缀占位，
// 「粘贴密钥」输入框由用户自行粘贴，UI 不自动回填全文。
function OneTimeKeyPanel({ title, bareKey, prefix }: { title: string; bareKey: string; prefix: string }) {
  const [pasted, setPasted] = useState('');
  const shown = pasted.trim() ? pasted.trim() : `${prefix}…`;
  const snippet = [
    'from cds_sdk import CDSClient',
    '',
    'client = CDSClient(',
    '    base_url="http://<your-host>/api/v1",',
    `    api_key="${shown}",`,
    ')',
  ].join('\n');

  return (
    <div>
      <Alert
        type="error"
        showIcon
        message="该密钥仅显示一次，请立即保存；平台无法找回"
        style={{ marginBottom: 12 }}
      />
      <Text strong style={{ display: 'block', marginBottom: 8 }}>{title}</Text>
      <Space.Compact style={{ width: '100%', marginBottom: 16 }}>
        <Input value={bareKey} readOnly style={{ fontFamily: 'monospace' }} />
        <CopyButton text={bareKey} />
      </Space.Compact>
      <Space direction="vertical" size={8} style={{ width: '100%' }}>
        <Text strong>SDK 接入片段</Text>
        <pre
          style={{
            background: '#f5f5f5',
            padding: 12,
            borderRadius: 6,
            overflowX: 'auto',
            fontSize: 12,
            margin: 0,
          }}
        >
          {snippet}
        </pre>
        <Space wrap>
          <Input
            placeholder="粘贴密钥（用于生成完整片段）"
            style={{ width: 360, fontFamily: 'monospace' }}
            value={pasted}
            onChange={(event) => setPasted(event.target.value)}
          />
          <CopyButton text={snippet} label="复制片段" />
        </Space>
        <Text type="secondary" style={{ fontSize: 12 }}>
          片段默认以 {prefix}… 占位；在输入框粘贴密钥后复制片段即可得到完整可运行代码（UI 不会自动回填密钥全文）。
        </Text>
      </Space>
    </div>
  );
}

function ApiKeyCreateModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (created: ApiKeyCreated) => void;
}) {
  const [form] = Form.useForm();
  const [preset, setPreset] = useState<string | undefined>(undefined);
  const [customScopes, setCustomScopes] = useState<string[]>([]);
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);

  const mutation = useMutation({
    mutationFn: apiKeysApi.create,
    onSuccess: (data) => setCreated(data),
    onError: () => message.error('创建失败，请重试'),
  });

  const toggleScope = (scope: string, checked: boolean) => {
    setCustomScopes((prev) => (checked ? [...prev, scope] : prev.filter((s) => s !== scope)));
  };

  const handleSubmit = () => {
    void form.validateFields().then((values: {
      name: string;
      description?: string;
      preset?: string;
      expires_in_days?: number;
      rate_limit?: number;
    }) => {
      const payload: ApiKeyCreatePayload = {
        name: values.name.trim(),
        description: values.description?.trim() || null,
        preset: values.preset as ApiKeyCreatePayload['preset'],
        expires_in_days: values.expires_in_days ?? 90,
        rate_limit: values.rate_limit ?? 120,
      };
      if (values.preset === 'custom') {
        if (!customScopes.length) {
          message.warning('请至少勾选一个功能权限');
          return;
        }
        payload.scopes = [...customScopes];
      }
      mutation.mutate(payload);
    });
  };

  const handleClose = () => {
    if (created) onCreated(created);
    setCreated(null);
    setPreset(undefined);
    setCustomScopes([]);
    form.resetFields();
    onClose();
  };

  return (
    <Modal
      title={created ? '密钥已创建' : '创建 API 密钥'}
      open={open}
      onCancel={handleClose}
      width={680}
      footer={
        created ? (
          <Button type="primary" onClick={handleClose}>我已保存，关闭</Button>
        ) : (
          <Space>
            <Button onClick={handleClose}>取消</Button>
            <Button type="primary" loading={mutation.isPending} onClick={handleSubmit}>创建</Button>
          </Space>
        )
      }
    >
      {created ? (
        <OneTimeKeyPanel
          title={`「${created.name}」已创建，请立即保存裸密钥`}
          bareKey={created.key}
          prefix={created.key_prefix}
        />
      ) : (
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label="名称"
            rules={[{ required: true, message: '请输入密钥名称' }, { max: 64, message: '最长 64 字符' }]}
          >
            <Input placeholder="例如：ETL 管线机器人" maxLength={64} />
          </Form.Item>
          <Form.Item name="description" label="用途说明（选填）">
            <Input.TextArea
              rows={2}
              placeholder="哪个 agent、做什么？例如：nightly ETL agent 拉取审计日志"
            />
          </Form.Item>
          <Form.Item
            name="preset"
            label="权限预设"
            rules={[{ required: true, message: '请选择权限预设' }]}
          >
            <Radio.Group
              onChange={(event) => setPreset(event.target.value as string)}
            >
              <Space direction="vertical" style={{ width: '100%' }}>
                <Radio value="readonly">
                  <Text strong>只读</Text>{' '}
                  <Text type="secondary">12 个功能组全部只读，适合查询类 agent</Text>
                </Radio>
                <Radio value="operator">
                  <Text strong>业务操作</Text>{' '}
                  <Text type="secondary">只读 + 会话/契约/数据/计算管线/输出治理/数据网关/Agent 面的写权限</Text>
                </Radio>
                <Radio value="full">
                  <Text strong>全量</Text>{' '}
                  <Text type="secondary">全部功能组可读可写</Text>
                </Radio>
                <Radio value="custom">
                  <Text strong>自定义</Text>{' '}
                  <Text type="secondary">按功能组 × 读/写自由勾选</Text>
                </Radio>
              </Space>
            </Radio.Group>
          </Form.Item>
          {preset === 'full' && (
            <Alert
              type="warning"
              showIcon
              message="全量访问：可调用除用户/密钥管理外的全部平台功能"
              style={{ marginBottom: 16 }}
            />
          )}
          {preset === 'custom' && (
            <div style={{ marginBottom: 16, border: '1px solid #f0f0f0', borderRadius: 6, padding: '0 12px' }}>
              {SCOPE_GROUPS.map((group) => (
                <div
                  key={group.name}
                  style={{ display: 'flex', alignItems: 'center', gap: 24, padding: '8px 0', borderBottom: '1px solid #f5f5f5' }}
                >
                  <span style={{ width: 170, flexShrink: 0 }}>
                    {group.label}{' '}
                    <Text type="secondary" style={{ fontSize: 12 }}>{group.name}</Text>
                  </span>
                  <Checkbox
                    checked={customScopes.includes(`${group.name}:read`)}
                    onChange={(event) => toggleScope(`${group.name}:read`, event.target.checked)}
                  >
                    读
                  </Checkbox>
                  <Checkbox
                    checked={customScopes.includes(`${group.name}:write`)}
                    onChange={(event) => toggleScope(`${group.name}:write`, event.target.checked)}
                  >
                    写
                  </Checkbox>
                </div>
              ))}
            </div>
          )}
          <Form.Item name="expires_in_days" label="有效期" initialValue={90}>
            <Select options={EXPIRY_OPTIONS} />
          </Form.Item>
          <Form.Item
            name="rate_limit"
            label="限流（req/min）"
            initialValue={120}
            rules={[{ required: true, message: '请输入限流值' }]}
          >
            <InputNumber min={1} max={1000} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      )}
    </Modal>
  );
}

type ActivityRow = ApiKeyActivity & { _k: number };

function ActivityDrawer({ apiKey, onClose }: { apiKey: ApiKey; onClose: () => void }) {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['api-keys', apiKey.id, 'activity'],
    queryFn: () => apiKeysApi.activity(apiKey.id),
  });

  const columns: ColumnsType<ActivityRow> = [
    { title: '时间', dataIndex: 'time', width: 170, render: (v: string) => formatDateTime(v) },
    { title: '路径', dataIndex: 'path', ellipsis: true, render: (v: string | null) => v ?? '—' },
    { title: '方法', dataIndex: 'method', width: 80, render: (v: string | null) => v ?? '—' },
    {
      title: '状态码',
      dataIndex: 'status_code',
      width: 80,
      render: (v: number | null) => (v == null ? '—' : <Tag color={v >= 400 ? 'red' : 'green'}>{v}</Tag>),
    },
    { title: 'IP', dataIndex: 'ip', width: 140, render: (v: string | null) => v ?? '—' },
    { title: '拒绝原因', dataIndex: 'denied_reason', ellipsis: true, render: (v?: string) => v ?? '—' },
  ];

  const rows: ActivityRow[] = (data ?? []).map((row, index) => ({ ...row, _k: index }));

  return (
    <Drawer title={`活动：${apiKey.name}`} open onClose={onClose} width={760}>
      {isError ? (
        <QueryErrorAlert error={error} message="活动加载失败" onRetry={() => { void refetch(); }} />
      ) : (
        <Table<ActivityRow>
          rowKey="_k"
          columns={columns}
          dataSource={rows}
          loading={isLoading}
          pagination={false}
          size="small"
          locale={tableEmpty('该密钥尚未被使用')}
          onRow={(record) =>
            record.status_code === 401 || record.status_code === 403
              ? { style: { background: '#fff1f0' } }
              : {}
          }
        />
      )}
    </Drawer>
  );
}

export default function ApiKeys() {
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [activityKey, setActivityKey] = useState<ApiKey | null>(null);
  const [rotateResult, setRotateResult] = useState<{ key: string; name: string } | null>(null);

  const { data: keys, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['api-keys'],
    queryFn: apiKeysApi.list,
  });

  const invalidate = () => { void queryClient.invalidateQueries({ queryKey: ['api-keys'] }); };

  const statusMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: 'active' | 'disabled' }) =>
      apiKeysApi.patchStatus(id, status),
    onSuccess: () => {
      message.success('状态已更新');
      invalidate();
    },
    onError: () => message.error('状态更新失败'),
  });

  const rotateMutation = useMutation({
    mutationFn: (id: string) => apiKeysApi.rotate(id),
    onSuccess: (data, id) => {
      const name = keys?.find((k) => k.id === id)?.name ?? '该密钥';
      setRotateResult({ key: data.key, name });
      invalidate();
    },
    onError: () => message.error('轮转失败'),
  });

  const revokeMutation = useMutation({
    mutationFn: (id: string) => apiKeysApi.revoke(id),
    onSuccess: () => {
      message.success('密钥已吊销');
      invalidate();
    },
    onError: () => message.error('吊销失败'),
  });

  const confirmDisable = (record: ApiKey) => {
    Modal.confirm({
      title: '确认禁用 API 密钥',
      okText: '确认禁用',
      content: `禁用「${record.name}」后，使用该密钥的调用将立即收到 401。禁用可随时恢复。`,
      onOk: () => { statusMutation.mutate({ id: record.id, status: 'disabled' }); },
    });
  };

  const confirmEnable = (record: ApiKey) => {
    Modal.confirm({
      title: '确认启用 API 密钥',
      okText: '确认启用',
      content: `启用「${record.name}」后，该密钥将恢复调用能力。`,
      onOk: () => { statusMutation.mutate({ id: record.id, status: 'active' }); },
    });
  };

  const confirmRotate = (record: ApiKey) => {
    Modal.confirm({
      title: '确认轮转 API 密钥',
      okText: '确认轮转',
      content: `轮转「${record.name}」后，旧密钥立即失效，请确保 agent 已切换到新密钥。`,
      onOk: () => { rotateMutation.mutate(record.id); },
    });
  };

  const confirmRevoke = (record: ApiKey) => {
    Modal.confirm({
      title: '确认吊销 API 密钥',
      okText: '确认吊销',
      okButtonProps: { danger: true },
      content: `吊销「${record.name}」后，使用该密钥的 agent 将立即收到 401，且不可恢复。`,
      onOk: () => { revokeMutation.mutate(record.id); },
    });
  };

  const renderScopes = (scopes: string[]) => {
    const preset = detectPreset(scopes);
    if (preset) {
      const color = preset === 'full' ? 'orange' : preset === 'operator' ? 'blue' : 'cyan';
      return <Tag color={color}>{PRESET_LABELS[preset]}</Tag>;
    }
    const shown = scopes.slice(0, MAX_SCOPE_BADGES);
    const rest = scopes.length - shown.length;
    return (
      <Space size={[0, 4]} wrap>
        {shown.map((scope) => (
          <Tag key={scope}>{scope}</Tag>
        ))}
        {rest > 0 && <Tag>+{rest}</Tag>}
      </Space>
    );
  };

  const renderStatus = (record: ApiKey) => {
    const map: Record<string, [string, string]> = {
      active: ['生效', 'green'],
      disabled: ['已禁用', 'default'],
      revoked: ['已吊销', 'red'],
    };
    const [label, color] = isExpired(record) ? ['已过期', 'orange'] : (map[record.status] ?? [record.status, 'default']);
    return (
      <Space size={4} wrap>
        <Tag color={color}>{label}</Tag>
        {typeof record.cooldown_remaining_minutes === 'number' && record.cooldown_remaining_minutes > 0 && (
          <Tooltip title="连续认证失败进入冷却（进程内计数，多实例部署为近似值）">
            <Text type="warning" style={{ fontSize: 12 }}>
              认证失败冷却中（约 {record.cooldown_remaining_minutes} 分钟后恢复）
            </Text>
          </Tooltip>
        )}
      </Space>
    );
  };

  const columns: ColumnsType<ApiKey> = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 200,
      render: (name: string, record) => (
        <Space direction="vertical" size={0}>
          <Text strong>{name}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {record.description ? record.description : `签发人：${record.issued_by_username}`}
          </Text>
        </Space>
      ),
    },
    {
      title: '前缀',
      dataIndex: 'key_prefix',
      key: 'key_prefix',
      width: 140,
      render: (v: string) => <Text code>{v}…</Text>,
    },
    {
      title: '权限',
      dataIndex: 'scopes',
      key: 'scopes',
      render: (scopes: string[]) => renderScopes(scopes),
    },
    {
      title: '状态',
      key: 'status',
      width: 200,
      render: (_, record) => renderStatus(record),
    },
    {
      title: '最近使用',
      dataIndex: 'last_used_at',
      key: 'last_used_at',
      width: 130,
      render: (v: string | null) => formatLastUsed(v),
    },
    {
      title: '到期',
      dataIndex: 'expires_at',
      key: 'expires_at',
      width: 130,
      render: (v: string | null) => (v ? <span>{new Date(v).toLocaleDateString('zh-CN')}</span> : <span>永不过期</span>),
    },
    {
      title: '操作',
      key: 'actions',
      width: 240,
      render: (_, record) => {
        const canToggle = record.status === 'active' || record.status === 'disabled';
        return (
          <Space size={4} wrap>
            {canToggle && record.status === 'active' && (
              <Button size="small" onClick={() => confirmDisable(record)}>禁用</Button>
            )}
            {canToggle && record.status === 'disabled' && (
              <Button size="small" onClick={() => confirmEnable(record)}>启用</Button>
            )}
            {canToggle && <Button size="small" onClick={() => confirmRotate(record)}>轮转</Button>}
            {canToggle && (
              <Button size="small" danger onClick={() => confirmRevoke(record)}>吊销</Button>
            )}
            <Button size="small" onClick={() => setActivityKey(record)}>活动</Button>
          </Space>
        );
      },
    },
  ];

  const empty = (keys ?? []).length === 0;

  return (
    <div>
      <Space style={{ justifyContent: 'space-between', width: '100%', marginBottom: 8 }}>
        <div>
          <Title level={4} style={{ margin: 0 }}>API 密钥</Title>
          <Text type="secondary">
            面向外部 agent 智能体的平台机器凭据（cds_ak_ 前缀）；裸密钥仅在创建/轮转时显示一次。
          </Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => { void refetch(); }}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>创建 API 密钥</Button>
        </Space>
      </Space>

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="防暴力冷却为进程内计数（同一密钥连续 5 次认证失败进入 15 分钟冷却）；多实例部署下为近似值。"
      />

      {isError ? (
        <QueryErrorAlert error={error} message="API 密钥列表加载失败" onRetry={() => { void refetch(); }} />
      ) : empty && !isLoading ? (
        <Card size="small">
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <div>
                <p style={{ margin: 0, fontWeight: 500 }}>什么是 API 密钥？</p>
                <p style={{ margin: '8px 0 0' }}>
                  API 密钥（<Text code>cds_ak_…</Text>）是面向外部 agent 智能体的平台机器凭据：以标准
                  Bearer 方式调用平台 API，按功能权限域（scope）受控访问，每次调用全程审计归因。
                </p>
              </div>
            }
          >
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
              创建第一把密钥
            </Button>
          </Empty>
        </Card>
      ) : (
        <Card size="small">
          <Table<ApiKey>
            rowKey="id"
            columns={columns}
            dataSource={keys ?? []}
            loading={isLoading}
            pagination={false}
            size="small"
            locale={tableEmpty('没有 API 密钥')}
          />
        </Card>
      )}

      <ApiKeyCreateModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={() => invalidate()}
      />

      {activityKey && <ActivityDrawer key={activityKey.id} apiKey={activityKey} onClose={() => setActivityKey(null)} />}

      <Modal
        title="密钥已轮转"
        open={!!rotateResult}
        footer={
          <Button onClick={() => setRotateResult(null)}>关闭</Button>
        }
        width={680}
        onCancel={() => setRotateResult(null)}
      >
        {rotateResult && (
          <OneTimeKeyPanel
            title={`「${rotateResult.name}」的新密钥（旧密钥已吊销），请立即保存`}
            bareKey={rotateResult.key}
            prefix={rotateResult.key.slice(0, 11)}
          />
        )}
      </Modal>
    </div>
  );
}
