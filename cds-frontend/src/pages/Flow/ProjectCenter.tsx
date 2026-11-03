import { useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Drawer,
  Dropdown,
  Empty,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { PlusOutlined, ReloadOutlined, SafetyOutlined, SendOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import {
  ENVIRONMENT_OPTIONS,
  LANGUAGE_OPTIONS,
  type PreferredLanguage,
  type ProjectEnvironmentType,
} from '../../services/scenarioApi';
import {
  DAG_NODE_LANGUAGE_OPTIONS,
  DAG_NODE_TYPE_OPTIONS,
  MILESTONE_STATUS_LABELS,
  PROJECT_STATUS_COLORS,
  PROJECT_STATUS_OPTIONS,
  dagNodeCount,
  projectApi,
  projectStatusLabel,
  type DagEdge,
  type DagNode,
  type DagValidation,
  type ModelingProject,
  type ProjectCreatePayload,
  type ProjectDetail,
  type ProjectListParams,
  type ProjectStatus,
} from '../../services/projectApi';
import { QueryErrorAlert } from '../../components/Feedback/QueryFeedback';
import { tableEmpty } from '../../components/Feedback/tableEmpty';

const { Text, Title } = Typography;

interface ParticipantRow {
  org_name?: string;
  username?: string;
  role?: string;
}

interface MilestoneRow {
  title?: string;
  due_date?: string;
}

interface ProjectFormValues {
  name: string;
  description?: string;
  preferred_language: PreferredLanguage;
  environment_type: ProjectEnvironmentType;
  participants?: ParticipantRow[];
  milestones?: MilestoneRow[];
}

function formatDateTime(iso: string | null): string {
  if (!iso) return '—';
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString('zh-CN', { hour12: false });
}

function statusTag(status: string) {
  return <Tag color={PROJECT_STATUS_COLORS[status] ?? 'default'}>{projectStatusLabel(status)}</Tag>;
}

function nodeLanguage(node: DagNode): string {
  return node.language ?? '继承项目';
}

function splitIds(raw: string): string[] {
  return raw
    .split(/[,，\s]+/)
    .map((part) => part.trim())
    .filter((part) => part.length > 0);
}

function upstreamText(edges: DagEdge[], nodeId: string): string {
  return edges
    .filter((edge) => edge.target === nodeId)
    .map((edge) => edge.source)
    .join(', ');
}

// 分层只为画图：权威判定来自后端 /dag/validate 的 Kahn 排序。这里做最长路径松弛，
// 迭代 node 轮仍能被推后的节点即落在环上（回 -1 供 UI 标红），不假装它算出了拓扑序。
function layerOf(nodes: DagNode[], edges: DagEdge[]): Record<string, number> {
  const layer: Record<string, number> = {};
  nodes.forEach((node) => {
    layer[node.id] = 0;
  });
  for (let round = 0; round < nodes.length; round += 1) {
    let moved = false;
    edges.forEach((edge) => {
      if (!(edge.source in layer) || !(edge.target in layer)) return;
      const wanted = layer[edge.source] + 1;
      if (wanted > layer[edge.target]) {
        layer[edge.target] = wanted;
        moved = true;
      }
    });
    if (!moved) break;
  }
  const capped: Record<string, number> = {};
  nodes.forEach((node) => {
    capped[node.id] = layer[node.id] >= nodes.length ? -1 : layer[node.id];
  });
  return capped;
}

function ProjectCell({ project }: { project: ModelingProject }) {
  return (
    <Space direction="vertical" size={0}>
      <Text>{project.name}</Text>
      <Text type="secondary" style={{ fontSize: 12 }}>
        {project.description?.trim() ? project.description.trim() : '未填写项目说明'}
      </Text>
    </Space>
  );
}

function CreateProjectModal({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [form] = Form.useForm<ProjectFormValues>();

  const createMutation = useMutation({
    mutationFn: (payload: ProjectCreatePayload) => projectApi.createProject(payload),
    onSuccess: (project) => {
      message.success(`建模项目「${project.name}」已创建，可开始编排任务画布`);
      queryClient.invalidateQueries({ queryKey: ['projects'] });
      onClose();
    },
    onError: () => message.error('项目创建失败，请检查语言 / 环境与协作者填写'),
  });

  const buildPayload = (values: ProjectFormValues): ProjectCreatePayload => {
    const participants = (values.participants ?? [])
      .map((row) => ({
        org_name: row?.org_name?.trim() || undefined,
        username: row?.username?.trim() || undefined,
        role: row?.role?.trim() || 'collaborator',
      }))
      .filter((row) => row.org_name || row.username);
    const milestones = (values.milestones ?? [])
      .map((row) => ({
        title: row?.title?.trim() ?? '',
        due_date: row?.due_date?.trim() || undefined,
        status: 'pending' as const,
      }))
      .filter((row) => row.title.length > 0);
    return {
      name: values.name.trim(),
      description: values.description?.trim() || undefined,
      preferred_language: values.preferred_language,
      environment_type: values.environment_type,
      participants,
      milestones,
    };
  };

  return (
    <Modal
      open
      title="新建建模项目"
      okText="创建项目"
      confirmLoading={createMutation.isPending}
      onCancel={onClose}
      onOk={() => {
        form
          .validateFields()
          .then((values) => createMutation.mutate(buildPayload(values)))
          .catch(() => undefined);
      }}
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="项目是 Notebook / 负载 / 合约之上的协同空间"
        description="协作者名册用于展示多方参与与审计上下文；成员资格的权威仍是团队，本处不另设权限。"
      />
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          preferred_language: 'python' as PreferredLanguage,
          environment_type: 'l2c' as ProjectEnvironmentType,
          participants: [{}],
          milestones: [{ title: '编排与自检', status: 'pending' }],
        }}
      >
        <Form.Item
          name="name"
          label="项目名称"
          rules={[{ required: true, message: '请输入项目名称' }, { max: 128, message: '不超过 128 个字符' }]}
        >
          <Input placeholder="例如：多中心影像联合建模" />
        </Form.Item>
        <Form.Item name="description" label="项目说明">
          <Input.TextArea rows={2} placeholder="目标、参与方与交付物（可选）" />
        </Form.Item>
        <Form.Item
          name="preferred_language"
          label="首选建模语言"
          tooltip="画布节点未单独指定语言时继承此处；R 与 PySpark 需对应内核可用，否则执行面会如实拒绝"
          rules={[{ required: true }]}
        >
          <Select options={LANGUAGE_OPTIONS} />
        </Form.Item>
        <Form.Item
          name="environment_type"
          label="沙箱环境"
          tooltip="L2′ = MicroVM+TEE；无 TEE 硬件时按 MicroVM 单一路径执行，不会降级为容器/进程隔离"
        >
          <Select options={ENVIRONMENT_OPTIONS} />
        </Form.Item>

        <Title level={5} style={{ marginTop: 8 }}>
          多方协作者
        </Title>
        <Form.List name="participants">
          {(fields, { add, remove }) => (
            <>
              {fields.map((field) => (
                <Space key={field.key} align="baseline" style={{ display: 'flex', marginBottom: 4 }}>
                  <Form.Item name={[field.name, 'org_name']} style={{ marginBottom: 8 }}>
                    <Input placeholder="机构方，如 数据提供方A" />
                  </Form.Item>
                  <Form.Item name={[field.name, 'username']} style={{ marginBottom: 8 }}>
                    <Input placeholder="平台账号（可选）" />
                  </Form.Item>
                  <Form.Item name={[field.name, 'role']} style={{ marginBottom: 8 }}>
                    <Input placeholder="角色，如 数据科学家" style={{ width: 140 }} />
                  </Form.Item>
                  <Button size="small" type="text" danger onClick={() => remove(field.name)}>
                    移除
                  </Button>
                </Space>
              ))}
              <Button size="small" icon={<PlusOutlined />} onClick={() => add({})}>
                增加协作者
              </Button>
            </>
          )}
        </Form.List>

        <Title level={5} style={{ marginTop: 16 }}>
          里程碑
        </Title>
        <Form.List name="milestones">
          {(fields, { add, remove }) => (
            <>
              {fields.map((field) => (
                <Space key={field.key} align="baseline" style={{ display: 'flex', marginBottom: 4 }}>
                  <Form.Item
                    name={[field.name, 'title']}
                    rules={[{ required: true, message: '请输入里程碑名称' }]}
                    style={{ marginBottom: 8 }}
                  >
                    <Input placeholder="里程碑，如 模型评估通过" />
                  </Form.Item>
                  <Form.Item name={[field.name, 'due_date']} style={{ marginBottom: 8 }}>
                    <Input placeholder="到期日 2026-12-31（可选）" />
                  </Form.Item>
                  <Button size="small" type="text" danger onClick={() => remove(field.name)}>
                    移除
                  </Button>
                </Space>
              ))}
              <Button size="small" icon={<PlusOutlined />} onClick={() => add({ status: 'pending' })}>
                增加里程碑
              </Button>
            </>
          )}
        </Form.List>
      </Form>
    </Modal>
  );
}

function DagBoard({ project }: { project: ProjectDetail }) {
  const queryClient = useQueryClient();
  const frozen = project.status !== 'active';

  const [nodes, setNodes] = useState<DagNode[]>(project.dag_definition?.nodes ?? []);
  const [edges, setEdges] = useState<DagEdge[]>(project.dag_definition?.edges ?? []);
  const [draft, setDraft] = useState({ id: '', label: '', type: 'task', language: '' });
  const [upstreamDraft, setUpstreamDraft] = useState<Record<string, string>>({});
  const [result, setResult] = useState<DagValidation | null>(null);

  const baseline = useMemo(() => JSON.stringify({ nodes: project.dag_definition?.nodes ?? [], edges: project.dag_definition?.edges ?? [] }), [project]);
  const dirty = useMemo(() => JSON.stringify({ nodes, edges }) !== baseline, [nodes, edges, baseline]);

  const layers = layerOf(nodes, edges);
  const cyclic = new Set(result?.cyclic_nodes ?? []);
  const known = new Set(nodes.map((node) => node.id));

  const saveMutation = useMutation({
    mutationFn: () => projectApi.saveDag(project.id, { nodes, edges }),
    onSuccess: () => {
      message.success('任务编排已保存');
      queryClient.invalidateQueries({ queryKey: ['project-detail', project.id] });
      queryClient.invalidateQueries({ queryKey: ['projects'] });
    },
    onError: () => message.error('编排保存失败：节点标识需为字母数字，或项目已不在可编辑状态'),
  });

  const validateMutation = useMutation({
    mutationFn: () => projectApi.validateDag(project.id),
    onSuccess: (data) => {
      setResult(data);
      if (data.valid) message.success(`拓扑校验通过，${data.execution_order.length} 个节点可执行`);
      else message.error('拓扑校验未通过，请按提示修正画布');
    },
    onError: () => message.error('校验请求失败，画布可能尚未保存'),
  });

  const addNode = () => {
    const id = draft.id.trim();
    if (!id) {
      message.warning('请填写节点标识');
      return;
    }
    if (known.has(id)) {
      message.error(`节点标识「${id}」已存在`);
      return;
    }
    setNodes((prev) => [
      ...prev,
      {
        id,
        label: draft.label.trim() || id,
        type: draft.type,
        language: draft.language || undefined,
      },
    ]);
    setDraft({ id: '', label: '', type: 'task', language: '' });
    setResult(null);
  };

  const removeNode = (id: string) => {
    setNodes((prev) => prev.filter((node) => node.id !== id));
    setEdges((prev) => prev.filter((edge) => edge.source !== id && edge.target !== id));
    setResult(null);
  };

  const commitUpstream = (nodeId: string) => {
    const raw = upstreamDraft[nodeId];
    if (raw === undefined) return;
    const wanted = splitIds(raw).filter((source) => source !== nodeId && known.has(source));
    setEdges((prev) => [
      ...prev.filter((edge) => edge.target !== nodeId),
      ...wanted.map((source) => ({ source, target: nodeId })),
    ]);
    setUpstreamDraft((prev) => {
      const next = { ...prev };
      delete next[nodeId];
      return next;
    });
    setResult(null);
  };

  const grouped = useMemo(() => {
    const buckets = new Map<number, DagNode[]>();
    nodes.forEach((node) => {
      const depth = layers[node.id] ?? 0;
      const list = buckets.get(depth) ?? [];
      list.push(node);
      buckets.set(depth, list);
    });
    return [...buckets.entries()].sort((a, b) => a[0] - b[0]);
  }, [nodes, layers]);

  return (
    <Card
      size="small"
      title={
        <Space>
          <span>DAG 任务编排</span>
          {dirty ? <Tag color="orange">有未保存修改</Tag> : null}
          {frozen ? <Tag>已冻结（{projectStatusLabel(project.status)}）</Tag> : null}
        </Space>
      }
      extra={
        <Space>
          <Tooltip title="校验读的是已保存到后端的画布，请先保存">
            <Button size="small" icon={<SafetyOutlined />} loading={validateMutation.isPending} onClick={() => validateMutation.mutate()}>
              一键拓扑校验
            </Button>
          </Tooltip>
          <Button
            size="small"
            type="primary"
            disabled={!dirty || frozen}
            loading={saveMutation.isPending}
            onClick={() => saveMutation.mutate()}
          >
            保存编排
          </Button>
        </Space>
      }
    >
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        {!frozen ? (
          <Space wrap align="end">
            <Input
              size="small"
              style={{ width: 150 }}
              placeholder="节点标识 clean_01"
              value={draft.id}
              onChange={(e) => setDraft({ ...draft, id: e.target.value })}
            />
            <Input
              size="small"
              style={{ width: 150 }}
              placeholder="节点名称，如 数据清洗"
              value={draft.label}
              onChange={(e) => setDraft({ ...draft, label: e.target.value })}
            />
            <Select
              size="small"
              style={{ width: 130 }}
              value={draft.type}
              onChange={(value: string) => setDraft({ ...draft, type: value })}
              options={DAG_NODE_TYPE_OPTIONS}
            />
            <Select
              size="small"
              style={{ width: 130 }}
              value={draft.language}
              onChange={(value: string) => setDraft({ ...draft, language: value })}
              options={[{ label: '继承项目语言', value: '' }, ...DAG_NODE_LANGUAGE_OPTIONS]}
            />
            <Button size="small" icon={<PlusOutlined />} onClick={addNode}>
              添加节点
            </Button>
          </Space>
        ) : null}

        {nodes.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="画布为空，尚无可编排节点" />
        ) : (
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            {grouped.map(([depth, row]) => (
              <div key={depth}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {depth < 0 ? '存在环路，层级无法确定' : `第 ${depth + 1} 层`}
                </Text>
                <Space direction="vertical" size={6} style={{ width: '100%', marginTop: 4 }}>
                  {row.map((node) => (
                    <Space key={node.id} wrap align="center">
                      <Tag color={cyclic.has(node.id) ? 'red' : 'blue'}>{node.id}</Tag>
                      <Text>{node.label}</Text>
                      <Tag>{node.type}</Tag>
                      <Tag color="geekblue">{nodeLanguage(node)}</Tag>
                      {!frozen ? (
                        <>
                          <Input
                            size="small"
                            style={{ width: 190 }}
                            placeholder={`上游依赖（当前：${upstreamText(edges, node.id) || '无'}）`}
                            value={upstreamDraft[node.id] ?? ''}
                            onChange={(e) => setUpstreamDraft({ ...upstreamDraft, [node.id]: e.target.value })}
                            onBlur={() => commitUpstream(node.id)}
                          />
                          <Button size="small" type="text" danger onClick={() => removeNode(node.id)}>
                            删除
                          </Button>
                        </>
                      ) : (
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          上游：{upstreamText(edges, node.id) || '无'}
                        </Text>
                      )}
                    </Space>
                  ))}
                </Space>
              </div>
            ))}
          </Space>
        )}

        {result ? (
          result.valid ? (
            <Alert
              type="success"
              showIcon
              message={`无环，执行顺序共 ${result.execution_order.length} 个节点`}
              description={
                <Space size={[4, 4]} wrap>
                  {result.execution_order.map((id, index) => (
                    <Tag key={id} color="green">
                      {index + 1}. {id}
                    </Tag>
                  ))}
                </Space>
              }
            />
          ) : (
            <Alert
              type="error"
              showIcon
              message="拓扑校验未通过"
              description={
                <Space direction="vertical" size={2}>
                  {result.errors.map((err) => (
                    <Text key={err} style={{ fontSize: 12 }}>
                      {err}
                    </Text>
                  ))}
                  {result.cyclic_nodes.length ? (
                    <Text style={{ fontSize: 12 }}>环路节点：{result.cyclic_nodes.join(' → ')}</Text>
                  ) : null}
                </Space>
              }
            />
          )
        ) : (
          <Text type="secondary" style={{ fontSize: 12 }}>
            尚未校验：保存后点击「一键拓扑校验」，后端以 Kahn 算法判定无环并回执行顺序。
          </Text>
        )}
      </Space>
    </Card>
  );
}

