import { create } from 'zustand';

/**
 * 页内钻取面包屑栈（平台通用层的动态半边）。
 * 静态半边在 `components/Layout/routeCrumb.ts`（路由注册表）；两者拼合由 PageCrumb 完成。
 * 钻取视图（如交付中心的新建API/接口监控）通过 `useCrumbSegments` 写入，卸载自动清空。
 */
interface CrumbStackState {
  stack: string[];
  setStack: (stack: string[]) => void;
}

export const useCrumbStackStore = create<CrumbStackState>((set) => ({
  stack: [],
  setStack: (stack) => set({ stack }),
}));
