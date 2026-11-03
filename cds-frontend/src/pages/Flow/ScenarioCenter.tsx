import { useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import {
  ENVIRONMENT_OPTIONS,
  LANGUAGE_OPTIONS,
  SCENARIO_STATUS_OPTIONS,
  DELIVERY_SCENE_OPTIONS,
  deliverySceneLabel,
  scenarioApi,
  scenarioStatusLabel,
  SCENARIO_STATUS_COLORS,
  type PreferredLanguage,
  type ProjectEnvironmentType,
  type Scenario,
  type ScenarioCreatePayload,
  type ScenarioListParams,
  type ScenarioStatus,
} from '../../services/scenarioApi';
import { teamApi, type Team } from '../../services/teamApi';
import { QueryErrorAlert } from '../../components/Feedback/QueryFeedback';
import { tableEmpty } from '../../components/Feedback/tableEmpty';

const { Text, Title, Paragraph } = Typography;

type ScenarioView = 'development' | 'delivery' | 'mine';

const SCENARIO_COLORS: Record<string, string> = SCENARIO_STATUS_COLORS;

function formatDateTime(iso: string | null): string {
  if (!iso) return '—';
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString('zh-CN', { hour12: false });
}

function statusTag(status: string) {
  return <Tag color={SCENARIO_COLORS[status] ?? 'default'}>{scenarioStatusLabel(status)}</Tag>;
}

function sceneTag(scene: string) {
  return <Tag color="geekblue">{deliverySceneLabel(scene)}</Tag>;
}

function keyList(values: Record<string, unknown> | undefined | null): string[] {
  return values ? Object.keys(values) : [];
}

function parseJsonObject(raw: string | undefined, field: string): Record<string, unknown> {
  const text = (raw ?? '').trim();
  if (!text) return {};
  const parsed: unknown = JSON.parse(text);
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    throw new Error(`${field}需要是 JSON 对象，例如 {"output_fields": "仅汇总指标"}`);
  }
  return parsed as Record<string, unknown>;
}

function ScenarioCell({ scenario }: { scenario: Scenario }) {
  return (
    <Space direction="vertical" size={0}>
      <Text>{scenario.title}</Text>
      <Text type="secondary" style={{ fontSize: 12 }}>
        {scenario.industry ?? '未标注行业'}
        {scenario.target_description ? ` · ${scenario.target_description}` : ''}
      </Text>
    </Space>
  );
}

function AcceptModal({
  scenario,
  teams,
  onClose,
}: {
  scenario: Scenario;
  teams: Team[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [form] = Form.useForm<{
    project_name: string;
    preferred_language: PreferredLanguage;
    environment_type: ProjectEnvironmentType;
    team_id?: string;
  }>();

  const acceptMutation = useMutation({
    mutationFn: (values: {
      project_name: string;
      preferred_language: PreferredLanguage;
      environment_type: ProjectEnvironmentType;
      team_id?: string;
    }) =>
      scenarioApi.acceptScenario(scenario.id, {
        project_name: values.project_name.trim(),
        preferred_language: values.preferred_language,
        environment_type: values.environment_type,
        team_id: values.team_id || undefined,
      }),
    onSuccess: (result) => {
      message.success(`已承接，建模项目「${result.project.name}」已创建`);
      queryClient.invalidateQueries({ queryKey: ['scenarios'] });
      queryClient.invalidateQueries({ queryKey: ['scenario-detail', scenario.id] });
      onClose();
    },
    onError: () => message.error('承接失败，场景状态可能已被其他团队变更'),
  });

  const constraintKeys = keyList(scenario.compliance_constraints);
  const techKeys = keyList(scenario.delivery_tech_config);

  return (
    <Modal
      open
      title={`承接场景 · ${scenario.title}`}
      okText="确认承接并创建项目"
      confirmLoading={acceptMutation.isPending}
      onCancel={onClose}
      onOk={() => {
        form
          .validateFields()
          .then((values) => acceptMutation.mutate(values))
          .catch(() => undefined);
      }}
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message={`承接会新建建模项目，并注入场景约束：合规约束 ${constraintKeys.length} 项、交付预埋 ${techKeys.length} 项`}
        description={`交付场景 ${deliverySceneLabel(scenario.delivery_scene)}；一个场景只能被承接一次，项目归属团队后由该团队自行编排任务。`}
      />
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          project_name: scenario.title,
          preferred_language: 'python' as PreferredLanguage,
          environment_type: 'l2c' as ProjectEnvironmentType,
          team_id: teams.length === 1 ? teams[0].id : undefined,
        }}
      >
        <Form.Item
          name="project_name"
          label="项目名称"
          rules={[{ required: true, message: '请输入项目名称' }, { max: 128, message: '不超过 128 个字符' }]}
        >
          <Input placeholder="默认沿用场景标题" />
        </Form.Item>
        <Form.Item name="preferred_language" label="首选建模语言" rules={[{ required: true }]}>
          <Select options={LANGUAGE_OPTIONS} />
        </Form.Item>
        <Form.Item
          name="environment_type"
          label="沙箱环境"
          tooltip="L2′ = MicroVM+TEE；无 TEE 硬件时按 MicroVM 单一路径执行，不会降级为容器/进程隔离"
        >
          <Select options={ENVIRONMENT_OPTIONS} />
        </Form.Item>
        <Form.Item
          name="team_id"
          label="承接团队"
          rules={teams.length > 1 ? [{ required: true, message: '账号属于多个团队，请指定承接团队' }] : []}
          tooltip={teams.length === 0 ? '当前账号不属于任何团队，项目将建为未归属团队' : undefined}
        >
          <Select
            allowClear
            placeholder={teams.length === 0 ? '未加入团队（项目不绑定团队）' : '选择承接团队'}
            options={teams.map((team) => ({ label: team.name, value: team.id }))}
            notFoundContent="未加入任何团队"
          />
        </Form.Item>
      </Form>
    </Modal>
  );
}

