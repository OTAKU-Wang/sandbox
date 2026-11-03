import { useMemo } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Badge, Button, Drawer, Empty, List, Segmented, Space, Switch, Tag, Typography, message } from 'antd';
import { useNavigate } from 'react-router-dom';
import {
  categoryLabel,
  NOTIFICATIONS_QUERY_KEY,
  notificationApi,
  useNotificationSummary,
  type NotificationItem,
} from '../../services/notificationApi';
import { useUIStore } from '../../stores/uiStore';
import { getErrorMessage } from '../Feedback/errorMessage';
import { QueryErrorAlert } from '../Feedback/QueryFeedback';
import { useState } from 'react';

const { Text } = Typography;

// uiStore 被测试 mock 时 fake state 可能没有该键（MainLayout.a253 先例），缺失按全选处理。
const EMPTY_CATEGORIES: Record<string, boolean> = {};

const FILTERS = [
  { value: 'all', label: '全部' },
  { value: 'todo', label: '待办' },
  { value: 'system', label: '系统' },
  { value: 'business', label: '业务' },
];

export default function NotificationDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState('all');
  const categories = useUIStore((s) => s.notificationCategories ?? EMPTY_CATEGORIES);
  const setCategoryVisible = useUIStore((s) => s.setNotificationCategoryVisible);
  const notifications = useNotificationSummary();

  const markRead = useMutation({
    mutationFn: (id: string) => notificationApi.markRead(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: NOTIFICATIONS_QUERY_KEY });
    },
    onError: (err) => message.error(getErrorMessage(err, '标记已读失败')),
  });

  const data = notifications.data;
  const items = useMemo(() => {
    const all = data?.items ?? [];
    return all.filter(
      (item) =>
        categories[item.category] !== false &&
        (filter === 'all' || (filter === 'todo' ? item.category === 'todo' : item.category === filter)),
    );
  }, [data, categories, filter]);

  const renderItem = (item: NotificationItem) => (
    <List.Item
      actions={
        item.source === 'stored' && !item.is_read
          ? [
              <Button key="read" size="small" type="link" loading={markRead.isPending} onClick={() => markRead.mutate(item.id)}>
                标为已读
              </Button>,
            ]
          : []
      }
    >
      <List.Item.Meta
        title={
          <Space>
            <Badge status={item.is_read ? 'default' : 'processing'} />
            <Text strong={!item.is_read}>{item.title}</Text>
            <Tag>{categoryLabel(item.category)}</Tag>
            {item.source === 'live' && <Tag color="blue">实时</Tag>}
          </Space>
        }
        description={
          <Space direction="vertical" size={0}>
            {item.content && <Text type="secondary">{item.content}</Text>}
            {item.created_at && <Text type="secondary">{String(item.created_at).slice(0, 16).replace('T', ' ')}</Text>}
          </Space>
        }
      />
      {item.target_path && (
        <Button size="small" onClick={() => { onClose(); navigate(item.target_path as string); }}>
          前往处理
        </Button>
      )}
    </List.Item>
  );

  return (
    <Drawer
      title={`通知中心${data && data.unread_count > 0 ? `（${data.unread_count} 未读 · ${data.todo_count} 待办）` : ''}`}
      width={520}
      open={open}
      onClose={onClose}
      extra={
        <Space size="large">
          {['todo', 'system', 'business'].map((cat) => (
            <Space key={cat} size={4}>
              <Switch size="small" checked={categories[cat] !== false} onChange={(v) => setCategoryVisible(cat, v)} />
              <Text type="secondary">{categoryLabel(cat)}</Text>
            </Space>
          ))}
        </Space>
      }
    >
      {notifications.isError && <QueryErrorAlert error={notifications.error} />}
      <Segmented options={FILTERS} value={filter} onChange={(v) => setFilter(String(v))} style={{ marginBottom: 12 }} />
      <List
        loading={notifications.isFetching}
        dataSource={items}
        rowKey={(item) => item.id}
        renderItem={renderItem}
        locale={{ emptyText: <Empty description="暂无通知" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
      />
    </Drawer>
  );
}
