/**
 * A300 的前端半：横幅那句话读后端声明的 `dataplane.mode`。
 *
 * `reason` / `note` 早就在消费了（见 `DataplaneBanner.test.tsx` 与 `a297`），缺的是 `mode`：
 * 档位那句话原先是从 `enabled === false` 倒推出来的「控制面档」。今天后端
 * `capability_matrix.py:350` 令 `mode = "full" if enabled else "control_plane_only"`，两者等价，
 * 所以看不出问题；但将来加第三档时，只看布尔的这一支会继续替后端编一个档位出来。
 * 这里钉的是「话术跟着 mode 走 + 认不出的 mode 说未记录」。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import DataplaneBanner from '../DataplaneBanner';
import { metaApi, type CapabilityMatrix, type DataplaneStatus } from '../../../services/metaApi';

vi.mock('../../../services/metaApi', () => ({
  metaApi: { getCapabilityMatrix: vi.fn() },
}));

const getMatrix = vi.mocked(metaApi.getCapabilityMatrix);

function matrixWith(dataplane: Partial<DataplaneStatus> | null): CapabilityMatrix {
  return { matrix: {}, levels: {}, dataplane: dataplane === null ? undefined : {
    enabled: false,
    mode: 'control_plane_only',
    reason: 'dataplane_disabled',
    note: '未启用 MicroVM 数据面：平台按控制面档交付',
    ...dataplane,
  } } as CapabilityMatrix;
}

beforeEach(() => {
  getMatrix.mockReset();
});

describe('A300：档位话术跟着 mode 走', () => {
  it('mode=control_plane_only ⇒ 控制面档那句话（真值不变）', async () => {
    getMatrix.mockResolvedValue(matrixWith({ mode: 'control_plane_only' }));
    render(<DataplaneBanner />);
    await waitFor(() => expect(screen.getByTestId('dataplane-banner')).toBeInTheDocument());
    expect(document.body.textContent).toContain('当前部署为控制面档：沙箱执行不可用');
  });

  it('mode 与 enabled 互相打脸时，两个事实一起摆出来，不替后端圆成控制面档', async () => {
    getMatrix.mockResolvedValue(matrixWith({ mode: 'full' as DataplaneStatus['mode'], enabled: false }));
    render(<DataplaneBanner />);
    await waitFor(() => expect(screen.getByTestId('dataplane-banner')).toBeInTheDocument());
    const text = document.body.textContent ?? '';
    expect(text).toContain('后端档位声明为 full');
    expect(text).not.toContain('当前部署为控制面档');
  });

  it('后端哪天加第三档：认不出的 mode 照实说未记录并带上原值，不套已有话术', async () => {
    getMatrix.mockResolvedValue(matrixWith({ mode: 'degraded_dataplane' as DataplaneStatus['mode'] }));
    render(<DataplaneBanner />);
    await waitFor(() => expect(screen.getByTestId('dataplane-banner')).toBeInTheDocument());
    const text = document.body.textContent ?? '';
    expect(text).toContain('degraded_dataplane');
    expect(text).not.toContain('当前部署为控制面档');
  });

  it('reason 缺失时只说 note，不拼出「（原因：null）」', async () => {
    getMatrix.mockResolvedValue(matrixWith({ reason: null }));
    render(<DataplaneBanner />);
    await waitFor(() => expect(screen.getByTestId('dataplane-banner')).toBeInTheDocument());
    const text = document.body.textContent ?? '';
    expect(text).toContain('未启用 MicroVM 数据面');
    expect(text).not.toContain('原因：');
  });
});
