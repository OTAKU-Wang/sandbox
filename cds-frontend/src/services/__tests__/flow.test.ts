import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

import api from '../api';
import {
  buildCurlExample,
  buildFlowConfigPayload,
  buildRequestExample,
  canonicalizeNotebookContent,
  defaultNotebookPath,
  describeFlowError,
  describeRuntimeReason,
  describePublishGateCode,
  flowApi,
  flowConfigToFormValues,
  flattenOpenApiOperations,
  formatBytes,
  normalizeTestRun,
  parseFlowError,
  parseNotebookContent,
  pickCallCount,
  serializeNotebookContent,
  toListPayload,
  validateFlowConfig,
} from '../flow';
import type { FlowConfigFormValues, OpenApiDoc } from '../flow';

const get = vi.mocked(api.get);
const post = vi.mocked(api.post);
const put = vi.mocked(api.put);
const patch = vi.mocked(api.patch);
const del = vi.mocked(api.delete);

const baseForm: FlowConfigFormValues = {
  delivery_methods: ['dataset'],
  auto_compute: true,
  compute_frequency: 'hourly',
  compute_minute: 5,
  compute_hour: 2,
  compute_tz: 'Asia/Shanghai',
};

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  put.mockReset();
  patch.mockReset();
  del.mockReset();
});

