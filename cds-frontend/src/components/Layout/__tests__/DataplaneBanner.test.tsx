// SS-13 交付档横幅真值：数据面关闭（dataplane.enabled=false）必须出横幅且带上 reason；
// 完整档沉默；探针失败也不叠错误 Alert（产品决策），但必须留一次 console.warn——
// 「探针失败」不能和「数据面健康」无痕同渲染，否则控制面档 + 401/网关错误会伪装成完整部署。
// 数据口径：services/api.ts 的响应拦截器已 response => response.data，
// metaApi.getCapabilityMatrix() 解析出的就是矩阵本身（没有 .data 第二层）。
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import DataplaneBanner from '../DataplaneBanner';
import { metaApi, type CapabilityMatrix } from '../../../services/metaApi';

vi.mock('../../../services/metaApi', () => ({
  metaApi: { getCapabilityMatrix: vi.fn() },
}));

// 旧探针路径（裸 api.get）的桩：返回的正是拦截器拆包后的载荷（无 .data）。
// 若探针退回顾 api.get 再自取 resp.data，得到 undefined → 横幅缺失 → 本文件对使用例红。
vi.mock('../../../services/api', () => ({
  default: {
    get: () =>
      Promise.resolve({
        matrix: {},
        levels: {},
        dataplane: {
          enabled: false,
          mode: 'control_plane_only',
          reason: 'dataplane_disabled',
          note: '未启用 MicroVM 数据面：平台按控制面档交付',
        },
      }),
  },
}));

const disabledMatrix = {
  matrix: {},
  levels: {},
  dataplane: {
    enabled: false,
    mode: 'control_plane_only',
    reason: 'dataplane_disabled',
    note: '未启用 MicroVM 数据面：平台按控制面档交付',
  },
} as unknown as CapabilityMatrix;

const enabledMatrix = {
  matrix: {},
  levels: {},
  dataplane: { enabled: true, mode: 'full', reason: null, note: 'MicroVM 数据面就绪' },
} as unknown as CapabilityMatrix;

const getMatrix = vi.mocked(metaApi.getCapabilityMatrix);

describe('DataplaneBanner SS-13 交付档横幅', () => {
  beforeEach(() => {
    getMatrix.mockReset();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('控制面档（dataplane.enabled=false）：横幅渲染，描述含 note 与 reason', async () => {
    getMatrix.mockResolvedValue(disabledMatrix);
    render(<DataplaneBanner />);
    const banner = await screen.findByTestId('dataplane-banner');
    expect(banner).toBeInTheDocument();
    expect(banner.textContent).toContain('未启用 MicroVM 数据面');
    expect(banner.textContent).toContain('dataplane_disabled');
  });

  it('完整档（dataplane.enabled=true）：不渲染横幅', async () => {
    getMatrix.mockResolvedValue(enabledMatrix);
    const { container } = render(<DataplaneBanner />);
    await act(async () => {
      await getMatrix.mock.results[0]?.value;
    });
    expect(container.querySelector('[data-testid="dataplane-banner"]')).toBeNull();
  });

  it('探针失败：不叠横幅，但恰好一次 console.warn（与“数据面健康”不得无痕同渲染）', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    getMatrix.mockRejectedValue({ code: 'UNAUTHORIZED', message: 'token expired' });
    render(<DataplaneBanner />);
    await waitFor(() => expect(warn).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId('dataplane-banner')).toBeNull();
  });
});