function PublishModal({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [form] = Form.useForm<{
    title: string;
    industry?: string;
    delivery_scene: string;
    business_background?: string;
    target_description?: string;
    suggested_resource_ids?: string[];
    compliance_constraints?: string;
    delivery_tech_config?: string;
  }>();

  const createMutation = useMutation({
    mutationFn: (payload: ScenarioCreatePayload) => scenarioApi.createScenario(payload),
    onSuccess: (scenario) => {
      message.success(`场景「${scenario.title}」已发布，等待开发团队承接`);
      queryClient.invalidateQueries({ queryKey: ['scenarios'] });
      onClose();
    },
    onError: () => message.error('场景发布失败'),
  });

  return (
    <Modal
      open
      title="发布场景"
      okText="发布"
      confirmLoading={createMutation.isPending}
      onCancel={onClose}
      onOk={() => {
        form
          .validateFields()
          .then((values) => {
            let payload: ScenarioCreatePayload;
            try {
              payload = toPayload(values);
            } catch (error) {
              message.error(error instanceof Error ? error.message : 'JSON 字段格式不正确');
              return;
            }
            createMutation.mutate(payload);
          })
          .catch(() => undefined);
      }}
    >
      <Form form={form} layout="vertical" initialValues={{ delivery_scene: 'api' }}>
        <Form.Item
          name="title"
          label="场景标题"
          rules={[{ required: true, message: '请输入场景标题' }, { max: 256, message: '不超过 256 个字符' }]}
        >
          <Input placeholder="例如：医疗影像科研协作场景" />
        </Form.Item>
        <Form.Item name="industry" label="所属行业">
          <Input placeholder="例如：医疗 / 金融 / 零售" />
        </Form.Item>
        <Form.Item name="delivery_scene" label="交付场景" rules={[{ required: true }]}>
          <Select options={DELIVERY_SCENE_OPTIONS} />
        </Form.Item>
        <Form.Item name="business_background" label="业务背景">
          <Input.TextArea rows={2} />
        </Form.Item>
        <Form.Item name="target_description" label="建模目标与交付物">
          <Input.TextArea rows={2} />
        </Form.Item>
        <Form.Item name="suggested_resource_ids" label="建议数据资源" tooltip="只登记意向，最终授权仍以合约白名单为准">
          <Select mode="tags" placeholder="粘贴数据资源 ID，可留空" open={false} />
        </Form.Item>
        <Form.Item name="compliance_constraints" label="合规约束（JSON 对象）">
          <Input.TextArea rows={2} placeholder='{"output_fields": "仅汇总指标"}' />
        </Form.Item>
        <Form.Item name="delivery_tech_config" label="交付技术预埋（JSON 对象）">
          <Input.TextArea rows={2} placeholder='{"tee_image": "tee-cuda:12.4"}' />
        </Form.Item>
      </Form>
    </Modal>
  );
}

interface PublishFormValues {
  title: string;
  industry?: string;
  delivery_scene: string;
  business_background?: string;
  target_description?: string;
  suggested_resource_ids?: string[];
  compliance_constraints?: string;
  delivery_tech_config?: string;
}

function toPayload(values: PublishFormValues): ScenarioCreatePayload {
  return {
    title: values.title.trim(),
    industry: values.industry?.trim() || undefined,
    business_background: values.business_background?.trim() || undefined,
    target_description: values.target_description?.trim() || undefined,
    delivery_scene: values.delivery_scene,
    suggested_resource_ids: values.suggested_resource_ids ?? [],
    compliance_constraints: parseJsonObject(values.compliance_constraints, '合规约束'),
    delivery_tech_config: parseJsonObject(values.delivery_tech_config, '交付技术预埋'),
  };
}

function ScenarioDetail({ scenarioId, onClose }: { scenarioId: string; onClose: () => void }) {
  const detailQuery = useQuery({
    queryKey: ['scenario-detail', scenarioId],
    queryFn: () => scenarioApi.getScenario(scenarioId),
  });
  const scenario = detailQuery.data;
  const project = scenario?.project ?? null;

  return (
    <Drawer open width={720} title="场景详情" onClose={onClose}>
      {detailQuery.isError && <QueryErrorAlert error={detailQuery.error} onRetry={() => detailQuery.refetch()} />}
      {scenario && (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Descriptions
            size="small"
            column={2}
            bordered
            items={[
              { key: 'title', label: '场景', span: 2, children: scenario.title },
              { key: 'industry', label: '行业', children: scenario.industry ?? '未标注' },
              { key: 'status', label: '状态', children: statusTag(scenario.status) },
              { key: 'scene', label: '交付场景', children: sceneTag(scenario.delivery_scene) },
              { key: 'created', label: '发布时间', children: formatDateTime(scenario.created_at) },
              { key: 'background', label: '业务背景', span: 2, children: scenario.business_background ?? '未填写' },
              { key: 'target', label: '建模目标', span: 2, children: scenario.target_description ?? '未填写' },
            ]}
          />
          <Card size="small" title="合规约束（承接后注入项目）">
            {keyList(scenario.compliance_constraints).length ? (
              <Descriptions
                size="small"
                column={1}
                items={Object.entries(scenario.compliance_constraints).map(([key, value]) => ({
                  key,
                  label: key,
                  children: typeof value === 'string' ? value : JSON.stringify(value),
                }))}
              />
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="发布方未填写合规约束" />
            )}
          </Card>
          <Card size="small" title="交付技术预埋">
            {keyList(scenario.delivery_tech_config).length ? (
              <Space size={[0, 6]} wrap>
                {Object.entries(scenario.delivery_tech_config).map(([key, value]) => (
                  <Tag key={key}>
                    {key}={typeof value === 'string' ? value : JSON.stringify(value)}
                  </Tag>
                ))}
              </Space>
            ) : (
              <Text type="secondary">发布方未做交付预埋</Text>
            )}
          </Card>
          <Card size="small" title="建议数据资源">
            {scenario.suggested_resource_ids.length ? (
              <Space direction="vertical" size={2}>
                {scenario.suggested_resource_ids.map((id) => (
                  <Text key={id} style={{ fontSize: 12 }}>
                    {id}
                  </Text>
                ))}
                <Text type="secondary" style={{ fontSize: 12 }}>
                  建议清单只登记意向，能否读取仍由合约白名单决定。
                </Text>
              </Space>
            ) : (
              <Text type="secondary">发布方未指定建议资源</Text>
            )}
          </Card>
          <Card size="small" title="承接项目">
            {project ? (
              <Descriptions
                size="small"
                column={2}
                bordered
                items={[
                  { key: 'name', label: '项目', span: 2, children: project.name },
                  { key: 'lang', label: '建模语言', children: project.preferred_language },
                  { key: 'env', label: '沙箱环境', children: project.environment_type },
                  { key: 'team', label: '归属团队', children: project.team_id ?? '未归属团队' },
                  { key: 'created', label: '承接时间', children: formatDateTime(project.created_at) },
                ]}
              />
            ) : (
              <Text type="secondary">该场景尚未被承接</Text>
            )}
          </Card>
        </Space>
      )}
    </Drawer>
  );
}

export default function ScenarioCenter() {
  const [view, setView] = useState<ScenarioView>('development');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [keyword, setKeyword] = useState<string>();
  const [status, setStatus] = useState<ScenarioStatus>();
  const [scene, setScene] = useState<string>();
  const [detailId, setDetailId] = useState<string | null>(null);
  const [acceptTarget, setAcceptTarget] = useState<Scenario | null>(null);
  const [publishOpen, setPublishOpen] = useState(false);

  const listQuery = useQuery({
    queryKey: ['scenarios', view, page, pageSize, keyword, status, scene],
    queryFn: () => {
      if (view === 'mine') {
        return scenarioApi.listMyScenarios({ page, page_size: pageSize });
      }
      const params: ScenarioListParams = { page, page_size: pageSize, keyword, status };
      if (scene) params.delivery_scene = scene;
      return scenarioApi.listScenarios(params);
    },
  });

  const teamsQuery = useQuery({
    queryKey: ['teams', 'mine', 'scenarios'],
    queryFn: () => teamApi.fetchTeams({ page: 1, page_size: 100 }),
  });
  const teams = teamsQuery.data?.items ?? [];

  const resetPage = () => setPage(1);

  const openAccept = (scenario: Scenario) => {
    if (teamsQuery.isError) message.warning('团队清单取不回，可先不带团队承接，稍后由管理员归属');
    setAcceptTarget(scenario);
  };

  const baseColumns: ColumnsType<Scenario> = [
    {
      title: '场景',
      dataIndex: 'title',
      key: 'title',
      render: (_: string, scenario) => (
        <Button type="link" style={{ padding: 0, height: 'auto' }} onClick={() => setDetailId(scenario.id)}>
          <ScenarioCell scenario={scenario} />
        </Button>
      ),
    },
    {
      title: '交付场景',
      dataIndex: 'delivery_scene',
      key: 'delivery_scene',
      width: 150,
      render: (value: string) => sceneTag(value),
    },
    { title: '状态', dataIndex: 'status', key: 'status', width: 140, render: (value: string) => statusTag(value) },
  ];

  const developmentColumns: ColumnsType<Scenario> = [
    ...baseColumns,
    {
      title: '承接方',
      key: 'accepted',
      width: 160,
      render: (_: unknown, scenario) =>
        scenario.accepted_team_id ?? scenario.accepted_by ? (
          <Text>已有人承接</Text>
        ) : (
          <Text type="secondary">待承接</Text>
        ),
    },
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
      width: 170,
      render: (_: unknown, scenario) => (
        <Space>
          <Button size="small" onClick={() => setDetailId(scenario.id)}>
            详情
          </Button>
          <Tooltip title={scenario.status === 'published' ? undefined : '只有已发布的场景可承接'}>
            <Button
              size="small"
              type="primary"
              disabled={scenario.status !== 'published'}
              onClick={() => openAccept(scenario)}
            >
              一键承接
            </Button>
          </Tooltip>
        </Space>
      ),
    },
  ];

  const deliveryColumns: ColumnsType<Scenario> = [
    ...baseColumns,
    {
      title: '交付预埋',
      key: 'tech',
      render: (_: unknown, scenario) => {
        const keys = keyList(scenario.delivery_tech_config);
        return keys.length ? (
          <Space size={[0, 4]} wrap>
            {keys.map((key) => (
              <Tag key={key}>{key}</Tag>
            ))}
          </Space>
        ) : (
          <Text type="secondary">未预埋</Text>
        );
      },
    },
    {
      title: '承接项目',
      key: 'project',
      width: 200,
      render: (_: unknown, scenario) =>
        scenario.project ? (
          <Space direction="vertical" size={0}>
            <Text>{scenario.project.name}</Text>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {scenario.project.preferred_language} · {scenario.project.environment_type}
            </Text>
          </Space>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 90,
      render: (_: unknown, scenario) => (
        <Button size="small" onClick={() => setDetailId(scenario.id)}>
          详情
        </Button>
      ),
    },
  ];

  const mineColumns: ColumnsType<Scenario> = [
    ...baseColumns,
    {
      title: '建模项目',
      key: 'project',
      render: (_: unknown, scenario) =>
        scenario.project ? (
          <Space direction="vertical" size={0}>
            <Text>{scenario.project.name}</Text>
            <Text type="secondary" style={{ fontSize: 12 }}>
              语言 {scenario.project.preferred_language} · 环境 {scenario.project.environment_type} ·{' '}
              {scenario.project.team_id ? '已绑定团队' : '未绑定团队'}
            </Text>
          </Space>
        ) : (
          <Text type="warning">场景状态已变更但缺少承接项目，请联系管理员核对</Text>
        ),
    },
    {
      title: '承接时间',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 180,
      render: (value: string | null) => formatDateTime(value),
    },
    {
      title: '操作',
      key: 'actions',
      width: 90,
      render: (_: unknown, scenario) => (
        <Button size="small" onClick={() => setDetailId(scenario.id)}>
          详情
        </Button>
      ),
    },
  ];

  const columnsByView: Record<ScenarioView, ColumnsType<Scenario>> = {
    development: developmentColumns,
    delivery: deliveryColumns,
    mine: mineColumns,
  };

  const emptyText: Record<ScenarioView, string> = {
    development: '暂无可承接的场景',
    delivery: '该交付场景下暂无场景',
    mine: '还没有承接过场景，去「数据开发场景」挑一个',
  };

  return (
    <Card
      title={
        <Space>
          <Title level={4} style={{ margin: 0 }}>
            场景中心
          </Title>
          <Text type="secondary">数据开发场景与数据交付场景的发布、筛选与一键承接</Text>
        </Space>
      }
      extra={
        <Space wrap>
          <Input.Search
            allowClear
            placeholder="搜索场景标题 / 行业"
            style={{ width: 220 }}
            onSearch={(value) => {
              resetPage();
              setKeyword(value.trim() || undefined);
            }}
          />
          {view !== 'mine' && (
            <Select
              allowClear
              placeholder="场景状态"
              style={{ width: 150 }}
              options={SCENARIO_STATUS_OPTIONS}
              value={status}
              onChange={(value?: ScenarioStatus) => {
                resetPage();
                setStatus(value);
              }}
            />
          )}
          {view === 'delivery' && (
            <Select
              allowClear
              placeholder="交付场景"
              style={{ width: 160 }}
              options={DELIVERY_SCENE_OPTIONS}
              value={scene}
              onChange={(value?: string) => {
                resetPage();
                setScene(value);
              }}
            />
          )}
          <Button icon={<ReloadOutlined />} onClick={() => listQuery.refetch()}>
            刷新
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setPublishOpen(true)}>
            发布场景
          </Button>
        </Space>
      }
    >
      <Tabs
        activeKey={view}
        onChange={(key) => {
          resetPage();
          setView(key as ScenarioView);
        }}
        items={[
          { key: 'development', label: '数据开发场景' },
          { key: 'delivery', label: '数据交付场景' },
          { key: 'mine', label: '我的承接' },
        ]}
      />
      {view === 'delivery' && (
        <Paragraph type="secondary" style={{ fontSize: 12 }}>
          交付场景按发布方的技术预埋分类：API 接口交付、数据集文件交付、TEE 密态交付。实际交付仍受合约与出口网关约束。
        </Paragraph>
      )}
      {listQuery.isError && <QueryErrorAlert error={listQuery.error} onRetry={() => listQuery.refetch()} />}
      <Table<Scenario>
        rowKey="id"
        loading={listQuery.isLoading}
        columns={columnsByView[view]}
        dataSource={listQuery.data?.items ?? []}
        locale={tableEmpty(emptyText[view])}
        pagination={{
          current: page,
          pageSize,
          total: listQuery.data?.total ?? 0,
          showSizeChanger: true,
          showTotal: (total) => `共 ${total} 个场景`,
          onChange: (nextPage, nextSize) => {
            setPage(nextPage);
            setPageSize(nextSize);
          },
        }}
      />
      {acceptTarget && (
        <AcceptModal
          key={acceptTarget.id}
          scenario={acceptTarget}
          teams={teams}
          onClose={() => setAcceptTarget(null)}
        />
      )}
      {publishOpen && <PublishModal onClose={() => setPublishOpen(false)} />}
      {detailId && <ScenarioDetail scenarioId={detailId} onClose={() => setDetailId(null)} />}
    </Card>
  );
}