describe('flowApi 请求契约', () => {
  it('saveFlowConfig POST 到合约 flow-config 并原样提交载荷', async () => {
    post.mockResolvedValue({ id: 'c-1' });
    const payload = buildFlowConfigPayload(baseForm);
    await flowApi.saveFlowConfig('c-1', payload);
    expect(post).toHaveBeenCalledWith('/contracts/c-1/flow-config', payload);
    expect(payload).toEqual({
      delivery_methods: ['dataset'],
      auto_compute: true,
      compute_frequency: 'hourly',
      compute_minute: 5,
      compute_hour: 2,
      compute_tz: 'Asia/Shanghai',
      sandbox_resource_spec: {},
    });
  });

  it('listNotebooks 省略空 contract_id 并带分页默认值', async () => {
    get.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 });
    await flowApi.listNotebooks({ contract_id: null });
    expect(get).toHaveBeenCalledWith('/notebooks', { params: { contract_id: undefined, page: 1, page_size: 20 } });
  });

  it('notebook CRUD 路径与动词', async () => {
    post.mockResolvedValue({ id: 'n-1' });
    put.mockResolvedValue({ id: 'n-1' });
    del.mockResolvedValue(undefined);
    await flowApi.createNotebook({ title: 't', path: '/p.ipynb' });
    await flowApi.updateNotebook('n-1', { content: 'x' });
    await flowApi.deleteNotebook('n-1');
    expect(post).toHaveBeenLastCalledWith('/notebooks', { title: 't', path: '/p.ipynb' });
    expect(put).toHaveBeenCalledWith('/notebooks/n-1', { content: 'x' });
    expect(del).toHaveBeenCalledWith('/notebooks/n-1');
  });

  it('dev-session / jupyter / run 走 notebook 子路径', async () => {
    post.mockResolvedValue({ session_id: 's-1' });
    get.mockResolvedValue({ jupyter_available: false });
    await flowApi.startDevSession('n-1');
    await flowApi.getJupyterStatus('n-1');
    expect(post).toHaveBeenLastCalledWith('/notebooks/n-1/dev-session');
    expect(get).toHaveBeenLastCalledWith('/notebooks/n-1/jupyter');
  });

  it('单元格执行带 code/cell_index/persist 并放宽超时', async () => {
    post.mockResolvedValue({ ok: true });
    await flowApi.executeCell('n-1', { code: 'print(1)', cell_index: 2, persist: true });
    expect(post).toHaveBeenCalledWith('/notebooks/n-1/cells/execute', { code: 'print(1)', cell_index: 2, persist: true }, { timeout: 120000 });
  });

  it('工作负载测试与全量运行使用长超时', async () => {
    post.mockResolvedValue({});
    await flowApi.runWorkloadTest('w-1');
    await flowApi.runNotebook('n-1');
    expect(post).toHaveBeenNthCalledWith(1, '/workloads/w-1/test', undefined, { timeout: 300000 });
    expect(post).toHaveBeenNthCalledWith(2, '/notebooks/n-1/run', undefined, { timeout: 300000 });
  });

  it('test-runs 同时兼容裸数组与信封并归一化', async () => {
    get.mockResolvedValue([{ test_run_id: 't-1', passed: 1, checks: [{ name: 'A', passed: 0 }] }]);
    const runs = await flowApi.listWorkloadTestRuns('w-1');
    expect(get).toHaveBeenCalledWith('/workloads/w-1/test-runs');
    expect(runs[0]).toMatchObject({ test_run_id: 't-1', passed: true });
    expect(runs[0].checks[0].passed).toBe(false);

    get.mockResolvedValue({ items: { test_run_id: 't-2', check_results: [{ check: 'B', ok: true }] } });
    const single = await flowApi.listWorkloadTestRuns('w-2');
    expect(single).toEqual([]);
  });

  it('发布/退役/回滚走 POST 子路径', async () => {
    post.mockResolvedValue({ id: 'w-1' });
    await flowApi.publishWorkload('w-1');
    await flowApi.retireWorkload('w-1');
    await flowApi.rollbackWorkload('w-1');
    expect(post.mock.calls.map((call) => call[0])).toEqual(['/workloads/w-1/publish', '/workloads/w-1/retire', '/workloads/w-1/rollback']);
  });

  it('compute 调度/触发/取消路径', async () => {
    get.mockResolvedValue({ items: [] });
    post.mockResolvedValue({ id: 'r-1' });
    await flowApi.getSchedule('c-1');
    await flowApi.triggerRun('c-1');
    await flowApi.cancelRun('r-1');
    expect(get).toHaveBeenNthCalledWith(1, '/compute/schedule', { params: { contract_id: 'c-1' } });
    expect(post).toHaveBeenNthCalledWith(1, '/compute/runs', { contract_id: 'c-1' }, { timeout: 120000 });
    expect(post).toHaveBeenNthCalledWith(2, '/compute/runs/r-1/cancel');
  });

  it('成果包下载请求二进制流', async () => {
    get.mockResolvedValue(new Blob(['x']));
    await flowApi.downloadBundle('b-1');
    expect(get).toHaveBeenCalledWith('/deliveries/dataset/b-1/download', { responseType: 'blob', timeout: 120000 });
  });

  it('webhook 增删改与重投路径', async () => {
    post.mockResolvedValue({ secret: 's' });
    patch.mockResolvedValue({ id: 'h-1' });
    del.mockResolvedValue(undefined);
    get.mockResolvedValue([]);
    await flowApi.createWebhook({ name: 'n', url: 'https://x', events: ['compute_run.succeeded'] });
    await flowApi.listWebhooks('c-1');
    await flowApi.updateWebhook('h-1', { status: 'paused' });
    await flowApi.rotateWebhookSecret('h-1');
    await flowApi.redeliver('h-1', 'd-1');
    await flowApi.deleteWebhook('h-1');
    expect(post).toHaveBeenNthCalledWith(1, '/deliveries/webhooks', { name: 'n', url: 'https://x', events: ['compute_run.succeeded'] });
    expect(get).toHaveBeenNthCalledWith(1, '/deliveries/webhooks', { params: { contract_id: 'c-1' } });
    expect(patch).toHaveBeenCalledWith('/deliveries/webhooks/h-1', { status: 'paused' });
    expect(post).toHaveBeenNthCalledWith(2, '/deliveries/webhooks/h-1/rotate-secret');
    expect(post).toHaveBeenNthCalledWith(3, '/deliveries/webhooks/h-1/deliveries/d-1/redeliver');
    expect(del).toHaveBeenCalledWith('/deliveries/webhooks/h-1');
  });

  it('openapi 文档与端点路径', async () => {
    get.mockResolvedValue({ openapi: '3.1.0' });
    post.mockResolvedValue({ id: 'e-1' });
    await flowApi.getContractOpenApiDoc('c-1');
    await flowApi.listEndpoints(null);
    await flowApi.createEndpoint({ contract_id: 'c-1', name: 'n', slug: 's' });
    await flowApi.updateEndpoint('e-1', { status: 'disabled' });
    expect(get).toHaveBeenNthCalledWith(1, '/deliveries/openapi/contract/c-1');
    expect(get).toHaveBeenNthCalledWith(2, '/deliveries/openapi/endpoints', { params: { contract_id: undefined } });
    expect(post).toHaveBeenLastCalledWith('/deliveries/openapi/endpoints', { contract_id: 'c-1', name: 'n', slug: 's' });
    expect(patch).toHaveBeenCalledWith('/deliveries/openapi/endpoints/e-1', { status: 'disabled' });
  });
});

