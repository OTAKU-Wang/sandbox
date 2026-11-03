import api from './api';

// 场景中心（Task 5）唯一数据源：后端契约 = app/api/scenarios.py。
// 交付场景三分类（api / dataset_file / tee_compute）是库里的真值，前端不自造第四类；
// 「开发场景 / 交付场景」是同一批场景的两个视图，不是两套字段。

export type DeliveryScene = 'api' | 'dataset_file' | 'tee_compute' | (string & {});

export type ScenarioStatus = 'published' | 'accepted' | 'in_progress' | 'completed' | 'archived';

export type PreferredLanguage = 'python' | 'sql' | 'r' | 'pyspark';

export type ProjectEnvironmentType = 'l1' | 'l2' | 'l2c' | 'l3';

export interface ScenarioProjectBrief {
  id: string;
  name: string;
  status: string;
  team_id: string | null;
  scenario_id: string | null;
  environment_type: ProjectEnvironmentType | string;
  preferred_language: string;
  participants: unknown[];
  /** 承接时注入的合规约束与交付预埋（后端 dag_definition.scenario_context）。 */
  scenario_context: Record<string, unknown>;
  created_at: string | null;
  updated_at: string | null;
}

export interface Scenario {
  id: string;
  title: string;
  industry: string | null;
  business_background: string | null;
  target_description: string | null;
  delivery_scene: DeliveryScene;
  status: ScenarioStatus | string;
  suggested_resource_ids: string[];
  compliance_constraints: Record<string, unknown>;
  delivery_tech_config: Record<string, unknown>;
  accepted_team_id: string | null;
  accepted_by: string | null;
  created_by: string | null;
  created_at: string | null;
  updated_at: string | null;
  /** 已承接时随详情下发；未承接就是 null，不是空对象。 */
  project: ScenarioProjectBrief | null;
}

export interface ScenarioListResult {
  items: Scenario[];
  total: number;
  page: number;
  page_size: number;
}

export interface ScenarioListParams {
  status?: ScenarioStatus;
  delivery_scene?: DeliveryScene;
  industry?: string;
  keyword?: string;
  page?: number;
  page_size?: number;
}

export interface ScenarioCreatePayload {
  title: string;
  industry?: string;
  business_background?: string;
  target_description?: string;
  delivery_scene: DeliveryScene;
  suggested_resource_ids?: string[];
  compliance_constraints?: Record<string, unknown>;
  delivery_tech_config?: Record<string, unknown>;
}

export interface ScenarioAcceptPayload {
  project_name?: string;
  preferred_language: PreferredLanguage;
  environment_type: ProjectEnvironmentType;
  /** 多团队账号必须显式指定；单团队账号可省略（后端按成员关系归属）。 */
  team_id?: string;
}

export interface ScenarioAcceptResult {
  scenario: Scenario;
  project: ScenarioProjectBrief;
}

export const DELIVERY_SCENE_LABELS: Record<string, string> = {
  api: 'API 接口交付',
  dataset_file: '数据集文件交付',
  tee_compute: 'TEE 密态交付',
};

export const SCENARIO_STATUS_LABELS: Record<string, string> = {
  published: '已发布待承接',
  accepted: '已承接',
  in_progress: '进行中',
  completed: '已完成',
  archived: '已归档',
};

export const SCENARIO_STATUS_COLORS: Record<string, string> = {
  published: 'blue',
  accepted: 'gold',
  in_progress: 'processing',
  completed: 'green',
  archived: 'default',
};

export const DELIVERY_SCENE_OPTIONS = ['api', 'dataset_file', 'tee_compute'].map((value) => ({
  label: DELIVERY_SCENE_LABELS[value],
  value,
}));

export const SCENARIO_STATUS_OPTIONS: { label: string; value: ScenarioStatus }[] = Object.entries(
  SCENARIO_STATUS_LABELS,
).map(([value, label]) => ({ label, value: value as ScenarioStatus }));

export const LANGUAGE_OPTIONS: { label: string; value: PreferredLanguage }[] = [
  { label: 'Python', value: 'python' },
  { label: 'SQL', value: 'sql' },
  { label: 'R', value: 'r' },
  { label: 'PySpark', value: 'pyspark' },
];

export const ENVIRONMENT_OPTIONS: { label: string; value: ProjectEnvironmentType }[] = [
  { label: 'L2′ MicroVM+TEE（机密计算）', value: 'l2c' },
  { label: 'L1 进程级隔离', value: 'l1' },
  { label: 'L2 容器隔离', value: 'l2' },
  { label: 'L3 专属集群', value: 'l3' },
];

export const scenarioApi = {
  listScenarios: (params?: ScenarioListParams): Promise<ScenarioListResult> =>
    api.get('/flow/scenarios', { params }) as unknown as Promise<ScenarioListResult>,

  listMyScenarios: (params?: { page?: number; page_size?: number }): Promise<ScenarioListResult> =>
    api.get('/flow/scenarios/mine', { params }) as unknown as Promise<ScenarioListResult>,

  getScenario: (scenarioId: string): Promise<Scenario> =>
    api.get(`/flow/scenarios/${scenarioId}`) as unknown as Promise<Scenario>,

  createScenario: (payload: ScenarioCreatePayload): Promise<Scenario> =>
    api.post('/flow/scenarios', payload) as unknown as Promise<Scenario>,

  acceptScenario: (scenarioId: string, payload: ScenarioAcceptPayload): Promise<ScenarioAcceptResult> =>
    api.post(`/flow/scenarios/${scenarioId}/accept`, payload) as unknown as Promise<ScenarioAcceptResult>,
};

/** 交付场景展示名：库里出现新的预埋类型时原样透出，不静默归到「API 接口交付」。 */
export function deliverySceneLabel(scene: string): string {
  return DELIVERY_SCENE_LABELS[scene] ?? scene;
}

export function scenarioStatusLabel(status: string): string {
  return SCENARIO_STATUS_LABELS[status] ?? status;
}
