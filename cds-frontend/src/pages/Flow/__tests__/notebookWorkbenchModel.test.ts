// SS-23「调试环境」外框的纯逻辑层测试：原型逐字文案 + 每个槽位的降级口径。
//
// 这里的断言刻意「锁字面量」：图 1 的标题、副标题、工具条五项、页签名都是逐字契约，
// 改动必须连带改图，所以让它们红得有原因；同时锁住「没有后端字段就绝不出现数字」这条铁律。
import { describe, expect, it } from 'vitest';
import {
  DISK_QUOTA_SCOPE_HINT,
  NBW_BACK,
  NBW_FILE_PANEL,
  NBW_MENUS,
  NBW_NOT_MEASURED,
  NBW_NOT_RUN,
  NBW_NOT_WIRED,
  NBW_SUBTITLE,
  NBW_TAB_OUTPUT,
  NBW_TAB_TERMINAL,
  NBW_TITLE,
  TERMINAL_TAB_NOTE,
  applyViewCleared,
  buildLogLines,
  clockOf,
  connectionState,
  cursorText,
  detectEol,
  fileBasename,
  formatBytes,
  formatDuration,
  gibFromMb,
  isAliveSessionStatus,
  isRunningSessionStatus,
  jupyterEntryToNode,
  lastExecutionFromLogs,
  logLineFromAudit,
  runStatusView,
  sessionFilesToTree,
  summarizeFiles,
  type WorkbenchLogLine,
} from '../notebookWorkbenchModel';
import type { SessionLogEntry } from '../../../services/sandboxApi';
import type { JupyterContentEntry } from '../../../services/jupyterContents';
import { UNKNOWN_TEXT } from '../../../services/flow';

const audit = (
  id: string,
  action: string,
  created_at: string,
  detail: Record<string, unknown> = {},
): SessionLogEntry => ({ id, action, created_at, detail, user_id: 'u-1' });

const jEntry = (
  over: Partial<JupyterContentEntry> & Pick<JupyterContentEntry, 'name' | 'path' | 'isDirectory'>,
): JupyterContentEntry => ({
  type: over.isDirectory ? 'directory' : 'file',
  size: null,
  lastModified: null,
  children: null,
  ...over,
});

describe('图 1 外框的逐字文案契约', () => {
  it('标题卡：调试环境 + 那句副标题 + 只有一句「返回列表」', () => {
    expect(NBW_TITLE).toBe('调试环境');
    expect(NBW_SUBTITLE).toBe('交付场景 / 数据沙箱开发 · 配置合约、资源与关联数据后即可启动调试');
    expect(NBW_BACK).toBe('返回列表');
  });

  it('左栏标题、工具条五项、输出页签三项逐字且顺序固定', () => {
    expect(NBW_FILE_PANEL).toBe('文件目录');
    expect([...NBW_MENUS]).toEqual(['文件', '编辑', '查看', '语言', '运行']);
    expect(NBW_TAB_OUTPUT).toBe('输出');
    expect(NBW_TAB_TERMINAL).toBe('终端');
  });

  it('降级词是固定字面量（测试与页面共用同一常量，不允许就地编文案）', () => {
    expect(NBW_NOT_MEASURED).toBe('未计量');
    expect(NBW_NOT_WIRED).toBe('未接入');
    expect(NBW_NOT_RUN).toBe('尚未执行');
  });

  it('口径提示里写清分母来源与后端字段名，而不是含糊一句「配额」', () => {
    expect(DISK_QUOTA_SCOPE_HINT).toContain('/deliveries/quota-usage');
    expect(DISK_QUOTA_SCOPE_HINT).toContain('tenant_resources.limits.disk_mb');
    expect(TERMINAL_TAB_NOTE).toContain('/sandbox-sessions/{id}/exec');
  });
});