describe('validateFlowConfig', () => {
  const fields = (issues: ReturnType<typeof validateFlowConfig>) => issues.map((issue) => issue.field);

  it('放行合法配置', () => {
    expect(validateFlowConfig(baseForm)).toEqual([]);
  });

  it('分钟越界在客户端即拒绝', () => {
    expect(fields(validateFlowConfig({ ...baseForm, compute_minute: 60 }))).toContain('compute_minute');
    expect(fields(validateFlowConfig({ ...baseForm, compute_minute: -1 }))).toContain('compute_minute');
    expect(fields(validateFlowConfig({ ...baseForm, compute_minute: 5.5 }))).toContain('compute_minute');
  });

  it('每日模式的越界小时被拒绝，每小时模式忽略小时', () => {
    expect(fields(validateFlowConfig({ ...baseForm, compute_frequency: 'daily', compute_hour: 24 }))).toContain('compute_hour');
    expect(validateFlowConfig({ ...baseForm, compute_hour: 24 })).toEqual([]);
  });

  it('开启自动计算却不选频率视为冲突', () => {
    expect(fields(validateFlowConfig({ ...baseForm, compute_frequency: 'manual' }))).toContain('compute_frequency');
  });

  it('关闭自动计算时不要求分钟，且允许不选频率', () => {
    expect(validateFlowConfig({ ...baseForm, auto_compute: false, compute_frequency: 'manual', compute_minute: null })).toEqual([]);
  });

  it('交付方式与时区必填', () => {
    const issues = validateFlowConfig({ ...baseForm, delivery_methods: [], compute_tz: '  ' });
    expect(fields(issues)).toEqual(expect.arrayContaining(['delivery_methods', 'compute_tz']));
  });

  it('资源字段按区间与整数性校验', () => {
    const issues = validateFlowConfig({ ...baseForm, cpu_cores: 128, memory_mb: 64, gpu_count: 1.5, timeout_seconds: 30 });
    const found = fields(issues);
    expect(found).toEqual(expect.arrayContaining(['cpu_cores', 'memory_mb', 'gpu_count', 'timeout_seconds']));
  });

  it('未填写的资源字段不参与校验', () => {
    expect(validateFlowConfig({ ...baseForm, cpu_cores: null, disk_gb: undefined })).toEqual([]);
  });
});

