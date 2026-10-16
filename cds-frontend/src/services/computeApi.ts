import { useMutation, useQueryClient } from '@tanstack/react-query';
import api from './api';

// 生产运行是同步执行（后端在请求内跑完整个沙箱生命周期），真实 MicroVM 冷启动
// 可达数分钟，因此这里必须覆盖共享实例的 30s 默认超时。
const RUN_TIMEOUT_MS = 900_000;

export interface ComputeRunOverrides {
  workload_id?: string | null;
  params?: Record<string, unknown> | null;
  resource_override?: Record<string, number> | null;
}

export interface ComputeResourceUsage {
  exit_code?: number | null;
  output_bytes?: number | null;
  output_blocked?: boolean | null;
  manifest_error?: string | null;
}

export interface ComputeRunView {
  id: string;
  contract_id: string;
  workload_id: string | null;
  trigger: string | null;
  status: string;
  scheduled_for: string | null;
  status_updated_at?: string | null;
  started_at: string | null;
  finished_at: string | null;
  output_rows: number | null;
  output_files: { name: string; size_bytes?: number }[] | string[] | null;
  error_message: string | null;
  session_id: string | null;
  resource_usage: ComputeResourceUsage | null;
  params: Record<string, unknown> | null;
  resource_override: Record<string, number> | null;
}

export const computeApi = {
  triggerRun: (contractId: string, overrides: ComputeRunOverrides = {}): Promise<ComputeRunView> =>
    api.post(
      '/compute/runs',
      {
        contract_id: contractId,
        workload_id: overrides.workload_id ?? null,
        params: overrides.params ?? null,
        resource_override: overrides.resource_override ?? null,
      },
      { timeout: RUN_TIMEOUT_MS },
    ) as unknown as Promise<ComputeRunView>,
};

export function useTriggerComputeRunDetailed() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { contractId: string } & ComputeRunOverrides) => computeApi.triggerRun(input.contractId, input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['flow', 'compute'] });
    },
  });
}
