import api from './api';

// 数据管线任务 — 后端 app/api/data_pipeline.py（/api/v1/data-pipeline）。
// 列表/详情只返回当前用户的任务（后端按 user_id 过滤）。

export interface PipelineTaskSummary {
  task_id: string;
  task_type: string;
  status: string;
  input_path: string;
  output_path: string | null;
  duration_ms: number | null;
  created_at: string;
}

export interface PipelineTaskDetail extends PipelineTaskSummary {
  result: Record<string, unknown> | null;
  error: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export const dataPipelineApi = {
  listTasks: (params?: {
    page?: number;
    page_size?: number;
    status?: string;
  }): Promise<{ items: PipelineTaskSummary[]; total: number }> =>
    api.get('/data-pipeline/tasks', {
      params: {
        skip: ((params?.page ?? 1) - 1) * (params?.page_size ?? 20),
        limit: params?.page_size ?? 20,
        status: params?.status,
      },
    }),
  getTask: (taskId: string): Promise<PipelineTaskDetail> =>
    api.get(`/data-pipeline/tasks/${taskId}`),
};