describe('buildFlowConfigPayload / flowConfigToFormValues', () => {
  it('关闭自动计算时频率归 manual 并省略空资源', () => {
    const payload = buildFlowConfigPayload({
      delivery_methods: ['open_api', 'result_push'],
      auto_compute: false,
      compute_frequency: 'daily',
      compute_minute: 30,
      compute_hour: 3,
      compute_tz: '',
      cpu_cores: null,
      memory_mb: undefined,
      gpu_count: 2,
    });
    expect(payload).toEqual({
      delivery_methods: ['open_api', 'result_push'],
      auto_compute: false,
      compute_frequency: 'manual',
      compute_minute: 30,
      compute_hour: 3,
      compute_tz: 'Asia/Shanghai',
      sandbox_resource_spec: { gpu_count: 2 },
    });
  });

  it('镜像模板去空格后写入资源规格，整数资源取整', () => {
    const payload = buildFlowConfigPayload({ ...baseForm, image_template: '  cds-python:3.12  ', memory_mb: 1024.7 });
    expect(payload.sandbox_resource_spec).toEqual({ memory_mb: 1025, image_template: 'cds-python:3.12' });
  });

  it('回读合约配置时补齐默认值', () => {
    const values = flowConfigToFormValues({
      id: 'c-1',
      contract_no: 'CT-1',
      title: 't',
      status: 'active',
      compute_frequency: 'daily',
      compute_minute: 15,
      sandbox_resource_spec: { cpu_cores: 4, timeout_seconds: 900 },
    } as never);
    expect(values).toMatchObject({
      auto_compute: false,
      compute_frequency: 'daily',
      compute_minute: 15,
      compute_hour: 2,
      compute_tz: 'Asia/Shanghai',
      cpu_cores: 4,
      timeout_seconds: 900,
      memory_mb: null,
    });
  });

  it('表单往返保持资源与频率', () => {
    const values: FlowConfigFormValues = { ...baseForm, compute_frequency: 'daily', disk_gb: 40, image_template: 'img' };
    const round = flowConfigToFormValues({ id: 'c', contract_no: '', title: '', status: 'draft', ...buildFlowConfigPayload(values) } as never);
    expect(round).toMatchObject({ compute_frequency: 'daily', disk_gb: 40, image_template: 'img', delivery_methods: ['dataset'] });
  });
});

describe('notebook 内容解析与回写', () => {
  it('ipython JSON 的字符串数组 source 会拼接', () => {
    const parsed = parseNotebookContent(JSON.stringify({ cells: [{ cell_type: 'code', source: ['a=1\n', 'a\n'] }, { source: 'b=2' }] }));
    expect(parsed.format).toBe('ipython');
    expect(parsed.cells).toEqual(['a=1\na\n', 'b=2']);
  });

  it('百分比脚本按分隔行切分', () => {
    const parsed = parseNotebookContent('# %%\nx=1\n\n# %%\ny=2\n');
    expect(parsed.format).toBe('percent');
    expect(parsed.cells).toEqual(['x=1', 'y=2']);
  });

  it('普通脚本身为单个单元格', () => {
    const parsed = parseNotebookContent('print(1)');
    expect(parsed.format).toBe('plain');
    expect(parsed.cells).toEqual(['print(1)']);
  });

  it('空内容仍可编辑', () => {
    expect(parseNotebookContent('   ').cells).toEqual(['']);
  });

  it('非法 JSON 前缀按脚本处理而不是崩溃', () => {
    expect(parseNotebookContent('{not json').format).toBe('plain');
  });

  it('ipython 回写保留 notebook 其余字段', () => {
    const source = JSON.stringify({ metadata: { kernelspec: { name: 'python3' } }, cells: [{ cell_type: 'code', source: 'a=1', outputs: [1] }] });
    const parsed = parseNotebookContent(source);
    const next = JSON.parse(serializeNotebookContent(parsed, ['a=2'])) as Record<string, unknown>;
    expect(next.metadata).toEqual({ kernelspec: { name: 'python3' } });
    expect((next.cells as Record<string, unknown>[])[0]).toMatchObject({ cell_type: 'code', source: 'a=2', outputs: [1] });
  });

  it('百分比格式往返保持单元格数', () => {
    const parsed = parseNotebookContent('# %%\nx=1\n\n# %%\ny=2\n');
    const serialized = serializeNotebookContent(parsed, ['x=1', 'y=2\nz=3']);
    expect(parseNotebookContent(serialized).cells).toEqual(['x=1', 'y=2\nz=3']);
  });

  it('后端返回的文档对象可直接解析', () => {
    const parsed = parseNotebookContent({ nbformat: 4, cells: [{ cell_type: 'code', source: ['a=1\n', 'a\n'] }] });
    expect(parsed.format).toBe('ipython');
    expect(parsed.cells).toEqual(['a=1\na\n']);
  });

  it('非 ipython 来源也回写成 nbformat 4 文档', () => {
    const parsed = parseNotebookContent('# %%\nx=1\n\n# %%\ny=2\n');
    const doc = JSON.parse(serializeNotebookContent(parsed, ['x=1', 'y=2'])) as Record<string, unknown>;
    expect(doc.nbformat).toBe(4);
    expect((doc.cells as Record<string, unknown>[]).map((cell) => cell.source)).toEqual(['x=1', 'y=2']);
  });

  it('规范文本对文档与它的 JSON 字符串幂等', () => {
    const doc = { nbformat: 4, metadata: {}, cells: [{ cell_type: 'code', source: 'a=1', outputs: [], execution_count: null }] };
    expect(canonicalizeNotebookContent(doc)).toBe(canonicalizeNotebookContent(JSON.stringify(doc)));
  });

  it('按标题生成安全路径', () => {
    expect(defaultNotebookPath('Churn Explore!')).toBe('/nb/churn-explore.ipynb');
    expect(defaultNotebookPath('   ')).toBe('/nb/untitled.ipynb');
  });
});