describe('会话文件清单 → 左栏树（图 1 §3.1）', () => {
  it('以后端真实键 filename 为准，name 只是旧前端口径的兜底', () => {
    const tree = sessionFilesToTree([
      { filename: 'b.csv', size: 10 },
      { name: 'a.py', size: 20 },
      { size: 5 },
    ]);
    expect(tree.map((node) => node.name)).toEqual(['', 'a.py', 'b.csv']);
    expect(tree.every((node) => node.isDirectory === false)).toBe(true);
    expect(tree.every((node) => node.downloadable)).toBe(true);
  });

  it('后端没给 size 就是 null，不拿 0 顶替（否则「占用」会假报 0 B）', () => {
    const tree = sessionFilesToTree([{ filename: 'x.txt' }]);
    expect(tree[0].size).toBeNull();
    expect(summarizeFiles(tree).bytes).toBe(0);
  });

  it('统计只累加文件字节；目录自己的 size（Jupyter 里是子项个数）不进账', () => {
    const nodes = [
      jupyterEntryToNode(
        jEntry({
          name: 'out',
          path: 'out',
          isDirectory: true,
          size: 2,
          children: [
            jEntry({ name: 'a.txt', path: 'out/a.txt', isDirectory: false, size: 1024 }),
            jEntry({ name: 'b.txt', path: 'out/b.txt', isDirectory: false, size: 2048 }),
          ],
        }),
      ),
      jupyterEntryToNode(jEntry({ name: 'top.txt', path: 'top.txt', isDirectory: false, size: 512 })),
    ];
    const summary = summarizeFiles(nodes);
    expect(summary).toEqual({ count: 3, bytes: 3584, partial: false });
  });

  it('开发环目录 downloadable=false：勾选框在本页没有对应动作', () => {
    const node = jupyterEntryToNode(jEntry({ name: 'n.ipynb', path: 'n.ipynb', isDirectory: false, size: 9 }));
    expect(node.downloadable).toBe(false);
    expect(node.key).toBe('j:n.ipynb');
  });

  it('字节与配额格式化：没有配额就回「未记录」，不猜 2 GB', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(3072)).toBe('3.0 KB');
    expect(formatBytes(20480 * 1024 * 1024)).toBe('20.0 GB');
    expect(gibFromMb(20480)).toBe('20 G');
    expect(gibFromMb(512)).toBe('0.5 G');
    expect(gibFromMb(null)).toBe(UNKNOWN_TEXT);
    expect(gibFromMb(0)).toBe(UNKNOWN_TEXT);
  });
});

describe('沙箱连接态（图 1 §3.2 右侧那颗点）', () => {
  it('只有后端 status=running 才画绿点；ready/provisioning 只是活着', () => {
    expect(isRunningSessionStatus('running')).toBe(true);
    expect(isRunningSessionStatus('ready')).toBe(false);
    expect(isAliveSessionStatus('ready')).toBe(true);
    expect(isAliveSessionStatus('terminated')).toBe(false);
  });

  it('没有会话 / 查询失败 / 终态都算未连接，绝不画绿点', () => {
    expect(connectionState({ sessionId: null, status: null }).label).toBe('● 沙箱未连接');
    expect(connectionState({ sessionId: 's-1', status: null, error: new Error('boom') }).tone).toBe('off');
    expect(connectionState({ sessionId: 's-1', status: 'terminated' }).label).toBe('● 沙箱未连接');
    expect(connectionState({ sessionId: 's-1', status: 'ready' }).tone).toBe('warn');
    expect(connectionState({ sessionId: 's-1', status: null, loading: true }).tone).toBe('unknown');
  });

  it('running 但上次执行落在 cds_stateless：连接点是黄的并说明降级原因', () => {
    const ok = connectionState({ sessionId: 's-1', status: 'running' });
    expect(ok).toMatchObject({ tone: 'ok', label: '● 沙箱已连接' });
    const warn = connectionState({
      sessionId: 's-1',
      status: 'running',
      degradedBackend: 'cds_stateless',
      degradedReason: 'kernel daemon 未就绪',
    });
    expect(warn.tone).toBe('warn');
    expect(warn.label).toBe('● 沙箱已连接（执行降级）');
    expect(warn.hint).toContain('kernel daemon 未就绪');
  });
});

