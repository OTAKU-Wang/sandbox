// A264：缺 exit_code 的执行，日志行记「成功」而状态芯片记「失败」——同一个
// detail.exit_code 在 notebookWorkbenchModel.ts 里被两套语义解读（`?? 0` 判成功 vs
// `typeof === 'number'` 判失败）。修法口径：三处共用 exitCodeOf(): number | null，
// null 单独一档显示「退出码未记录」（照抄同文件 formatDuration 的「耗时未记录」口径）。
// 在密态沙箱里，「没拿到退出码」既不能当成功也不能当失败。
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { lastExecutionFromLogs, logLineFromAudit, runStatusView } from '../notebookWorkbenchModel';
import type { SessionLogEntry } from '../../../services/sandboxApi';

function readSourceFromDisk(rel: string): string {
  let dir = process.cwd();
  for (let depth = 0; depth < 5; depth += 1) {
    for (const base of [dir, join(dir, 'cds-frontend')]) {
      const candidate = join(base, rel);
      if (existsSync(candidate)) return readFileSync(candidate, 'utf8');
    }
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new Error(`找不到源文件 ${rel}（cwd=${process.cwd()}）`);
}

const audit = (
  id: string,
  action: string,
  created_at: string | undefined,
  detail: Record<string, unknown> = {},
): SessionLogEntry => ({ id, action, created_at, detail, user_id: 'u-1' }) as SessionLogEntry;

const NO_EXIT = audit('l-1', 'sandbox.execute', '2026-10-17T08:00:00Z', { duration_ms: 400 });

describe('A264 缺 exit_code 的执行：两处读法统一为「退出码未记录」一档', () => {
  it('日志行不得假装成功：tone 为 warn（不是 ok），并如实写「退出码未记录」', () => {
    const line = logLineFromAudit(NO_EXIT);
    expect(line.tone).toBe('warn');
    expect(line.text).toContain('退出码未记录');
    expect(line.problem).toBe(false);
  });

  it('状态芯片同一档：ok 为 null、文案「退出码未记录」，不是「执行成功」也不是「执行失败」', () => {
    const last = lastExecutionFromLogs([NO_EXIT]);
    expect(last).not.toBeNull();
    expect(last!.ok).toBeNull();
    const view = runStatusView(last, null);
    expect(view.tone).toBe('warn');
    expect(view.text).toContain('退出码未记录');
    expect(view.text).not.toContain('执行成功');
    expect(view.text).not.toContain('执行失败');
  });

  it('同一条执行，日志行与状态芯片不得一档绿一档红（tone 必须同为 warn）', () => {
    const line = logLineFromAudit(NO_EXIT);
    const chip = runStatusView(lastExecutionFromLogs([NO_EXIT]), null);
    expect([line.tone, chip.tone]).toEqual(['warn', 'warn']);
  });

  it('非数字 exit_code（字符串「0」这类漂移数据）同样落未记录档，不静默判成功', () => {
    const line = logLineFromAudit(audit('l-2', 'sandbox.execute', '2026-10-17T08:00:01Z', { exit_code: '0' }));
    expect(line.tone).toBe('warn');
    expect(line.text).toContain('退出码未记录');
    expect(runStatusView(lastExecutionFromLogs([audit('l-2', 'sandbox.execute', '2026-10-17T08:00:01Z', { exit_code: '0' })]), null).text)
      .toContain('退出码未记录');
  });

  it('回归锁：exit_code=0 成功档、exit_code=1 失败档、阻断优先，口径不变', () => {
    const ok = audit('l-3', 'sandbox.execute', '2026-10-17T08:00:02Z', { exit_code: 0, duration_ms: 200 });
    expect(logLineFromAudit(ok).tone).toBe('ok');
    expect(logLineFromAudit(ok).text).not.toContain('退出码未记录');
    expect(runStatusView(lastExecutionFromLogs([ok]), null).text).toBe('执行成功 · 0.2s');
    const bad = audit('l-4', 'sandbox.execute', '2026-10-17T08:00:03Z', { exit_code: 1 });
    expect(logLineFromAudit(bad).tone).toBe('error');
    expect(logLineFromAudit(bad).problem).toBe(true);
    expect(runStatusView(lastExecutionFromLogs([bad]), null).text).toContain('执行失败');
    const blocked = audit('l-5', 'sandbox.execute', '2026-10-17T08:00:04Z', { exit_code: 0, output_blocked: true });
    expect(runStatusView(lastExecutionFromLogs([blocked]), null).text).toContain('输出被阻断');
  });

  it('静态闸：exit_code 的解读只有 exitCodeOf 一个口径，toneOf/isProblem/lastExecutionFromLogs 共用', () => {
    const src = readSourceFromDisk('src/pages/Flow/notebookWorkbenchModel.ts');
    expect(src).not.toContain('detail.exit_code ?? 0');
    expect(src).toMatch(/function exitCodeOf|const exitCodeOf/);
    const uses = src.split('exitCodeOf(').length - 1;
    expect(uses).toBeGreaterThanOrEqual(4); // 定义 1 次 + 三处消费
  });
});