describe('W2 错误信封', () => {
  it('解析 detail 对象/数组并保留 request_id 与状态码', () => {
    const info = parseFlowError({
      response: {
        status: 412,
        data: { code: 'WORKLOAD_HASH_MISMATCH', message: '前置校验未通过', detail: { reason: 'hash differs' }, request_id: 'req-9' },
      },
    });
    expect(info).toEqual({
      code: 'WORKLOAD_HASH_MISMATCH',
      message: '前置校验未通过',
      detail: '{"reason":"hash differs"}',
      reason: 'hash differs',
      requestId: 'req-9',
      httpStatus: 412,
    });
  });

  it('detail 数组会拼接为可读串', () => {
    const info = parseFlowError({ code: 'X', message: 'm', detail: ['a', 'b'] });
    expect(info.detail).toBe('a；b');
  });

  it('detail 优先于 message，并附带错误码', () => {
    expect(describeFlowError({ code: 'E1', message: '粗粒度', detail: '细粒度原因' })).toBe('细粒度原因（E1）');
    expect(describeFlowError({ message: '只有消息' })).toBe('只有消息');
    expect(describeFlowError(new Error('网络中断'))).toBe('网络中断');
    expect(describeFlowError(null, '兜底文案')).toBe('兜底文案');
  });

  it('已含错误码时不重复追加', () => {
    expect(describeFlowError({ code: 'E2', message: '失败：E2' })).toBe('失败：E2');
  });

  it('detail.reason 已知时给人话文案，未知时退回原始 detail', () => {
    const mapped = describeFlowError({
      code: 'RUNTIME_UNAVAILABLE',
      message: "L2' (cube) data plane is not enabled",
      detail: { reason: 'dataplane_disabled' },
    });
    expect(mapped).toContain('MicroVM');
    expect(mapped).toContain('（RUNTIME_UNAVAILABLE）');
    expect(mapped).not.toContain('dataplane_disabled');

    expect(
      describeFlowError({ code: 'E3', message: 'm', detail: { reason: 'brand_new_reason' } }),
    ).toContain('brand_new_reason');
    expect(describeRuntimeReason('no_ready_node')).toBeTruthy();
    expect(describeRuntimeReason(null)).toBeNull();
  });

  it('发布门错误码有中文解释，未知码返回 null', () => {
    expect(describePublishGateCode('WORKLOAD_TEST_REQUIRED')).toBeTruthy();
    expect(describePublishGateCode('WORKLOAD_TEST_NOT_PASSED')).toBeTruthy();
    expect(describePublishGateCode('WORKLOAD_HASH_MISMATCH')).toBeTruthy();
    expect(describePublishGateCode('WHATEVER')).toBeNull();
    expect(describePublishGateCode(null)).toBeNull();
  });
});

describe('列表边界归一化', () => {
  it('裸数组补全信封字段', () => {
    expect(toListPayload([1, 2, 3])).toEqual({ items: [1, 2, 3], total: 3, page: 1, page_size: 3 });
  });

  it('缺字段信封回退到条目数', () => {
    expect(toListPayload({ items: [{ a: 1 }] })).toEqual({ items: [{ a: 1 }], total: 1, page: 1, page_size: 1 });
  });

  it('非预期负载不抛错', () => {
    expect(toListPayload(undefined).items).toEqual([]);
  });

  it('检查项字段别名与数值布尔被归一', () => {
    const run = normalizeTestRun({ id: 't-9', check_results: [{ check: 'A', ok: 1 }, { name: 'B', passed: 0, message: 'boom' }], passed: 0 });
    expect(run).toMatchObject({ test_run_id: 't-9', passed: false, status: 'failed' });
    expect(run.checks).toEqual([
      { name: 'A', passed: true, detail: null },
      { name: 'B', passed: false, detail: 'boom' },
    ]);
  });
});