function ProjectDrawer({ projectId, onClose }: { projectId: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const detailQuery = useQuery({
    queryKey: ['project-detail', projectId],
    queryFn: () => projectApi.getProject(projectId),
    enabled: Boolean(projectId),
  });
  const project = detailQuery.data;

  const reviewMutation = useMutation({
    mutationFn: () => projectApi.submitReview(projectId),
    onSuccess: (data) => {
      message.success(`项目「${data.name}」已提交审核，画布随后冻结`);
      queryClient.invalidateQueries({ queryKey: ['project-detail', projectId] });
      queryClient.invalidateQueries({ queryKey: ['projects'] });
    },
    onError: () => message.error('提交审核失败：请先保存并通过拓扑校验，或项目已在审核中'),
  });

  return (
    <Drawer
      open
      width={860}
      title={project ? `建模项目 · ${project.name}` : '建模项目'}
      onClose={onClose}
      extra={
        <Space>
          <Button size="small" icon={<ReloadOutlined />} onClick={() => detailQuery.refetch()}>
            刷新
          </Button>
          <Dropdown.Button
            size="small"
            type="primary"
            icon={<SendOutlined />}
            disabled={!project || project.status !== 'active'}
            loading={reviewMutation.isPending}
            onClick={() => reviewMutation.mutate()}
            menu={{ items: [{ key: 'hint', label: '提交后 DAG 写入冻结，需运营退回方可再改' }] }}
          >
            提交审核
          </Dropdown.Button>
        </Space>
      }
    >
      {detailQuery.isError ? <QueryErrorAlert error={detailQuery.error} message="项目详情加载失败" /> : null}
      {project ? (
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Descriptions
            size="small"
            column={2}
            bordered
            items={[
              { key: 'status', label: '状态', children: statusTag(project.status) },
              { key: 'lang', label: '首选语言', children: project.preferred_language },
              { key: 'env', label: '沙箱环境', children: project.environment_type },
              { key: 'team', label: '归属团队', children: project.team_id ?? '未归属团队' },
              { key: 'scenario', label: '来源场景', children: project.scenario_id ?? '自建（非场景承接）' },
              { key: 'contract', label: '关联合约', children: project.contract_id ?? '未关联' },
              { key: 'created', label: '创建时间', children: formatDateTime(project.created_at) },
              { key: 'updated', label: '更新时间', children: formatDateTime(project.updated_at) },
              { key: 'desc', label: '项目说明', span: 2, children: project.description?.trim() || '未填写项目说明' },
            ]}
          />

          <Card size="small" title={`多方协同名册（${project.participants.length}）`}>
            {project.participants.length ? (
              <Space size={[0, 6]} wrap>
                {project.participants.map((person, index) => (
                  <Tag key={`${person.org_name ?? 'self'}-${index}`}>
                    {person.org_name ?? person.username ?? '本人'} · {person.role ?? 'collaborator'}
                  </Tag>
                ))}
              </Space>
            ) : (
              <Text type="secondary">仅创建人参与</Text>
            )}
          </Card>

          <Card size="small" title={`里程碑（${project.milestones.length}）`}>
            {project.milestones.length ? (
              <Space direction="vertical" size={2}>
                {project.milestones.map((item, index) => (
                  <Text key={`${item.title}-${index}`} style={{ fontSize: 12 }}>
                    {item.title} · {MILESTONE_STATUS_LABELS[item.status ?? 'pending'] ?? item.status}
                    {item.due_date ? ` · 到期 ${item.due_date}` : ''}
                  </Text>
                ))}
              </Space>
            ) : (
              <Text type="secondary">未配置里程碑</Text>
            )}
          </Card>

          <Card size="small" title="联动画作">
            <Space direction="vertical" size={4} style={{ width: '100%' }}>
              {project.notebooks.map((nb) => (
                <Text key={nb.id} style={{ fontSize: 12 }}>
                  Notebook · {nb.title} · {nb.language} · {nb.status}
                </Text>
              ))}
              {project.workloads.map((wl) => (
                <Text key={wl.id} style={{ fontSize: 12 }}>
                  负载 · {wl.name} · {wl.language} · {wl.env} · {wl.status}
                </Text>
              ))}
              {project.unresolved_notebook_ids.length || project.unresolved_workload_ids.length ? (
                <Alert
                  type="warning"
                  showIcon
                  message="存在失效引用"
                  description={`引用清单里有 ${project.unresolved_notebook_ids.length} 个 Notebook、${project.unresolved_workload_ids.length} 个负载已不存在，请在画布中移除对应节点后重存。`}
                />
              ) : null}
              {!project.notebooks.length && !project.workloads.length ? (
                <Text type="secondary">尚无联动画布与负载</Text>
              ) : null}
            </Space>
          </Card>

          <DagBoard key={`${project.id}-${project.updated_at ?? ''}`} project={project} />
        </Space>
      ) : null}
    </Drawer>
  );
}

export default function ProjectCenter() {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [keyword, setKeyword] = useState<string>();
  const [status, setStatus] = useState<ProjectStatus>();
  const [language, setLanguage] = useState<ProjectListParams['preferred_language']>();
  const [detailId, setDetailId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

  const listQuery = useQuery({
    queryKey: ['projects', page, pageSize, keyword, status, language],
    queryFn: () => projectApi.listProjects({ page, page_size: pageSize, keyword, status, preferred_language: language }),
  });

  const columns: ColumnsType<ModelingProject> = [
    {
      title: '项目',
      dataIndex: 'name',
      key: 'name',
      render: (_: string, project) => (
        <Button type="link" style={{ padding: 0, height: 'auto' }} onClick={() => setDetailId(project.id)}>
          <ProjectCell project={project} />
        </Button>
      ),
    },
    { title: '首选语言', dataIndex: 'preferred_language', key: 'language', width: 110 },
    {
      title: '画布节点',
      key: 'nodes',
      width: 100,
      render: (_: unknown, project) => <Text>{dagNodeCount(project)}</Text>,
    },
    {
      title: '协同方',
      key: 'participants',
      width: 100,
      render: (_: unknown, project) => <Text>{project.participants?.length ?? 0}</Text>,
    },
    { title: '状态', dataIndex: 'status', key: 'status', width: 130, render: (value: string) => statusTag(value) },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 180,
      render: (value: string | null) => formatDateTime(value),
    },
    {
      title: '操作',
      key: 'actions',
      width: 90,
      render: (_: unknown, project) => (
        <Button size="small" onClick={() => setDetailId(project.id)}>
          看板
        </Button>
      ),
    },
  ];

  return (
    <div style={{ padding: 16 }}>
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
          <div>
            <Title level={4} style={{ marginBottom: 4 }}>
              建模项目
            </Title>
            <Text type="secondary">
              协同空间与任务编排：项目聚合 Notebook / 负载，画布在此编排并经后端拓扑校验后方可提交审核。
            </Text>
          </div>
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => listQuery.refetch()}>
              刷新
            </Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
              新建项目
            </Button>
          </Space>
        </Space>

        {listQuery.isError ? <QueryErrorAlert error={listQuery.error} message="项目列表加载失败" /> : null}

        <Space wrap>
          <Input.Search
            style={{ width: 260 }}
            placeholder="搜索项目名称 / 说明"
            allowClear
            onSearch={(value) => {
              setPage(1);
              setKeyword(value.trim() || undefined);
            }}
          />
          <Select
            style={{ width: 160 }}
            allowClear
            placeholder="按状态筛选"
            value={status}
            onChange={(value?: ProjectStatus) => {
              setPage(1);
              setStatus(value);
            }}
            options={PROJECT_STATUS_OPTIONS}
          />
          <Select
            style={{ width: 160 }}
            allowClear
            placeholder="按建模语言筛选"
            value={language}
            onChange={(value?: ProjectListParams['preferred_language']) => {
              setPage(1);
              setLanguage(value);
            }}
            options={DAG_NODE_LANGUAGE_OPTIONS}
          />
          <Text type="secondary">共 {listQuery.data?.total ?? 0} 个项目</Text>
        </Space>

        <Table<ModelingProject>
          rowKey="id"
          size="small"
          loading={listQuery.isFetching}
          columns={columns}
          dataSource={listQuery.data?.items ?? []}
          locale={tableEmpty('暂无建模项目，可从场景中心承接或在此新建')}
          pagination={{
            current: page,
            pageSize,
            total: listQuery.data?.total ?? 0,
            showSizeChanger: true,
            onChange: (next, nextSize) => {
              setPage(next);
              setPageSize(nextSize);
            },
          }}
        />
      </Space>

      {createOpen ? <CreateProjectModal onClose={() => setCreateOpen(false)} /> : null}
      {detailId ? <ProjectDrawer projectId={detailId} onClose={() => setDetailId(null)} /> : null}
    </div>
  );
}
