import { create } from 'zustand';

interface UIState {
  sidebarCollapsed: boolean;
  theme: 'light' | 'dark';
  /** 通知中心分类显隐偏好（Task 12 个性化）：key = 后端 category 值。 */
  notificationCategories: Record<string, boolean>;
  toggleSidebar: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  setTheme: (theme: 'light' | 'dark') => void;
  setNotificationCategoryVisible: (category: string, visible: boolean) => void;
}

export const useUIStore = create<UIState>()((set) => ({
  sidebarCollapsed: false,
  theme: 'light',
  notificationCategories: { todo: true, system: true, business: true },
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
  setTheme: (theme) => set({ theme }),
  setNotificationCategoryVisible: (category, visible) =>
    set((s) => ({ notificationCategories: { ...s.notificationCategories, [category]: visible } })),
}));
