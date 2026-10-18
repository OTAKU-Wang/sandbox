// /compliance-disclosure 页的 anchoring 契约（对齐后端 app/schemas/compliance.py L60
// `anchoring: AnchoringDisclosure | None = None`）：
// ① 后端下发 null 时页面必须照常渲染，并显式写明「未配置/未知」——后端无 ErrorBoundary，
//    裸解引用等于整页白屏；
// ② 没有披露就不许渲染成「本地防篡改日志」这类看着像真值的默认值（禁伪造铁律）；
// ③ 有披露时仍按后端三个字段（backend / backend_label / is_consortium_chain）如实区分联盟链与本地日志。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { renderWithProviders } from '../../test/test-utils';
import ComplianceDisclosure from '../ComplianceDisclosure';
import { complianceApi, type ComplianceReport } from '../../services/complianceApi';
import { monitoringApi, type SecurityPosture } from '../../services/monitoringApi';

// antd Table/Card 的响应式断点依赖 window.matchMedia（jsdom 未实现）
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }),
});

vi.mock('../../services/complianceApi', () => ({
  complianceApi: { quickReport: vi.fn() },
}));
vi.mock('../../services/monitoringApi', () => ({
  monitoringApi: { getSecurityPosture: vi.fn() },
}));

function makeReport(overrides: Partial<ComplianceReport> = {}): ComplianceReport {
  return {
    report_id: 'rpt-2026-10-18',
    report_type: 'tc609',
    generated_at: '2026-10-18T02:00:00Z',
    period_start: '2026-09-18T00:00:00Z',
    period_end: '2026-10-18T00:00:00Z',
    audit_summary: { total_events: 186 },
    access_control: { failed_auth_attempts: 2 },
    data_protection: { output_blocks: 1 },
    compliance_status: { audit_trail: 'pass', security_incident_response: 'warn' },
    recommendations: ['建议补齐安全事件响应流程'],
    anchoring: {
      backend: 'pg-log',
      backend_label: 'PG 追加式防篡改日志（非上链）',
      is_consortium_chain: false,
    },
    signing_backend: 'software',
    aead_algorithm: 'aes_fallback',
    trust_root: 'local-simulated',
    chain_anchoring: 'pg-log',
    ...overrides,
  };
}

const posture: SecurityPosture = {
  generated_at: '2026-10-18T02:00:00Z',
  release_recommendation: 'conditional_go',
  capabilities: [],
  signing_backend: 'software',
  aead_algorithm: 'aes_fallback',
  trust_root: 'local-simulated',
  chain_anchoring: 'pg-log',
};

async function renderPage() {
  renderWithProviders(<ComplianceDisclosure />);
  await screen.findByText('合规披露');
  await waitFor(() => expect(complianceApi.quickReport).toHaveBeenCalled());
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(monitoringApi.getSecurityPosture).mockResolvedValue(posture);
});

describe('ComplianceDisclosure 链锚定披露', () => {
  it('后端 anchoring=null：页面照常渲染并显式披露未配置/未知，不白屏', async () => {
    vi.mocked(complianceApi.quickReport).mockResolvedValue(makeReport({ anchoring: null }));

    await renderPage();

    await screen.findByText('链锚定披露');
    const honest = await screen.findByText(/未配置\/未知/);
    expect(honest.textContent).toContain('未配置/未知');
    // 没有披露就没有结论：不得凭空渲染成联盟链或本地日志
    expect(document.body.textContent).not.toContain('联盟链锚定');
    expect(document.body.textContent).not.toContain('本地防篡改日志（非上链）');
    // 同页其余区块照常渲染（anchoring 缺失不得拖垮整页）
    expect(screen.getByText(/合规状态（近 30 天/)).toBeTruthy();
    expect(screen.getByText('audit_trail: pass')).toBeTruthy();
  });

  it('后端下发 anchoring：按 backend/backend_label/is_consortium_chain 如实渲染', async () => {
    vi.mocked(complianceApi.quickReport).mockResolvedValue(makeReport({
      anchoring: {
        backend: 'fisco-bcos',
        backend_label: 'FISCO BCOS 联盟链',
        is_consortium_chain: true,
      },
    }));

    await renderPage();

    await screen.findByText('FISCO BCOS 联盟链');
    expect(screen.getByText('联盟链锚定')).toBeTruthy();
    expect(screen.getByText('后端: fisco-bcos')).toBeTruthy();
    expect(document.body.textContent).not.toContain('未配置/未知');
  });
});
