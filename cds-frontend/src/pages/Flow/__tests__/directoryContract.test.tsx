import { describe, it, expect, beforeAll } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils';
import FlowGuide from '../Guide';
import {
  SANDBOX_DIRECTORY_ENTRIES,
  SANDBOX_DIRECTORY_NOTICE_TITLE,
  SANDBOX_HARVEST_RULE,
} from '../directoryContract';

// SS-18 沙箱目录契约的钉桩。权威源是 app/services/cube_runtime_adapter.py 的 GUEST_* 常量
// 与 HARVEST_DIRS；后端改路径时必须同步 cds-frontend/src/pages/Flow/directoryContract.ts，
// 这条用例就是那根会红的哨兵。
const BACKEND_GUEST_PATHS = ['/sandbox/data', '/sandbox/output', '/scratch', '/results/.cds-exec'];

describe('沙箱目录契约文案', () => {
  it('四个目录与后端 GUEST_* 常量逐字同序', () => {
    expect(SANDBOX_DIRECTORY_ENTRIES.map((entry) => entry.path)).toEqual(BACKEND_GUEST_PATHS);
  });

  it('每个目录都有用途说明', () => {
    for (const entry of SANDBOX_DIRECTORY_ENTRIES) {
      expect(entry.purpose.trim().length).toBeGreaterThan(0);
    }
  });

  it('采集口径把 output/scratch 划进采集面，把 data/.cds-exec 划在采集面外', () => {
    expect(SANDBOX_HARVEST_RULE).toContain('/sandbox/output');
    expect(SANDBOX_HARVEST_RULE).toContain('/scratch');
    expect(SANDBOX_HARVEST_RULE).toContain('/sandbox/data');
    expect(SANDBOX_HARVEST_RULE).toContain('/results/.cds-exec');
    expect(SANDBOX_HARVEST_RULE).toContain('逐文件标注来源目录');
  });

  it('只读口径按事实表述：只读声明加采集排除，不夸成虚拟化层强制（禁伪造铁律）', () => {
    expect(SANDBOX_HARVEST_RULE).toContain('只读声明');
    expect(SANDBOX_HARVEST_RULE).toContain('出口网关');
    for (const overclaim of ['虚拟化层强制', 'hypervisor', '物理隔离', '绝对只读']) {
      expect(SANDBOX_HARVEST_RULE).not.toContain(overclaim);
    }
  });
});

describe('开发指引页的常驻目录约定公告', () => {
  beforeAll(() => {
    // jsdom 不实现 matchMedia，antd 响应式栅格在 Table 里会取它（与现有用例无交叉）。
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: (query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }),
    });
  });

  it('不做任何交互就把四个目录与采集口径渲染出来', () => {
    renderWithProviders(<FlowGuide />);
    expect(screen.getByText(SANDBOX_DIRECTORY_NOTICE_TITLE)).toBeInTheDocument();
    for (const entry of SANDBOX_DIRECTORY_ENTRIES) {
      expect(screen.getByText(entry.path)).toBeInTheDocument();
      expect(screen.getByText(entry.purpose)).toBeInTheDocument();
    }
    expect(screen.getByText(SANDBOX_HARVEST_RULE)).toBeInTheDocument();
  });
});
