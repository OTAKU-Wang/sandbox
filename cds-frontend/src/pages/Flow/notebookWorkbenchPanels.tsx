// SS-23「调试环境」外框的两个展示组件：左栏「文件目录」与右下「输出/日志」卡。
//
// 两者都是**纯展示组件**：所有数与状态由页面算好后传进来，组件自己不取数、不猜默认值。
// 槽位缺真值时宁可写「未记录 / 未接入」，也不画原型上的示例数（项目 honest degradation 铁律）。
import { Checkbox, Empty, Spin, Tooltip, Typography } from 'antd';
import {
  CheckCircleFilled,
  DeleteOutlined,
  FileTextOutlined,
  FolderOpenOutlined,
  FolderOutlined,
  ReloadOutlined,
  StopOutlined,
} from '@ant-design/icons';
import { useState, type ReactNode } from 'react';
import type { RunStatusView, WorkbenchFileNode, WorkbenchFileSummary, WorkbenchLogLine } from './notebookWorkbenchModel';
import {
  NBW_FILE_PANEL,
  NBW_TAB_OUTPUT,
  NBW_TAB_TERMINAL,
  TERMINAL_TAB_NOTE,
  formatBytes,
} from './notebookWorkbenchModel';

const { Text } = Typography;

// ────────────────────────── 左栏：文件目录（图 1 §3.1） ──────────────────────────

export interface FileDirectoryPanelProps {
  nodes: WorkbenchFileNode[];
  /** null = 这一栏还没有可信清单（未开会话 / 读失败），此时底部两行统计不上屏。 */
  summary: WorkbenchFileSummary | null;
  loading: boolean;
  /** 后端原话（读目录失败的原因），没有就 null */
  error: string | null;
  /** 没有可信清单时的诚实解释 */
  emptyHint: string;
  /** 配额分母（已按口径格式化好的字符串），null = 配额未记录 */
  quotaText: string | null;
  quotaHint: string;
  /** 清单不是全量时的披露（开发环目录读取预算 / 失败子目录） */
  partialHint: string | null;
  refreshing: boolean;
  onRefresh: () => void;
  refreshEnabled: boolean;
  selected: string[];
  onToggle: (key: string, checked: boolean) => void;
  /** 勾选后的真动作：只有会话上传文件那条链能真取回。 */
  onDownloadSelected: () => void;
  downloading: boolean;
}

function flatten(list: WorkbenchFileNode[]): WorkbenchFileNode[] {
  return list.flatMap((node) => (node.isDirectory ? flatten(node.children ?? []) : [node]));
}

function TreeRow({
  node,
  depth,
  selected,
  onToggle,
}: {
  node: WorkbenchFileNode;
  depth: number;
  selected: string[];
  onToggle: (key: string, checked: boolean) => void;
}) {
  const [open, setOpen] = useState(true);
  const pad = { paddingLeft: 6 + depth * 14 };

  if (node.isDirectory) {
    return (
      <>
        <div className="nbw-file-row" style={pad}>
          <button
            type="button"
            className="nbw-folder-toggle"
            aria-label={`${open ? '折叠' : '展开'} ${node.name}`}
            onClick={() => setOpen((prev) => !prev)}
          >
            {open ? '▾' : '▸'}
          </button>
          {open ? <FolderOpenOutlined style={{ color: '#faad14' }} /> : <FolderOutlined style={{ color: '#faad14' }} />}
          <span className="nbw-file-name" title={node.path}>
            {node.name}
          </span>
        </div>
        {open
          ? (node.children ?? []).map((child) => (
              <TreeRow key={child.key} node={child} depth={depth + 1} selected={selected} onToggle={onToggle} />
            ))
          : null}
      </>
    );
  }

  const checkbox = (
    <Checkbox
      aria-label={`选择 ${node.name}`}
      disabled={!node.downloadable}
      checked={selected.includes(node.key)}
      onChange={(event) => onToggle(node.key, event.target.checked)}
    />
  );

  return (
    <div className="nbw-file-row" style={pad}>
      {node.downloadable ? (
        checkbox
      ) : (
        <Tooltip title="这条来自开发环（JupyterLab）目录：本页只读目录，取文件走 JupyterLab 自己的下载，勾选在这里没有对应动作，故禁用">
          <span>{checkbox}</span>
        </Tooltip>
      )}
      <FileTextOutlined style={{ color: '#8c8c8c' }} />
      <span className="nbw-file-name" title={node.path}>
        {node.name}
      </span>
    </div>
  );
}

export function FileDirectoryPanel(props: FileDirectoryPanelProps) {
  const {
    nodes,
    summary,
    loading,
    error,
    emptyHint,
    quotaText,
    quotaHint,
    partialHint,
    refreshing,
    onRefresh,
    refreshEnabled,
    selected,
    onToggle,
    onDownloadSelected,
    downloading,
  } = props;
  const chosenCount = flatten(nodes).filter((node) => selected.includes(node.key)).length;

  return (
    <aside className="nbw-left" aria-label={NBW_FILE_PANEL}>
      <div className="nbw-card-head">
        <span className="nbw-card-title">{NBW_FILE_PANEL}</span>
        <Tooltip title={refreshEnabled ? '重新读取沙箱目录' : '先有沙箱会话才读得到目录'}>
          <button
            type="button"
            className="nbw-icon-btn"
            aria-label="刷新文件目录"
            disabled={!refreshEnabled || refreshing}
            onClick={onRefresh}
          >
            <ReloadOutlined spin={refreshing} />
          </button>
        </Tooltip>
      </div>

      <div className="nbw-card-body">
        {loading && !nodes.length ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <Spin size="small" />
            <Text type="secondary" style={{ fontSize: 12 }}>
              正在读取沙箱目录
            </Text>
          </div>
        ) : null}

        {!loading && error ? (
          <Text type="danger" style={{ fontSize: 12 }}>
            目录读取失败：{error}
          </Text>
        ) : null}

        {!loading && !error && !nodes.length ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<span style={{ fontSize: 12 }}>{emptyHint}</span>} />
        ) : null}

        {nodes.map((node) => (
          <TreeRow key={node.key} node={node} depth={0} selected={selected} onToggle={onToggle} />
        ))}
      </div>

      {summary ? (
        <div className="nbw-panel-foot">
          <div>共 {summary.count} 个文件</div>
          <Tooltip title={quotaHint}>
            <div>
              占用 {formatBytes(summary.bytes)}
              {quotaText ? ` / ${quotaText}` : ' · 配额未记录'}
            </div>
          </Tooltip>
          {partialHint ? <div>{partialHint}</div> : null}
          {chosenCount ? (
            <div className="nbw-panel-foot-action">
              <a onClick={downloading ? undefined : onDownloadSelected}>下载选中 {chosenCount} 个</a>
              <span style={{ marginLeft: 6 }}>（逐个过 T5 输出审查后取回）</span>
            </div>
          ) : null}
        </div>
      ) : null}
    </aside>
  );
}

