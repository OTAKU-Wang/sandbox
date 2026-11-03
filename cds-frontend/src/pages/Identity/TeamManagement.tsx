import { useState } from 'react';
import {
  Button,
  Card,
  Descriptions,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import {
  teamApi,
  type Team,
  type TeamCreatePayload,
  type TeamMember,
  type TeamPatchPayload,
  type TeamRole,
  type TeamStatus,
} from '../../services/teamApi';
import { usersApi } from '../../services/usersApi';
import { QueryErrorAlert } from '../../components/Feedback/QueryFeedback';
import { tableEmpty } from '../../components/Feedback/tableEmpty';

const { Text, Title } = Typography;

const TEAM_ROLE_LABELS: Record<TeamRole, string> = {
  team_admin: '团队管理员',
  core_developer: '核心开发者',
  data_analyst: '数据分析师',
  viewer: '只读成员',
};

const TEAM_STATUS_LABELS: Record<string, string> = {
  active: '启用',
  archived: '已归档',
  suspended: '已停用',
};

const TEAM_STATUS_COLORS: Record<string, string> = {
  active: 'green',
  archived: 'default',
  suspended: 'orange',
};

const PERMISSION_OPTIONS = ['view', 'edit', 'run', 'export', 'manage'].map((value) => ({
  label: value,
  value,
}));

const TEAM_ROLE_OPTIONS = (Object.keys(TEAM_ROLE_LABELS) as TeamRole[]).map((value) => ({
  label: TEAM_ROLE_LABELS[value],
  value,
}));

const TEAM_STATUS_VALUES: TeamStatus[] = ['active', 'archived', 'suspended'];

const TEAM_STATUS_OPTIONS = TEAM_STATUS_VALUES.map((value) => ({
  label: TEAM_STATUS_LABELS[value],
  value,
}));

const DEFAULT_QUOTAS = { cpu_quota_cores: 16, memory_quota_gb: 64, storage_quota_gb: 500 };

const memberName = (member: TeamMember) => member.username ?? `未知账号 ${member.user_id.slice(0, 8)}`;

function formatDateTime(iso: string | null) {
  if (!iso) return '—';
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString('zh-CN', { hour12: false });
}

function QuotaCell({ team }: { team: Team }) {
  return (
    <Space size={[4, 4]} wrap>
      <Tooltip title="CPU 配额（核）">
        <Tag color="geekblue">{team.cpu_quota_cores} 核</Tag>
      </Tooltip>
      <Tooltip title="内存配额（GB）">
        <Tag color="purple">{team.memory_quota_gb} GB</Tag>
      </Tooltip>
      <Tooltip title="存储配额（GB）">
        <Tag color="cyan">{team.storage_quota_gb} GB</Tag>
      </Tooltip>
    </Space>
  );
}

interface TeamFormValues {
  name: string;
  org_id?: string;
  description?: string;
  status?: TeamStatus;
  cpu_quota_cores: number;
  memory_quota_gb: number;
  storage_quota_gb: number;
}

function TeamFormModal({ team, onClose }: { team: Team | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [form] = Form.useForm<TeamFormValues>();
  const editing = team != null;

  const saveMutation = useMutation({
    mutationFn: (payload: TeamCreatePayload & TeamPatchPayload) =>
      editing ? teamApi.updateTeam(team.id, payload) : teamApi.createTeam(payload),
    onSuccess: (saved) => {
      message.success(editing ? `团队「${saved.name}」已更新` : `团队「${saved.name}」已创建`);
      queryClient.invalidateQueries({ queryKey: ['teams'] });
      queryClient.invalidateQueries({ queryKey: ['team-detail', saved.id] });
      onClose();
    },
    onError: () => message.error(editing ? '团队更新失败' : '团队创建失败'),
  });

  return (
    <Modal
      open
      title={editing ? `编辑团队 · ${team.name}` : '新建团队'}
      okText={editing ? '保存' : '创建团队'}
      confirmLoading={saveMutation.isPending}
      onCancel={onClose}
      onOk={() => {
        form
          .validateFields()
          .then((values) => saveMutation.mutate(values))
          .catch(() => undefined);
      }}
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={
          team
            ? {
                name: team.name,
                org_id: team.org_id ?? undefined,
                description: team.description ?? undefined,
                status: team.status,
                cpu_quota_cores: team.cpu_quota_cores,
                memory_quota_gb: team.memory_quota_gb,
                storage_quota_gb: team.storage_quota_gb,
              }
            : { status: 'active', ...DEFAULT_QUOTAS }
        }
      >
        <Form.Item
          name="name"
          label="团队名称"
          rules={[{ required: true, message: '请输入团队名称' }, { max: 128, message: '不超过 128 个字符' }]}
        >
          <Input placeholder="例如：金融风控创新实验室" />
        </Form.Item>
        <Form.Item name="org_id" label="所属组织" tooltip="对应数据空间内的组织标识，可留空">
          <Input placeholder="例如：org-fin-01" />
        </Form.Item>
        <Form.Item name="description" label="团队说明" rules={[{ max: 512, message: '不超过 512 个字符' }]}>
          <Input.TextArea rows={2} placeholder="团队用途、协作范围等" />
        </Form.Item>
        {editing && (
          <Form.Item name="status" label="状态">
            <Select options={TEAM_STATUS_OPTIONS} />
          </Form.Item>
        )}
        <Form.Item name="cpu_quota_cores" label="CPU 配额（核）" rules={[{ required: true, message: '请设置 CPU 配额' }]}>
          <InputNumber min={1} max={4096} style={{ width: '100%' }} />
        </Form.Item>
        <Form.Item name="memory_quota_gb" label="内存配额（GB）" rules={[{ required: true, message: '请设置内存配额' }]}>
          <InputNumber min={1} max={16384} style={{ width: '100%' }} />
        </Form.Item>
        <Form.Item name="storage_quota_gb" label="存储配额（GB）" rules={[{ required: true, message: '请设置存储配额' }]}>
          <InputNumber min={1} max={1048576} style={{ width: '100%' }} />
        </Form.Item>
      </Form>
    </Modal>
  );
}

function MembersDrawer({ teamId, onClose }: { teamId: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [form] = Form.useForm<{ user_id: string; role: TeamRole; permissions?: string[] }>();

  const detailQuery = useQuery({
    queryKey: ['team-detail', teamId],
    queryFn: () => teamApi.fetchTeam(teamId),
  });

  const rosterQuery = useQuery({
    queryKey: ['teams', 'roster'],
    queryFn: () => usersApi.options({ limit: 100 }),
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['team-detail', teamId] });
    queryClient.invalidateQueries({ queryKey: ['teams'] });
  };

  const upsertMutation = useMutation({
    mutationFn: (payload: { user_id: string; role: TeamRole; permissions?: string[] }) =>
      teamApi.addTeamMember(teamId, payload),
    onSuccess: () => {
      message.success('成员已保存');
      form.resetFields();
      invalidate();
    },
    onError: () => message.error('成员保存失败'),
  });

  const removeMutation = useMutation({
    mutationFn: (userId: string) => teamApi.removeTeamMember(teamId, userId),
    onSuccess: () => {
      message.success('成员已移出团队');
      invalidate();
    },
    onError: () => message.error('成员移除失败'),
  });

  const team = detailQuery.data;
  const memberIds = new Set((team?.members ?? []).map((member) => member.user_id));
  const rosterOptions = (rosterQuery.data ?? [])
    .filter((option) => !memberIds.has(option.id))
    .map((option) => ({
      label: `${option.username}${option.organization ? ` · ${option.organization}` : ''}`,
      value: option.id,
    }));

  const columns: ColumnsType<TeamMember> = [
    {
      title: '成员',
      dataIndex: 'user_id',
      key: 'user_id',
      render: (_: string, member) => (
        <Space direction="vertical" size={0}>
          <Text>{memberName(member)}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>
            加入于 {formatDateTime(member.added_at)}
          </Text>
        </Space>
      ),
    },
    {
      title: '团队角色',
      dataIndex: 'role',
      key: 'role',
      width: 160,
      render: (_: string, member) => (
        <Select
          size="small"
          value={member.role as TeamRole}
          style={{ width: 140 }}
          options={TEAM_ROLE_OPTIONS}
          loading={upsertMutation.isPending}
          onChange={(role: TeamRole) =>
            upsertMutation.mutate({ user_id: member.user_id, role, permissions: member.permissions })
          }
        />
      ),
    },
    {
      title: '权限',
      dataIndex: 'permissions',
      key: 'permissions',
      render: (permissions: string[]) =>
        permissions.length ? (
          <Space size={[0, 4]} wrap>
            {permissions.map((permission) => (
              <Tag key={permission}>{permission}</Tag>
            ))}
          </Space>
        ) : (
          <Text type="secondary">未授予</Text>
        ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 88,
      render: (_: unknown, member) => (
        <Popconfirm
          title={`将 ${memberName(member)} 移出团队？`}
          description="移出后其在本团队的配额使用权立即失效。"
          okText="移出"
          okButtonProps={{ danger: true }}
          onConfirm={() => removeMutation.mutate(member.user_id)}
        >
          <Button size="small" danger loading={removeMutation.isPending}>
            移出
          </Button>
        </Popconfirm>
      ),
    },
  ];

  return (
    <Drawer open width={720} title="成员与权限" onClose={onClose}>
      {detailQuery.isError && <QueryErrorAlert error={detailQuery.error} onRetry={() => detailQuery.refetch()} />}
      {team && (
        <Descriptions size="small" column={2} bordered style={{ marginBottom: 16 }}>
          <Descriptions.Item label="团队" span={2}>
            {team.name}
            <Tag color={TEAM_STATUS_COLORS[team.status] ?? 'default'} style={{ marginLeft: 8 }}>
              {TEAM_STATUS_LABELS[team.status] ?? team.status}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="所属组织">{team.org_id ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="成员数">{team.member_count}</Descriptions.Item>
          <Descriptions.Item label="资源配额" span={2}>
            <QuotaCell team={team} />
          </Descriptions.Item>
        </Descriptions>
      )}

      <Card size="small" title="添加成员" style={{ marginBottom: 16 }}>
        {rosterQuery.isError && <QueryErrorAlert error={rosterQuery.error} onRetry={() => rosterQuery.refetch()} />}
        <Form
          form={form}
          layout="vertical"
          initialValues={{ role: 'viewer' }}
          onFinish={(values) => upsertMutation.mutate(values)}
        >
          <Form.Item
            name="user_id"
            label="平台账号"
            rules={[{ required: true, message: '请选择要加入团队的账号' }]}
          >
            <Select
              showSearch
              optionFilterProp="label"
              placeholder="按用户名检索"
              loading={rosterQuery.isLoading}
              options={rosterOptions}
              notFoundContent={rosterQuery.isLoading ? '加载中…' : '没有可选账号'}
            />
          </Form.Item>
          <Form.Item name="role" label="团队角色" rules={[{ required: true, message: '请选择团队角色' }]}>
            <Select options={TEAM_ROLE_OPTIONS} />
          </Form.Item>
          <Form.Item name="permissions" label="权限点" tooltip="留空则按该角色的团队默认权限映射下发">
            <Select mode="tags" placeholder="view / edit / run …" options={PERMISSION_OPTIONS} />
          </Form.Item>
          <Button type="primary" loading={upsertMutation.isPending} onClick={() => form.submit()}>
            添加成员
          </Button>
        </Form>
      </Card>

      <Table
        rowKey="user_id"
        size="small"
        loading={detailQuery.isLoading}
        columns={columns}
        dataSource={team?.members ?? []}
        pagination={false}
        locale={tableEmpty('该团队还没有成员')}
      />
    </Drawer>
  );
}

export default function TeamManagement() {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [keyword, setKeyword] = useState<string>();
  const [status, setStatus] = useState<TeamStatus>();
  const [editor, setEditor] = useState<{ open: boolean; team: Team | null }>({ open: false, team: null });
  const [rosterTeamId, setRosterTeamId] = useState<string | null>(null);

  const teamsQuery = useQuery({
    queryKey: ['teams', page, pageSize, keyword, status],
    queryFn: () => teamApi.fetchTeams({ page, page_size: pageSize, q: keyword, status }),
  });

  const columns: ColumnsType<Team> = [
    {
      title: '团队',
      dataIndex: 'name',
      key: 'name',
      render: (_: string, team) => (
        <Space direction="vertical" size={0}>
          <Button type="link" style={{ padding: 0, height: 'auto' }} onClick={() => setRosterTeamId(team.id)}>
            {team.name}
          </Button>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {team.description ?? '—'}
          </Text>
        </Space>
      ),
    },
    {
      title: '所属组织',
      dataIndex: 'org_id',
      key: 'org_id',
      width: 160,
      render: (value: string | null) => value ?? '—',
    },
    {
      title: '资源配额',
      key: 'quota',
      width: 240,
      render: (_: unknown, team) => <QuotaCell team={team} />,
    },
    {
      title: '成员',
      dataIndex: 'member_count',
      key: 'member_count',
      width: 90,
      render: (count: number, team) => (
        <Button type="link" style={{ padding: 0, height: 'auto' }} onClick={() => setRosterTeamId(team.id)}>
          {count} 人
        </Button>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (value: string) => (
        <Tag color={TEAM_STATUS_COLORS[value] ?? 'default'}>{TEAM_STATUS_LABELS[value] ?? value}</Tag>
      ),
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 180,
      render: (value: string | null) => formatDateTime(value),
    },
    {
      title: '操作',
      key: 'actions',
      width: 170,
      render: (_: unknown, team) => (
        <Space>
          <Button size="small" onClick={() => setEditor({ open: true, team })}>
            配额调整
          </Button>
          <Button size="small" type="primary" ghost onClick={() => setRosterTeamId(team.id)}>
            成员管理
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <Card
      title={
        <Space>
          <Title level={4} style={{ margin: 0 }}>
            团队管理
          </Title>
          <Text type="secondary">团队级 CPU / 内存 / 存储配额与成员隔离</Text>
        </Space>
      }
      extra={
        <Space>
          <Input.Search
            allowClear
            placeholder="搜索团队名称 / 组织"
            style={{ width: 220 }}
            onSearch={(value) => {
              setPage(1);
              setKeyword(value.trim() || undefined);
            }}
          />
          <Select
            allowClear
            placeholder="状态"
            style={{ width: 120 }}
            options={TEAM_STATUS_OPTIONS}
            value={status}
            onChange={(value?: TeamStatus) => {
              setPage(1);
              setStatus(value);
            }}
          />
          <Button icon={<ReloadOutlined />} onClick={() => teamsQuery.refetch()}>
            刷新
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setEditor({ open: true, team: null })}>
            新建团队
          </Button>
        </Space>
      }
    >
      {teamsQuery.isError && <QueryErrorAlert error={teamsQuery.error} onRetry={() => teamsQuery.refetch()} />}
      <Table
        rowKey="id"
        loading={teamsQuery.isLoading}
        columns={columns}
        dataSource={teamsQuery.data?.items ?? []}
        locale={tableEmpty('暂无团队，点击「新建团队」创建第一个团队')}
        pagination={{
          current: page,
          pageSize,
          total: teamsQuery.data?.total ?? 0,
          showSizeChanger: true,
          showTotal: (total) => `共 ${total} 个团队`,
          onChange: (nextPage, nextSize) => {
            setPage(nextPage);
            setPageSize(nextSize);
          },
        }}
      />
      {editor.open && (
        <TeamFormModal key={editor.team?.id ?? 'new'} team={editor.team} onClose={() => setEditor({ open: false, team: null })} />
      )}
      {rosterTeamId && <MembersDrawer teamId={rosterTeamId} onClose={() => setRosterTeamId(null)} />}
    </Card>
  );
}
