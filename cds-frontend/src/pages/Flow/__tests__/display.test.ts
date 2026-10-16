import { describe, it, expect } from 'vitest';
import {
  COMPUTE_RUN_STATUS_META,
  DELIVERY_STATUS_META,
  WORKLOAD_STATUS_META,
  attemptLabel,
  backendFromCellResult,
  checkSummary,
  evaluatePublishGate,
  openApiPathForSlug,
  publishGateReason,
  resolveBackendView,
  responseStatusColor,
  statusMeta,
  toOutputPane,
} from '../display';
import type { CellExecuteResult, Workload, WorkloadTestRun } from '../../../services/flow';

const workload = (overrides: Partial<Workload> = {}): Workload => ({
  id: 'w-1',
  name: 'w',
  contract_id: 'c-1',
  source_notebook_id: null,
  language: 'python',
  entrypoint_hash: 'hash-1',
  resource_spec: null,
  env: 'test',
  status: 'draft',
  version: 1,
  is_latest: true,
  test_run_id: null,
  published_at: null,
  created_at: '2026-10-15T00:00:00Z',
  ...overrides,
});

const run = (overrides: Partial<WorkloadTestRun> = {}): WorkloadTestRun => ({
  test_run_id: 't-1',
  status: 'passed',
  passed: true,
  checks: [],
  ...overrides,
});

const cellResult = (overrides: Partial<CellExecuteResult> = {}): CellExecuteResult => ({
  ok: true,
  backend: 'cds_kernel',
  stateful: true,
  stdout: '',
  stderr: '',
  repr: null,
  exit_code: 0,
  output_blocked: false,
  ...overrides,
});

describe('statusMeta', () => {
  it('命中登记表时给出颜色与标签', () => {
    expect(statusMeta(WORKLOAD_STATUS_META, 'test_passed')).toEqual(WORKLOAD_STATUS_META.test_passed);
    expect(statusMeta(COMPUTE_RUN_STATUS_META, 'skipped_overlap')).toEqual(COMPUTE_RUN_STATUS_META.skipped_overlap);
    expect(statusMeta(DELIVERY_STATUS_META, 'dead')).toEqual(DELIVERY_STATUS_META.dead);
  });

  it('未登记的取值原样透传，不落入已知标签', () => {
    const meta = statusMeta(WORKLOAD_STATUS_META, 'queued_by_backend');
    expect(meta.label).toBe('queued_by_backend');
    expect(meta.color).toBe('default');
  });

  it('空值有占位标签', () => {
    expect(statusMeta(WORKLOAD_STATUS_META, null).label).toBe('-');
    expect(statusMeta(WORKLOAD_STATUS_META, undefined).label).toBe('-');
  });

  it('每个状态表都覆盖契约枚举', () => {
    expect(Object.keys(WORKLOAD_STATUS_META).sort()).toEqual(['draft', 'published', 'retired', 'test_failed', 'test_passed', 'testing']);
    expect(Object.keys(DELIVERY_STATUS_META).sort()).toEqual(['dead', 'delivered', 'delivering', 'failed', 'pending']);
    expect(Object.keys(COMPUTE_RUN_STATUS_META).sort()).toEqual([
      'cancelled',
      'failed',
      'pending',
      'running',
      'skipped_overlap',
      'succeeded',
    ]);
  });
});

describe('resolveBackendView', () => {
  it('cds_kernel 判为有状态且不降级', () => {
    const view = resolveBackendView({ backend: 'cds_kernel' });
    expect(view).toMatchObject({ stateful: true, degraded: false, known: true });
    expect(view.reasons).toEqual([]);
  });

  it('cds_stateless 判为降级并透传后端原因', () => {
    const view = resolveBackendView({ backend: 'cds_stateless', degraded_reason: 'kernel died' });
    expect(view).toMatchObject({ degraded: true, stateful: false, known: true });
    expect(view.reasons).toEqual(['kernel died']);
  });

  it('degraded_reason 缺失时改用 kernel_error', () => {
    expect(resolveBackendView({ backend: 'cds_stateless', kernel_error: 'boom' }).reasons).toEqual(['boom']);
  });

  it('两者都为空时声明后端未给出原因而不是留白', () => {
    const view = resolveBackendView({ backend: 'cds_stateless' });
    expect(view.degraded).toBe(true);
    expect(view.reasons).toHaveLength(1);
    expect(view.reasons[0]).toBeTruthy();
  });

  it('后端 stateful=false 即使 backend 字段是内核也按降级披露', () => {
    const view = resolveBackendView({ backend: 'cds_kernel', stateful: false });
    expect(view.degraded).toBe(true);
  });

  it('未登记的后端标识不推断有状态性', () => {
    const view = resolveBackendView({ backend: 'mystery_backend' });
    expect(view.known).toBe(false);
    expect(view.degraded).toBe(false);
    expect(view.label).toBe('mystery_backend');
  });

  it('缺后端标识时归为未知且不编造标签', () => {
    const view = resolveBackendView({ backend: null });
    expect(view.backend).toBe('unknown');
    expect(view.known).toBe(false);
  });

  it('单元格结果直接复用同一判定', () => {
    expect(backendFromCellResult(cellResult({ backend: 'cds_stateless', stateful: false })).degraded).toBe(true);
  });
});

