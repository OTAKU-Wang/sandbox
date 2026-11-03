import api from './api';
import type { PreferredLanguage, ProjectEnvironmentType } from './scenarioApi';

// 建模项目中心（Task 6）唯一数据源：后端契约 = app/api/projects.py（/api/v1/flow/projects）。
// 语言与环境枚举复用 scenarioApi 已导出的那一份：项目可由场景承接而来，两处必须是同一集合，
// 各自写一份字面量就会在下次加语言时漂开。

export type ProjectStatus = 'active' | 'submitted_review' | 'approved' | 'archived' | (string & {});

export type DagNodeLanguage = 'python' | 'sql' | 'r' | 'pyspark';

export type MilestoneStatus = 'pending' | 'in_progress' | 'done';

/** 多方协同名册条目。owner 由服务端按认证身份注入，前端只登记机构方与合作角色。 */
export interface ProjectParticipant {
  user_id?: string | null;
  username?: string | null;
  org_name?: string | null;
  role?: string;
  added_at?: string;
}

export interface ProjectMilestone {
  title: string;
  due_date?: string | null;
  status?: MilestoneStatus;
}

export interface DagNode {
  id: string;
  label: string;
  type: string;
  /** 后端保存时缺省继承项目首选语言，因此回读总能看到确定值。 */
  language?: DagNodeLanguage | string;
  notebook_id?: string | null;
  workload_id?: string | null;
}

export interface DagEdge {
  source: string;
  target: string;
}

export interface DagDefinition {
  version?: number;
  language?: string;
  nodes: DagNode[];
  edges: DagEdge[];
  saved_at?: string | null;
  /** 场景承接注入的合规约束与交付预埋，保存 DAG 时由后端原样保留。 */
  scenario_context?: Record<string, unknown>;
}

export interface LinkedNotebook {
  id: string;
  title: string;
  language: string;
  status: string;
  task_no: string | null;
  updated_at: string | null;
}

export interface LinkedWorkload {
  id: string;
  name: string;
  language: string;
  env: string;
  status: string;
  updated_at: string | null;
}

export interface ModelingProject {
  id: string;
  name: string;
  description: string | null;
  team_id: string | null;
  scenario_id: string | null;
  contract_id: string | null;
  environment_type: ProjectEnvironmentType | string;
  preferred_language: string;
  status: ProjectStatus | string;
  participants: ProjectParticipant[];
  milestones: ProjectMilestone[];
  dag_definition: DagDefinition;
  linked_notebook_ids: string[];
  linked_workload_ids: string[];
  created_by: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ProjectDetail extends ModelingProject {
  notebooks: LinkedNotebook[];
  workloads: LinkedWorkload[];
  /** linked_* 是无外键的引用清单：行已不存在时后端如实列出，不静默少一条。 */
  unresolved_notebook_ids: string[];
  unresolved_workload_ids: string[];
}

export interface ProjectListResult {
  items: ModelingProject[];
  total: number;
  page: number;
  page_size: number;
}

export interface ProjectListParams {
  status?: ProjectStatus;
  preferred_language?: DagNodeLanguage;
  keyword?: string;
  page?: number;
  page_size?: number;
}

export interface ProjectCreatePayload {
  name: string;
  description?: string;
  scenario_id?: string;
  contract_id?: string;
  team_id?: string;
  environment_type: ProjectEnvironmentType;
  preferred_language: PreferredLanguage;
  participants: { org_name?: string; username?: string; role?: string }[];
  milestones: { title: string; due_date?: string; status?: MilestoneStatus }[];
}

export interface DagSavePayload {
  nodes: DagNode[];
  edges: DagEdge[];
}

export interface DagValidation {
  valid: boolean;
  execution_order: string[];
  cyclic_nodes: string[];
  errors: string[];
  node_count: number;
  edge_count: number;
}

export interface SubmitReviewResult {
  id: string;
  status: string;
  name: string;
}

export const PROJECT_STATUS_LABELS: Record<string, string> = {
  active: '进行中',
  submitted_review: '已提交审核',
  approved: '已批准',
  archived: '已归档',
};

export const PROJECT_STATUS_COLORS: Record<string, string> = {
  active: 'processing',
  submitted_review: 'gold',
  approved: 'green',
  archived: 'default',
};

export const MILESTONE_STATUS_LABELS: Record<string, string> = {
  pending: '未开始',
  in_progress: '进行中',
  done: '已完成',
};

export const DAG_NODE_TYPE_OPTIONS = [
  { label: '数据接入', value: 'input' },
  { label: 'SQL 清洗', value: 'sql_clean' },
  { label: '特征工程', value: 'feature_eng' },
  { label: '模型训练', value: 'train' },
  { label: '评估检验', value: 'evaluate' },
  { label: '成果输出', value: 'output' },
];

export const DAG_NODE_LANGUAGE_OPTIONS: { label: string; value: DagNodeLanguage }[] = [
  { label: 'Python', value: 'python' },
  { label: 'SQL', value: 'sql' },
  { label: 'R', value: 'r' },
  { label: 'PySpark', value: 'pyspark' },
];

export const PROJECT_STATUS_OPTIONS: { label: string; value: ProjectStatus }[] = Object.entries(
  PROJECT_STATUS_LABELS,
).map(([value, label]) => ({ label, value: value as ProjectStatus }));

export const projectApi = {
  listProjects: (params?: ProjectListParams): Promise<ProjectListResult> =>
    api.get('/flow/projects', { params }) as unknown as Promise<ProjectListResult>,

  createProject: (payload: ProjectCreatePayload): Promise<ModelingProject> =>
    api.post('/flow/projects', payload) as unknown as Promise<ModelingProject>,

  getProject: (projectId: string): Promise<ProjectDetail> =>
    api.get(`/flow/projects/${projectId}`) as unknown as Promise<ProjectDetail>,

  saveDag: (projectId: string, payload: DagSavePayload): Promise<ModelingProject> =>
    api.put(`/flow/projects/${projectId}/dag`, payload) as unknown as Promise<ModelingProject>,

  validateDag: (projectId: string): Promise<DagValidation> =>
    api.post(`/flow/projects/${projectId}/dag/validate`, {}) as unknown as Promise<DagValidation>,

  submitReview: (projectId: string): Promise<SubmitReviewResult> =>
    api.post(`/flow/projects/${projectId}/submit-review`, {}) as unknown as Promise<SubmitReviewResult>,
};

export function projectStatusLabel(status: string): string {
  return PROJECT_STATUS_LABELS[status] ?? status;
}

export function dagNodeCount(project: ModelingProject): number {
  return project.dag_definition?.nodes?.length ?? 0;
}
