/**
 * A294（panel 半边）：FileDirectoryPanel 页脚在「审查披露缺失」时不得仍是无条件断言。
 * 台账 docs/ocr-review-2026-10-18/ocr-verified-findings.md §A294 + fe-09 交接
 * （.gate-scratch/evidence/fe-09-tsx.md 的 NEEDS-OWNER）：后端只发
 * `passed; findings=N` / `redacted; findings=N` 两种 X-CDS-Output-Review；跨域未 expose_headers
 * 时头根本到不了页面，`parseReviewHeader(null)` 返 null = 审查结果未知。父级
 * （NotebookWorkbench.downloadSelected）已按第三态出 toast + warn 日志行，但页脚那句
 * 「（逐个过 T5 输出审查后取回）」无条件渲染，把未知渲染成断言式通过 —— 踩 honest degradation
 * 铁律。改法：父级把「本轮未取到披露的文件数」传进来（disclosureMissingCount），panel 用与
 * SessionDetail/NotebookWorkbench 已落地口径一致的哨兵「未读取到输出审查披露」替换断言句。
 * 三条用例：①默认（全有披露）保持现文案；②计数 0/null 不得伪造第三态；③计数 >0 必须换第三态
 * 且断言句消失（②③互为反向控制，防「永远渲染第三态」的假修）。
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { FileDirectoryPanel, type FileDirectoryPanelProps } from '../notebookWorkbenchPanels';
import type { WorkbenchFileNode } from '../notebookWorkbenchModel';

const outFile: WorkbenchFileNode = {
  key: 'out.csv',
  name: 'out.csv',
  path: 'files/out.csv',
  isDirectory: false,
  size: 2048,
  children: null,
  downloadable: true,
};

const baseProps: FileDirectoryPanelProps = {
  nodes: [outFile],
  summary: { count: 1, bytes: 2048, partial: false },
  loading: false,
  error: null,
  emptyHint: '还没有可信清单',
  quotaText: null,
  quotaHint: '配额口径提示',
  partialHint: null,
  refreshing: false,
  onRefresh: vi.fn(),
  refreshEnabled: true,
  selected: ['out.csv'],
  onToggle: vi.fn(),
  onDownloadSelected: vi.fn(),
  downloading: false,
};

describe('A294 FileDirectoryPanel 页脚的审查披露第三态', () => {
  it('默认（父级没报缺失）：页脚保持现断言文案「逐个过 T5 输出审查后取回」', () => {
    render(<FileDirectoryPanel {...baseProps} />);
    expect(screen.getByText(/逐个过 T5 输出审查后取回/)).toBeInTheDocument();
  });

  it('计数 0 / null（本轮披露齐全）：仍是断言文案，不得伪造第三态', () => {
    const { unmount } = render(<FileDirectoryPanel {...baseProps} disclosureMissingCount={0} />);
    expect(screen.getByText(/逐个过 T5 输出审查后取回/)).toBeInTheDocument();
    expect(screen.queryByText(/未读取到输出审查披露/)).not.toBeInTheDocument();
    unmount();
    render(<FileDirectoryPanel {...baseProps} disclosureMissingCount={null} />);
    expect(screen.getByText(/逐个过 T5 输出审查后取回/)).toBeInTheDocument();
    expect(screen.queryByText(/未读取到输出审查披露/)).not.toBeInTheDocument();
  });

  it('父级报了 2 个下载未取到披露：页脚必须换成第三态哨兵，且不再出现无条件断言', async () => {
    render(<FileDirectoryPanel {...baseProps} disclosureMissingCount={2} />);
    // 哨兵与 SessionDetail（内联 Alert/行徽标）、NotebookWorkbench（toast + warn 日志行）连续同文
    expect(await screen.findByText(/2 个文件未读取到输出审查披露/)).toBeInTheDocument();
    expect(screen.queryByText(/逐个过 T5 输出审查后取回/)).not.toBeInTheDocument();
    // 通过口径锁：允许「不代表已过审」这类否定式免责声明，但任何肯句式「已过审/已通过审查」都不得出现
    for (const el of screen.queryAllByText(/已过审|已通过审查/)) {
      expect(el.textContent, `页脚出现肯句式通过断言：${el.textContent}`).toContain('不代表');
    }
  });
});