describe('toOutputPane', () => {
  it('无结果时不渲染面板', () => {
    expect(toOutputPane(null)).toBeNull();
    expect(toOutputPane(undefined)).toBeNull();
  });

  it('分栏输出并保留退出码与阻断原因', () => {
    const pane = toOutputPane(
      cellResult({ stdout: 'out', stderr: 'err', repr: '3', exit_code: 1, output_blocked: true, blocked_reason: 'policy: no csv' }),
    );
    expect(pane).toMatchObject({ stdout: 'out', stderr: 'err', repr: '3', exitCode: 1, blocked: true, blockedReason: 'policy: no csv', hasContent: true });
  });

  it('阻断但无原因时如实标记缺省', () => {
    const pane = toOutputPane(cellResult({ output_blocked: true }));
    expect(pane?.blockedReason).toBeNull();
    expect(pane?.hasContent).toBe(true);
  });

  it('空执行结果标记为无内容', () => {
    expect(toOutputPane(cellResult())?.hasContent).toBe(false);
  });
});

describe('evaluatePublishGate', () => {
  it('未选择负载时不可发布', () => {
    expect(evaluatePublishGate(null)).toEqual({ canPublish: false, reason: expect.any(String), warning: null });
  });

  it('无测试记录时给出 WORKLOAD_TEST_REQUIRED 的人话原因', () => {
    const gate = evaluatePublishGate(workload({ status: 'draft' }));
    expect(gate.canPublish).toBe(false);
    expect(gate.reason).toBe(publishGateReason('WORKLOAD_TEST_REQUIRED'));
  });

  it('负载状态为测试通过时放行', () => {
    expect(evaluatePublishGate(workload({ status: 'test_passed' })).canPublish).toBe(true);
  });

  it('最新测试通过也放行', () => {
    expect(evaluatePublishGate(workload({ status: 'draft' }), run({ passed: true })).canPublish).toBe(true);
  });

  it('最新测试失败优先于负载状态', () => {
    const gate = evaluatePublishGate(workload({ status: 'test_passed' }), run({ passed: false, status: 'failed' }));
    expect(gate.canPublish).toBe(false);
    expect(gate.reason).toBe(publishGateReason('WORKLOAD_TEST_NOT_PASSED'));
  });

  it('测试失败状态不可发布', () => {
    expect(evaluatePublishGate(workload({ status: 'test_failed' })).reason).toBe(publishGateReason('WORKLOAD_TEST_NOT_PASSED'));
  });

  it('测试进行中不允许抢跑', () => {
    expect(evaluatePublishGate(workload({ status: 'testing' }), run({ passed: true })).canPublish).toBe(false);
  });

  it('已退役与已是生产版本都拒绝再次发布', () => {
    expect(evaluatePublishGate(workload({ status: 'retired' })).canPublish).toBe(false);
    expect(evaluatePublishGate(workload({ status: 'published', env: 'production' })).canPublish).toBe(false);
  });

  it('负载记录的测试编号与最新记录不一致时给出哈希不一致预警', () => {
    const gate = evaluatePublishGate(workload({ status: 'test_passed', test_run_id: 't-old' }), run({ test_run_id: 't-new', passed: true }));
    expect(gate.canPublish).toBe(true);
    expect(gate.warning).toContain('WORKLOAD_HASH_MISMATCH');
  });

  it('测试编号一致时无预警', () => {
    const gate = evaluatePublishGate(workload({ status: 'test_passed', test_run_id: 't-1' }), run({ test_run_id: 't-1' }));
    expect(gate.warning).toBeNull();
  });
});

describe('publishGateReason', () => {
  it('三个门禁码都有中文解释', () => {
    for (const code of ['WORKLOAD_TEST_REQUIRED', 'WORKLOAD_TEST_NOT_PASSED', 'WORKLOAD_HASH_MISMATCH']) {
      expect(publishGateReason(code)).toBeTruthy();
    }
  });

  it('未知码保留原始码便于报障', () => {
    expect(publishGateReason('SOMETHING_NEW')).toContain('SOMETHING_NEW');
  });

  it('无错误码时仍有兜底说明', () => {
    expect(publishGateReason(null)).toBeTruthy();
  });
});

describe('checkSummary', () => {
  it('统计未通过项', () => {
    const summary = checkSummary(
      run({
        checks: [
          { name: 'A', passed: true },
          { name: 'B', passed: false },
          { name: 'C', passed: false },
        ],
      }),
    );
    expect(summary).toEqual({ total: 3, failed: ['B', 'C'] });
  });

  it('无测试记录时计数为 0', () => {
    expect(checkSummary(null)).toEqual({ total: 0, failed: [] });
  });
});

describe('投递辅助', () => {
  it('响应码按区段着色', () => {
    expect(responseStatusColor(204)).toBe('green');
    expect(responseStatusColor(302)).toBe('blue');
    expect(responseStatusColor(429)).toBe('orange');
    expect(responseStatusColor(503)).toBe('red');
    expect(responseStatusColor(null)).toBe('default');
  });

  it('尝试次数带最大重试', () => {
    expect(attemptLabel(2, 3)).toBe('2/3');
    expect(attemptLabel(1, null)).toBe('1');
    expect(attemptLabel(undefined, 3)).toBe('-');
  });

  it('外部调用路径固定前缀', () => {
    expect(openApiPathForSlug('churn-score')).toBe('/open/v1/churn-score');
  });
});
