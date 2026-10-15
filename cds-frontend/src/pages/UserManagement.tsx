import { useState } from 'react';
import { Alert, Button, Card, Input, Select, Space, Table, Tag, Typography, message } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import { usersApi, type UserOption } from '../services/usersApi';
import { ROLE_LABELS } from '../utils/roles';
import type { UserRole } from '../types/enums';
import { confirmHighRiskOperation } from '../utils/highRiskOperation';
import { QueryErrorAlert, tableEmpty } from '../components/Feedback/QueryFeedback';

const { Title, Text } = Typography;

const ROLE_TAG_COLORS: Record<UserRole, string> = {
  data_provider: 'blue',
  buyer: 'cyan',
  operator: 'purple',
  regulator: 'gold',
  admin: 'red',
};

const ALL_ROLE_OPTIONS = (Object.entries(ROLE_LABELS) as [UserRole, string][]).map(
  ([role, label]) => ({ label, value: role }),
);

export default function UserManagement() {
  const queryClient = useQueryClient();
  const [roleFilter, setRoleFilter] = useState<string | undefined>();
  const [keyword, setKeyword] = useState<string | undefined>();

  const { data: users, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['users-options', roleFilter, keyword],
    queryFn: () => usersApi.options({ role: roleFilter, q: keyword, limit: 100 }),
  });

  const changeRoleMutation = useMutation({
    mutationFn: (payload: { userId: string; role: string; reason: string; ticket_id?: string | null }) =>
      usersApi.changeRole(payload.userId, {
        role: payload.role,
        reason: payload.reason,
        ticket_id: payload.ticket_id,
      }),
    onSuccess: () => {
      message.success('角色已变更');
      void queryClient.invalidateQueries({ queryKey: ['users-options'] });
    },
    onError: () => message.error('角色变更失败'),
  });

  const handleRoleChange = (record: UserOption, newRole: string) => {
    const oldLabel = ROLE_LABELS[record.role as UserRole] ?? record.role;
    const newLabel = ROLE_LABELS[newRole as UserRole] ?? newRole;
    confirmHighRiskOperation({
      title: '确认变更用户角色',
      content: `将 ${record.username} 的角色由「${oldLabel}」变更为「${newLabel}」。操作将以理由与工单号记入审计。`,
      okText: '确认变更',
      onConfirm: (payload) => changeRoleMutation.mutate({ userId: record.id, role: newRole, ...payload }),
    });
  };

  const columns: ColumnsType<UserOption> = [
    { title: '用户名', dataIndex: 'username', key: 'username', width: 200 },
    {
      title: '组织',
      dataIndex: 'organization',
      key: 'organization',
      ellipsis: true,
      render: (v: string | null) => v || '—',
    },
    {
      title: '当前角色',
      dataIndex: 'role',
      key: 'role',
      width: 140,
      render: (v: string) => (
        <Tag color={ROLE_TAG_COLORS[v as UserRole] ?? 'default'}>
          {ROLE_LABELS[v as UserRole] ?? v}
        </Tag>
      ),
    },
    {
      title: '变更角色',
      key: 'role_action',
      width: 200,
      render: (_, record) => (
        <Select
          size="small"
          style={{ width: 180 }}
          placeholder={`当前: ${ROLE_LABELS[record.role as UserRole] ?? record.role}`}
          options={ALL_ROLE_OPTIONS.filter((option) => option.value !== record.role)}
          onChange={(value) => handleRoleChange(record, value)}
        />
      ),
    },
  ];

  return (
    <div>
      <Space direction="vertical" size={4} style={{ width: '100%', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>用户管理</Title>
        <Text type="secondary">
          角色变更走 reason + ticket_id 高危留痕（P-GAP-014 纪律）；注册自选角色已收紧，新角色由管理员指派。
        </Text>
      </Space>

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="列表仅包含活跃用户（上限 100 条，来自 /users/options）。"
      />

      <Space style={{ marginBottom: 16 }} wrap>
        <Input.Search
          placeholder="搜索用户名 / 组织"
          allowClear
          style={{ width: 260 }}
          onSearch={(value) => setKeyword(value.trim() || undefined)}
          onChange={(event) => {
            if (!event.target.value) setKeyword(undefined);
          }}
        />
        <Select
          placeholder="按角色筛选"
          allowClear
          style={{ width: 160 }}
          value={roleFilter}
          onChange={(value) => setRoleFilter(value)}
          options={ALL_ROLE_OPTIONS}
        />
        <Button icon={<ReloadOutlined />} onClick={() => { void refetch(); }}>刷新</Button>
      </Space>

      <Card size="small">
        {isError ? (
          <QueryErrorAlert error={error} message="用户列表加载失败" onRetry={() => { void refetch(); }} />
        ) : (
          <Table<UserOption>
            rowKey="id"
            columns={columns}
            dataSource={users ?? []}
            loading={isLoading}
            pagination={false}
            size="small"
            locale={tableEmpty('没有匹配的用户')}
          />
        )}
      </Card>
    </div>
  );
}
