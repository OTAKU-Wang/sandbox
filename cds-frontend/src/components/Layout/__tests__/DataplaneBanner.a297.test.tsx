// A297：数据面横幅必须走 services 边界（metaApi.getCapabilityMatrix），而不是自己拿共享
// axios 实例再解一次 `.data`——那个绕过本身就是 A243（横幅永不渲染 = honest-degradation 披露面
// 静默失效）的成因。两半都要锁：
//   ① 约定闸（源级）：DataplaneBanner.tsx 里不得出现 services/api / axios / api.get( / 第二次
//      `.data` 解包；能力矩阵只能经 metaApi 取。
//   ② 行为闸（集成）：故意**不** mock metaApi，只把共享实例 `api` 的传输层（adapter）换成桩，
//      让 services/api.ts 的真实响应拦截器 `response => response.data` 跑一遍。这样「resolve 值
//      到底是 body 还是 AxiosResponse」由真实拦截器决定：退回顾 api.get 再自取 resp.data 会拿到
//      undefined ⇒ 横幅缺失 ⇒ 本文件行为用例红。tsc 看不见这类错（axios 类型仍声明 AxiosResponse），
//      所以这条只能钉在运行时。
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import DataplaneBanner from '../DataplaneBanner';
import api from '../../../services/api';

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

// 载荷形状取自后端 app/services/capability_matrix.py:338-343（matrix / levels / dataplane 三键）。
const CONTROL_PLANE_BODY = {
  matrix: {},
  levels: {},
  dataplane: {
    enabled: false,
    mode: 'control_plane_only',
    reason: 'dataplane_disabled',
    note: '未启用 MicroVM 数据面：平台按控制面档交付',
  },
};

const FULL_BODY = {
  matrix: {},
  levels: {},
  dataplane: { enabled: true, mode: 'full', reason: null, note: 'MicroVM 数据面就绪' },
};

describe('A297 横幅经 services 边界取能力矩阵（A243 成因锁）', () => {
  describe('约定闸：源级不得绕过 services', () => {
    const src = readSourceFromDisk('src/components/Layout/DataplaneBanner.tsx');

    it('不得直接 import 共享 axios 实例或 axios 本体', () => {
      expect(src).not.toMatch(/services\/api/);
      expect(src).not.toMatch(/\baxios\b/);
      expect(src).not.toMatch(/\bapi\.get\s*\(/);
    });

    it('能力矩阵只能经 metaApi.getCapabilityMatrix() 取', () => {
      expect(src).toMatch(/from\s+'[^']*services\/metaApi'/);
      expect(src).toMatch(/getCapabilityMatrix\s*\(\s*\)/);
    });

    it('不得在 metaApi 之后再解一次 .data（拦截器已拆一层，再拆＝A243）', () => {
      expect(src).not.toMatch(/\.data\b/);
    });

    // A297b：类型边界也收回 services 层——横幅不得自带一份本地 interface 再 `as` 一下了事，
    // 否则后端 dataplane 字段改名/删字段的漂移在 tsc 里看不见（那个 `as` 就是遮羞布）。
    it('dataplane 类型只能由 metaApi 导出并声明（组件不得自带类型再 as）', () => {
      const apiSrc = readSourceFromDisk('src/services/metaApi.ts');
      expect(apiSrc).toMatch(/export\s+(interface|type)\s+DataplaneStatus\b/);
      expect(apiSrc).toMatch(/dataplane\?\s*:\s*DataplaneStatus\b/);
      expect(src).not.toMatch(/interface\s+DataplaneStatus\b/);
      expect(src).not.toMatch(/\bas\s+DataplaneStatus\b/);
    });
  });

  describe('行为闸：真共享实例 + 真拆包拦截器下的档位披露', () => {
    const seenUrls: string[] = [];
    let body: Record<string, unknown> = CONTROL_PLANE_BODY;
    const realAdapter = api.defaults.adapter;

    beforeEach(() => {
      seenUrls.length = 0;
      body = CONTROL_PLANE_BODY;
      api.defaults.adapter = (async (config: { url?: string }) => {
        seenUrls.push(String(config.url));
        // 返回 AxiosResponse 形状；下面的真实拦截器会把它拆成 body 本体。
        return { data: body, status: 200, statusText: 'OK', headers: {}, config };
      }) as never;
    });

    afterEach(() => {
      api.defaults.adapter = realAdapter as never;
    });

    it('控制面档：横幅渲染且带 note 与 reason（探针打到 /meta/capability-matrix）', async () => {
      render(<DataplaneBanner />);
      const banner = await screen.findByTestId('dataplane-banner');
      expect(banner).toBeInTheDocument();
      expect(banner.textContent).toContain('未启用 MicroVM 数据面');
      expect(banner.textContent).toContain('dataplane_disabled');
      expect(seenUrls).toContain('/meta/capability-matrix');
    });

    it('完整档：不渲染横幅（档位为真时不得常亮告警）', async () => {
      body = FULL_BODY;
      const { container } = render(<DataplaneBanner />);
      await waitFor(() => expect(seenUrls).toHaveLength(1));
      expect(container.querySelector('[data-testid="dataplane-banner"]')).toBeNull();
    });
  });
});
