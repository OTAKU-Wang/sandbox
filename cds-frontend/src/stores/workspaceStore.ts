import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { WORKSPACE_STORAGE_KEY, type Workspace } from '../utils/workspace';

interface WorkspaceState {
  workspace: Workspace | null;
  setWorkspace: (workspace: Workspace) => void;
  reset: () => void;
}

export const useWorkspaceStore = create<WorkspaceState>()(
  persist(
    (set) => ({
      workspace: null,
      setWorkspace: (workspace) => set({ workspace }),
      // 工作台选择只属于当前这次登录：登出必须归零，否则下一个登录这个浏览器的
      // 账号会落在上一个会话的工作台上（运营切到开发者后重新登录仍停在开发者）。
      reset: () => set({ workspace: null }),
    }),
    { name: WORKSPACE_STORAGE_KEY },
  ),
);
