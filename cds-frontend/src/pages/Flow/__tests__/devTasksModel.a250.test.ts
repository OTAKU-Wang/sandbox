/**
 * A250：卡片可以一边贴「已发布生产」一边提示「没有生产版本」（同一屏自相矛盾）。
 * 台账 ocr-verified-findings.md §A250：阶段集 `publishedNotebookIds` 只过
 * `status === Published && source_notebook_id`，**不过 env**；生产执行入口用的
 * `publishedProductionByTask` 过的是 `env !== 'production' || status !== Published` 就跳过，
 * 同一份 workloads 喂两个口径 ⇒ 只发到测试环境的 notebook 被贴「已发布生产」，
 * 同一张卡的生产执行入口却弹 `NO_PRODUCTION_WORKLOAD_REASON`（需先在负载中心发布）。
 *
 * 闸的是修完之后的不变量：**标签断言了 env，就必须与执行入口同口径**——
 * 「贴了已发布生产」⇔「执行入口可用」，rows 视图与卡片视图也不许各说各话。
 */
import { describe, expect, it } from 'vitest';
import type { Notebook, Workload } from '../../../services/flow';
import { WorkloadStatus } from '../../../services/flow';
import { NO_PRODUCTION_WORKLOAD_REASON, buildDevTaskCards, buildDevTaskRows } from '../devTasksModel';

const PUBLISHED_LABEL = '已发布生产';

function notebook(over: Partial<Notebook> = {}): Notebook {
  return {
    id: 'n-1',
    contract_id: 'c-1',
    title: '客单价回归',
    path: '/dev/客单价回归.ipynb',
    kernel: 'python3',
    language: 'python',
    content: null,
    version: 1,
    status: 'active',
    session_id: null,
    execution_backend: null,
    last_executed_at: null,
    created_at: '2026-10-16T00:00:00Z',
    updated_at: '2026-10-16T00:00:00Z',
    ...over,
  };
}

function workload(over: Partial<Workload> = {}): Workload {
  return {
    id: 'w-1',
    name: '客单价生产版',
    contract_id: 'c-1',
    source_notebook_id: 'n-1',
    language: 'python',
    entrypoint_hash: null,
    resource_spec: null,
    env: 'production',
    status: WorkloadStatus.Published,
    version: 1,
    is_latest: true,
    test_run_id: null,
    published_at: '2026-10-16T00:00:00Z',
    created_at: '2026-10-16T00:00:00Z',
    ...over,
  };
}

describe('A250 阶段标签与生产执行入口同口径', () => {
  it('只发到测试环境的 published 负载：不得贴「已发布生产」，执行入口按同一口径禁用', () => {
    const card = buildDevTaskCards([notebook()], [workload({ env: 'test' })])[0];
    expect(card.stage.label).not.toBe(PUBLISHED_LABEL);
    expect(card.exec_disabled_reason).toBe(NO_PRODUCTION_WORKLOAD_REASON);
  });

  it('真·生产 published：标签与执行入口同时成立', () => {
    const card = buildDevTaskCards([notebook()], [workload({ env: 'production' })])[0];
    expect(card.stage.label).toBe(PUBLISHED_LABEL);
    expect(card.exec_disabled_reason).toBeNull();
  });

  it('rows 视图与卡片视图不许各说各话（同一份 workloads）', () => {
    const notebooks = [notebook()];
    const workloads = [workload({ env: 'test' })];
    const row = buildDevTaskRows({ notebooks, workloads, runs: [] }).find((r) => r.key === 'notebook:n-1');
    const card = buildDevTaskCards(notebooks, workloads)[0];
    expect(row?.stage.label).toBe(card.stage.label);
    expect(row?.stage.label).not.toBe(PUBLISHED_LABEL);
  });

  it('不变量：任何 env 组合下，「贴了已发布生产」都蕴含「执行入口可用」', () => {
    const combos = [
      [],
      [workload({ env: 'test' })],
      [workload({ env: 'test', version: 2 })],
      [workload({ env: 'production' })],
      [workload({ env: 'test' }), workload({ env: 'production', id: 'w-2', version: 3 })],
      [workload({ env: 'test', status: WorkloadStatus.Draft })],
    ];
    for (const workloads of combos) {
      const card = buildDevTaskCards([notebook()], workloads)[0];
      if (card.stage.label === PUBLISHED_LABEL) {
        expect(card.exec_disabled_reason, JSON.stringify(workloads.map((w) => [w.env, w.status]))).toBeNull();
      }
      if (card.exec_disabled_reason === NO_PRODUCTION_WORKLOAD_REASON) {
        expect(card.stage.label, JSON.stringify(workloads.map((w) => [w.env, w.status]))).not.toBe(PUBLISHED_LABEL);
      }
    }
  });
});