// ─────────────────────── 右下：输出/日志卡（图 1 §3.3） ───────────────────────

export type OutputTabKey = 'output' | 'problems' | 'terminal';

export interface OutputLogCardProps {
  lines: WorkbenchLogLine[];
  tab: OutputTabKey;
  onTab: (tab: OutputTabKey) => void;
  status: RunStatusView;
  statusHint: string;
  /** 高危动作：二次确认由页面做（Modal.useModal 的确认框），展示组件只管这颗钮本身。 */
  onStop: () => void;
  stopEnabled: boolean;
  stopHint: string;
  onClear: () => void;
  clearEnabled: boolean;
  /** 底部会话行（左）与其 tooltip 口径 */
  sessionLine: string;
  sessionHint: string;
  /** 「查看完整日志」落点；null = 无会话，也就没有可跳转的完整日志 */
  fullLogTo: string | null;
  onOpenFullLog: () => void;
  /** 追加在日志下方的诚实披露位（执行后端 / 降级原因等） */
  notices?: ReactNode;
}

const TONE_CLASS: Record<WorkbenchLogLine['tone'], string> = {
  info: '',
  ok: ' nbw-log-ok',
  warn: ' nbw-log-warn',
  error: ' nbw-log-error',
};

export function OutputLogCard(props: OutputLogCardProps) {
  const {
    lines,
    tab,
    onTab,
    status,
    statusHint,
    onStop,
    stopEnabled,
    stopHint,
    onClear,
    clearEnabled,
    sessionLine,
    sessionHint,
    fullLogTo,
    onOpenFullLog,
    notices,
  } = props;
  const problems = lines.filter((line) => line.problem);
  const shown = tab === 'problems' ? problems : lines;

  return (
    <section className="nbw-card nbw-card-fixed" aria-label="输出">
      <div className="nbw-tabbar">
        <div className="nbw-tabs" role="tablist">
          <button type="button" role="tab" aria-selected={tab === 'output'} className="nbw-tab" onClick={() => onTab('output')}>
            {NBW_TAB_OUTPUT}
          </button>
          <button type="button" role="tab" aria-selected={tab === 'problems'} className="nbw-tab" onClick={() => onTab('problems')}>
            问题 {problems.length}
          </button>
          <button type="button" role="tab" aria-selected={tab === 'terminal'} className="nbw-tab" onClick={() => onTab('terminal')}>
            {NBW_TAB_TERMINAL}
          </button>
        </div>
        <div className="nbw-tab-actions">
          <Tooltip title={statusHint}>
            <span className={`nbw-run-status nbw-run-${status.tone}`}>
              {status.tone === 'ok' ? <CheckCircleFilled style={{ marginRight: 4 }} /> : null}
              {status.text}
            </span>
          </Tooltip>
          <Tooltip title={stopHint}>
            <button type="button" className="nbw-icon-btn" aria-label="终止沙箱会话" disabled={!stopEnabled} onClick={onStop}>
              <StopOutlined />
            </button>
          </Tooltip>
          <Tooltip title="清空本页显示：只清前端这一屏，后端审计日志一条都不动（删除沙箱文件是另一个动作）">
            <button type="button" className="nbw-icon-btn" aria-label="清空本页输出显示" disabled={!clearEnabled} onClick={onClear}>
              <DeleteOutlined />
            </button>
          </Tooltip>
        </div>
      </div>

      <div className="nbw-card-body">
        {tab === 'terminal' ? (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {TERMINAL_TAB_NOTE}
          </Text>
        ) : shown.length ? (
          <div className="nbw-log">
            {shown.map((line) => (
              <div key={line.key} className={`nbw-log-line${TONE_CLASS[line.tone]}`}>
                <span className="nbw-log-clock">[{line.clock}]</span>
                <span className="nbw-log-text">{line.text}</span>
              </div>
            ))}
          </div>
        ) : (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {tab === 'problems'
              ? '没有问题条目：本会话审计流里没有执行失败、代码扫描拦截或输出阻断的记录。'
              : '暂无日志：本会话审计流还没有事件。'}
          </Text>
        )}
        {notices}
      </div>

      <div className="nbw-output-foot">
        <Tooltip title={sessionHint}>
          <span>{`>_ ${sessionLine}`}</span>
        </Tooltip>
        {fullLogTo ? (
          <a onClick={onOpenFullLog}>查看完整日志 &gt;</a>
        ) : (
          <Text type="secondary" style={{ fontSize: 12 }}>
            无沙箱会话，暂无完整日志
          </Text>
        )}
      </div>
    </section>
  );
}