describe('OpenAPI 文档可读化', () => {
  const doc: OpenApiDoc = {
    openapi: '3.1.0',
    info: { title: '合约交付 API', version: '1.0.0' },
    paths: {
      '/open/v1/churn-score': {
        post: {
          summary: '查询流失评分',
          parameters: [{ name: 'trace', in: 'header' }, { in: 'query' }],
          requestBody: {
            required: true,
            content: {
              'application/json': {
                schema: { type: 'object', properties: { customer_id: { type: 'string' }, top_n: { type: 'integer', format: 'int32' } }, required: ['customer_id'] },
              },
            },
          },
          responses: { '200': { description: 'ok' }, '429': { description: 'limit' } },
        },
        trace: { summary: 'should be ignored' },
      },
    },
  };

  it('摊平为可渲染行并忽略非 HTTP 方法', () => {
    const rows = flattenOpenApiOperations(doc);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ method: 'POST', path: '/open/v1/churn-score', requestBodyRequired: true });
    expect(rows[0].bodyFields).toEqual([
      { name: 'customer_id', type: 'string', required: true },
      { name: 'top_n', type: 'integer(int32)', required: false },
    ]);
    expect(rows[0].parameters).toEqual(['header: trace']);
    expect(rows[0].responseCodes).toEqual(['200', '429']);
  });

  it('空文档返回空行', () => {
    expect(flattenOpenApiOperations(null)).toEqual([]);
    expect(flattenOpenApiOperations({})).toEqual([]);
  });

  it('示例请求体只取文档声明的字段', () => {
    const row = flattenOpenApiOperations(doc)[0];
    expect(buildRequestExample(row)).toEqual({ customer_id: '<必填>', top_n: 0 });
  });

  it('文档缺 schema 时回退端点 request_schema', () => {
    expect(buildRequestExample(undefined, { request_schema: { properties: { q: { type: 'boolean' } } } } as never)).toEqual({ q: false });
    expect(buildRequestExample(undefined, null)).toEqual({});
  });

  it('curl 示例使用外部路径与鉴权头', () => {
    const curl = buildCurlExample('churn-score', { customer_id: 'c-1' }, 'https://cds.example.com/');
    expect(curl).toContain("curl -X POST 'https://cds.example.com/open/v1/churn-score'");
    expect(curl).toContain("-H 'Authorization: Bearer <平台 API Key>'");
    expect(curl).toContain('-H \'Content-Type: application/json\'');
    expect(curl).toContain('customer_id');
  });

  it('curl 示例转义单引号', () => {
    expect(buildCurlExample('s', { v: "a'b" }, 'http://h')).toContain("'\\''");
  });

  it('调用量取后端实际下发的字段，缺失时不臆造', () => {
    expect(pickCallCount({ request_count: 7, call_count: 3 })).toBe(3);
    expect(pickCallCount({ used: 0 })).toBe(0);
    expect(pickCallCount({ quota_total: 100 })).toBeNull();
    expect(pickCallCount(null)).toBeNull();
  });
});

describe('formatBytes', () => {
  it('按量级切换单位', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(999)).toBe('999 B');
    expect(formatBytes(1024)).toBe('1.0 KB');
    expect(formatBytes(1536)).toBe('1.5 KB');
    expect(formatBytes(5 * 1024 * 1024)).toBe('5.0 MB');
    expect(formatBytes(3 * 1024 ** 3)).toBe('3.0 GB');
  });

  it('未知大小不显示数字', () => {
    expect(formatBytes(null)).toBe('-');
    expect(formatBytes(Number.NaN)).toBe('-');
  });
});