describe('审计流 → 日志正文与执行态（图 1 §3.3）', () => {
  it('一条审计事件渲染成 [时钟] 语义 · 关键字段，签名这类噪声不进正文', () => {
    const line = logLineFromAudit(
      audit('l-1', 'sandbox.execute', '2026-10-17T12:00:00', {
        language: 'python',
        exit_code: 0,
        duration_ms: 200,
        _sm2_signature: 'deadbeef',
      }),
    );
    expect(line.clock).toBe('12:00:00');
    expect(line.text).toContain('language=python');
    expect(line.text).toContain('exit_code=0');
    expect(line.text).toContain('duration_ms=200');
    expect(line.text).not.toContain('deadbeef');
    expect(line.tone).toBe('ok');
    expect(line.problem).toBe(false);
  });

  it('A265 detail 走白名单：内容型字段（命令原文/SQL/路径/参数）绝不拼进正文', () => {
    const line = logLineFromAudit(
      audit('l-10', 'sandbox.exec', '2026-10-17T12:00:10', {
        command_preview: 'cat /workspace/customer_pii.csv',
        command_sha256: 'a'.repeat(64),
        sql: 'SELECT id_card FROM guests',
        query: 'hunter2',
        path: '/etc/cds/db.dsn',
        params: { inline: 'plain-secret' },
        error: 'connection refused at 10.20.30.40:5432/cds',
        timeout_seconds: 30,
        exit_code: 0,
      }),
    );
    expect(line.text).not.toContain('customer_pii');
    expect(line.text).not.toContain('SELECT');
    expect(line.text).not.toContain('hunter2');
    expect(line.text).not.toContain('db.dsn');
    expect(line.text).not.toContain('plain-secret');
    expect(line.text).not.toContain('10.20.30.40');
    // 展示字段照常可见，隐藏的东西必须说出来（不静默当成「detail 是空的」）
    expect(line.text).toContain('exit_code=0');
    expect(line.text).toContain('timeout_seconds=30');
    expect(line.text).toMatch(/未展示/);
  });

  it('A265 全字段都被隐藏时仍写清「有内容但没展示」，不是只剩动作名', () => {
    const line = logLineFromAudit(audit('l-11', 'sandbox.exec', '2026-10-17T12:00:11', { command_preview: 'ls -l' }));
    expect(line.text).not.toContain('ls -l');
    expect(line.text).toMatch(/未展示/);
  });

  it('退出码非 0 是 error 且计入「问题」页签；未登记的动作名原样透出', () => {
    const bad = logLineFromAudit(audit('l-2', 'sandbox.execute', '2026-10-17T12:00:01', { exit_code: 1 }));
    expect(bad.tone).toBe('error');
    expect(bad.problem).toBe(true);
    expect(logLineFromAudit(audit('l-3', 'tenant.custom_action', '2026-10-17T12:00:02')).text).toContain('tenant.custom_action');
  });

  it('正文按时间升序；本地补充行只填审计流还没回的那段窗口', () => {
    const backend = [
      audit('l-2', 'sandbox.execute', '2026-10-17T08:00:05Z'),
      audit('l-1', 'sandbox.create', '2026-10-17T08:00:00Z'),
    ];
    const localNew: WorkbenchLogLine = { key: 'x-new', at: '2026-10-17T08:00:09Z', clock: '16:00:09', text: '本页执行完成', tone: 'ok', problem: false };
    const localOld: WorkbenchLogLine = { key: 'x-old', at: '2026-10-17T08:00:01Z', clock: '16:00:01', text: '重复行', tone: 'info', problem: false };
    const lines = buildLogLines(backend, [localOld, localNew]);
    expect(lines.map((line) => line.key)).toEqual(['a:l-1', 'a:l-2', 'x-new']);
  });

  it('「清空视图」只保留更晚的事件，且不改数组元素本身', () => {
    const lines = buildLogLines([audit('l-1', 'sandbox.create', '2026-10-17T08:00:00Z'), audit('l-2', 'sandbox.execute', '2026-10-17T08:00:05Z')], []);
    expect(applyViewCleared(lines, null)).toHaveLength(2);
    expect(applyViewCleared(lines, lines[0].at).map((line) => line.key)).toEqual(['a:l-2']);
  });

  it('执行态取审计流最后一条 execute：成功带真耗时，失败/阻断各有口径', () => {
    const logs = [
      audit('l-1', 'sandbox.execute', '2026-10-17T08:00:00Z', { exit_code: 1, duration_ms: 900 }),
      audit('l-2', 'sandbox.file_upload', '2026-10-17T08:00:03Z', { size: 10 }),
      audit('l-3', 'sandbox.execute', '2026-10-17T08:00:09Z', { exit_code: 0, duration_ms: 6400 }),
    ];
    const last = lastExecutionFromLogs(logs);
    expect(last).toEqual({ ok: true, at: '2026-10-17T08:00:09Z', durationMs: 6400, blocked: false });
    expect(runStatusView(last, null)).toMatchObject({ tone: 'ok', text: '执行成功 · 6.4s' });
    expect(runStatusView({ ok: false, at: null, durationMs: 900, blocked: false }, null).text).toBe('执行失败 · 0.9s');
    expect(runStatusView({ ok: true, at: null, durationMs: null, blocked: true }, null).text).toBe('输出被阻断 · 耗时未记录');
    expect(lastExecutionFromLogs([audit('l-9', 'sandbox.create', '2026-10-17T08:00:00Z')])).toBeNull();
  });

  it('A267 created_at 缺失/不可解析的执行绝不排到「最近一次」前面', () => {
    const timed = audit('l-20', 'sandbox.execute', '2026-10-17T08:00:00Z', { exit_code: 0, duration_ms: 100 });
    const noTime = audit('l-21', 'sandbox.execute', undefined as unknown as string, { exit_code: 3, duration_ms: 200 });
    const garbage = audit('l-22', 'sandbox.execute', '刚刚', { exit_code: 4, duration_ms: 300 });
    // 无时间行排在哪一档都不该赢过一条带真时间的行
    expect(lastExecutionFromLogs([timed, noTime])).toMatchObject({ ok: true, at: '2026-10-17T08:00:00Z' });
    expect(lastExecutionFromLogs([noTime, timed])).toMatchObject({ at: '2026-10-17T08:00:00Z' });
    expect(lastExecutionFromLogs([timed, garbage])).toMatchObject({ at: '2026-10-17T08:00:00Z' });
    // 一条时间都没有时仍然如实报这条执行（at=null → 界面写「时间未记录」），但不编时间
    expect(lastExecutionFromLogs([noTime])).toEqual({ ok: false, at: null, durationMs: 200, blocked: false });
    expect(runStatusView(lastExecutionFromLogs([noTime]), null).hint).toContain('时间未记录');
  });

  it('没有后端记录时用本页真实回调兜底；回调也没结果就写「结果未记录」', () => {
    expect(runStatusView(null, { ok: true, durationMs: 210, label: '单元格 #1', at: 'x' }).text).toBe('执行成功 · 0.2s');
    expect(runStatusView(null, { ok: null, durationMs: null, label: '全部 2 个单元格', at: null }).text).toBe('全部 2 个单元格 · 结果未记录');
    const idle = runStatusView(null, null);
    expect(idle.tone).toBe('idle');
    expect(idle.text).toBe(NBW_NOT_RUN);
    expect(idle.hint.length).toBeGreaterThan(0);
  });

  it('耗时缺失就说「耗时未记录」，不补一个 0.0s', () => {
    expect(formatDuration(200)).toBe('0.2s');
    expect(formatDuration(null)).toBe('耗时未记录');
    expect(formatDuration(undefined)).toBe('耗时未记录');
  });
});

describe('编辑器状态条（图 1 §3.2 底部）', () => {
  it('时间戳无效就写 --:--:--，不编一个当前时间', () => {
    expect(clockOf('2026-10-17T12:00:00')).toBe('12:00:00');
    expect(clockOf(null)).toBe('--:--:--');
    expect(clockOf('not-a-date')).toBe('--:--:--');
  });

  it('行列为空显示「—」；换行符按正文实测；文件名取 path 末段', () => {
    expect(cursorText(null)).toBe('行 —, 列 —');
    expect(cursorText({ line: 3, column: null })).toBe('行 3, 列 —');
    expect(detectEol('a\r\nb')).toBe('CRLF');
    expect(detectEol('a\nb')).toBe('LF');
    expect(fileBasename('/nb/churn.ipynb')).toBe('churn.ipynb');
    expect(fileBasename('', '兜底标题')).toBe('兜底标题');
  });
});
