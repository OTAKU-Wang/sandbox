import api from './api';

// 合规报告（TC609-6-2025-01）— 后端 app/api/compliance.py（/api/v1/compliance）。
// 披露字段（PZ-06）由后端从实时状态计算：signing_backend / aead_algorithm /
// trust_root / chain_anchoring；anchoring 披露区分联盟链与本地防篡改日志。

export interface AnchoringDisclosure {
  backend: string;
  backend_label: string;
  is_consortium_chain: boolean;
}

export interface ComplianceReport {
  report_id: string;
  report_type: string;
  generated_at: string;
  period_start: string;
  period_end: string;
  space_id?: string;
  audit_summary: Record<string, unknown>;
  access_control: Record<string, unknown>;
  data_protection: Record<string, unknown>;
  compliance_status: Record<string, unknown>;
  recommendations: unknown[];
  // 后端 app/schemas/compliance.py L60：`anchoring: AnchoringDisclosure | None = None`——
  // 链锚定未配置时就是 null，前端类型必须同步可空（裸解引用会把整页炸成白屏）。
  anchoring: AnchoringDisclosure | null;
  // PZ-06: 实时状态披露字段
  signing_backend?: string;
  aead_algorithm?: string;
  trust_root?: string;
  chain_anchoring?: string;
}

export const complianceApi = {
  quickReport: (days = 30): Promise<ComplianceReport> =>
    api.get('/compliance/reports/quick', { params: { days } }),
};
