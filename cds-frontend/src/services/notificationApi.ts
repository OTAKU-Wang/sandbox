import api from './api';
import { useQuery } from '@tanstack/react-query';

// 聚合通知中心（Task 12）唯一数据源：后端契约 = app/api/notifications.py，
// 挂在 /monitoring/notifications。items 有两种 source：
// 'stored' = UserNotification 行（可标已读）；'live' = 实时待办合成项，
// id 是 `contract:<uuid>` / `task:<uuid>` 前缀形态，对其标已读后端必 404，
// 所以 UI 只给 stored 项渲染「标为已读」。

export type NotificationCategory = 'todo' | 'system' | 'business' | (string & {});

export interface NotificationItem {
  id: string;
  category: NotificationCategory;
  title: string;
  content: string | null;
  target_path: string | null;
  is_read: boolean;
  extra_meta: Record<string, unknown>;
  source: 'stored' | 'live' | (string & {});
  created_at: string | null;
}

export interface NotificationListResult {
  items: NotificationItem[];
  total: number;
  unread_count: number;
  todo_count: number;
}

export interface NotificationCreatePayload {
  category?: NotificationCategory;
  title: string;
  content?: string;
  target_path?: string;
}

export const CATEGORY_LABELS: Record<string, string> = {
  todo: '待办',
  system: '系统',
  business: '业务',
};

export const notificationApi = {
  list: (): Promise<NotificationListResult> =>
    api.get('/monitoring/notifications') as unknown as Promise<NotificationListResult>,

  create: (payload: NotificationCreatePayload): Promise<NotificationItem> =>
    api.post('/monitoring/notifications', payload) as unknown as Promise<NotificationItem>,

  markRead: (id: string): Promise<NotificationItem> =>
    api.post(`/monitoring/notifications/${id}/read`) as unknown as Promise<NotificationItem>,
};

export function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category] ?? category;
}

/** 顶栏徒计与抽屉共用的聚合查询：基线节奏由 refetchInterval 持有，打开抽屉时调用方 invalidate 补新。 */
export const NOTIFICATIONS_QUERY_KEY = ['notifications', 'center'] as const;

export function useNotificationSummary() {
  return useQuery({
    queryKey: NOTIFICATIONS_QUERY_KEY,
    queryFn: () => notificationApi.list(),
    refetchInterval: 120_000,
  });
}
