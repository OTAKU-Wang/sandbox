import type { ComputeRun, Notebook, Workload } from '../../services/flow';
import { WorkloadStatus } from '../../services/flow';
import { COMPUTE_RUN_STATUS_META, WORKLOAD_STATUS_META } from './display';
import type { StatusMeta } from './display';

export type DevTaskKind = 'notebook' | 'workload' | 'run';

export interface DevTaskRow {
  key: string;
  kind: DevTaskKind;
  name: string;
  stage: StatusMeta;
  contract_id: string | null;
  at: string | null;
  target: string;
}

const KIND_TARGET: Record<DevTaskKind, string> = {
  notebook: '/notebooks',
  workload: '/workloads',
  run: '/compute',
};

const NOTEBOOK_STAGES = {
  published: { color: 'purple', label: '已发布生产' },
  debugging: { color: 'green', label: '调试中' },
  draft: { color: 'default', label: '草稿' },
} satisfies Record<string, StatusMeta>;

function passThrough(table: Record<string, StatusMeta>, value: string): StatusMeta {
  return table[value] ?? { color: 'default', label: value };
}

export function buildDevTaskRows(input: {
  notebooks: Notebook[];
  workloads: Workload[];
  runs: ComputeRun[];
}): DevTaskRow[] {
  const publishedByNotebook = new Set(
    input.workloads
      .filter((workload) => workload.status === WorkloadStatus.Published && workload.source_notebook_id)
      .map((workload) => workload.source_notebook_id as string),
  );

  const rows: DevTaskRow[] = [
    ...input.notebooks.map((notebook): DevTaskRow => ({
      key: `notebook:${notebook.id}`,
      kind: 'notebook',
      name: notebook.title,
      stage: publishedByNotebook.has(notebook.id)
        ? NOTEBOOK_STAGES.published
        : notebook.session_id
          ? NOTEBOOK_STAGES.debugging
          : NOTEBOOK_STAGES.draft,
      contract_id: notebook.contract_id,
      at: notebook.last_executed_at ?? notebook.updated_at,
      target: KIND_TARGET.notebook,
    })),
    ...input.workloads.map((workload): DevTaskRow => ({
      key: `workload:${workload.id}`,
      kind: 'workload',
      name: `${workload.name} v${workload.version}`,
      stage: passThrough(WORKLOAD_STATUS_META, workload.status),
      contract_id: workload.contract_id,
      at: workload.published_at ?? workload.created_at,
      target: KIND_TARGET.workload,
    })),
    ...input.runs.map((run): DevTaskRow => ({
      key: `run:${run.id}`,
      kind: 'run',
      name: run.error_message ? `${run.status}：${run.error_message}` : `执行 ${run.scheduled_for ?? run.started_at ?? run.id}`,
      stage: passThrough(COMPUTE_RUN_STATUS_META, run.status),
      contract_id: run.contract_id,
      at: run.finished_at ?? run.started_at ?? run.scheduled_for,
      target: KIND_TARGET.run,
    })),
  ];

  return rows.sort((left, right) => {
    if (!left.at && !right.at) return left.key.localeCompare(right.key);
    if (!left.at) return 1;
    if (!right.at) return -1;
    return right.at.localeCompare(left.at);
  });
}
