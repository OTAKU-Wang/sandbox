// Task 7 的用户可见那一半：智能探查弹窗必须把后端算出的分级与出域后果逐列披露出来。
// 只钉结构化内容（列名、识别类型、等级、管控动作、截断行数），不钉解释性文案。
import { describe, it, expect, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils';
import AutoProfileModal from '../AutoProfileModal';
import type { AutoProfileColumn, AutoProfileResult } from '../../../services/dataResourceApi';

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

function column(over: Partial<AutoProfileColumn>): AutoProfileColumn {
  return {
    field: 'x',
    pii_type: null,
    severity: null,
    layer: null,
    confidence: null,
    classification_level: null,
    enforced: false,
    evidence_rows: 0,
    samples_checked: 0,
    note: null,
    pii_types: [],
    ...over,
  };
}

function result(over: Partial<AutoProfileResult>): AutoProfileResult {
  return {
    resource_id: 'r-1',
    format: 'csv',
    engine: 'rule',
    scanned_rows: 2,
    scanned_bytes: 200,
    truncated: false,
    columns_sampled_per_field: 20,
    sensitivity_level: 'high',
    classification_level: 4,
    columns: [],
    field_classifications: {},
    enforcement_preview: { mask_fields: [], deny_out_fields: [] },
    persisted: true,
    ...over,
  };
}

async function renderModal(payload: AutoProfileResult) {
  renderWithProviders(<AutoProfileModal result={payload} resourceName="客户表" onClose={() => {}} />);
  await vi.waitFor(() => expect(document.querySelector('.ant-alert')).toBeTruthy());
}

describe('智能探查分级弹窗（Task 7）', () => {
  it('按后端给的 level 逐列说出出域后果：4 禁止出域、3 强制掩码、无证据不设限', async () => {
    await renderModal(result({
      field_classifications: { id_card: 4, mobile: 3 },
      enforcement_preview: { mask_fields: ['mobile'], deny_out_fields: ['id_card'] },
      columns: [
        column({ field: 'id_card', pii_type: 'id_card', severity: 'critical', layer: 'regex', confidence: 1, classification_level: 4, enforced: true, evidence_rows: 2, samples_checked: 2 }),
        column({ field: 'mobile', pii_type: 'phone', severity: 'high', layer: 'regex', confidence: 1, classification_level: 3, enforced: true, evidence_rows: 2, samples_checked: 2 }),
        column({ field: 'revenue', samples_checked: 2 }),
      ],
    }));

    expect(screen.getByText('身份证号')).toBeTruthy();
    expect(screen.getByText('手机号')).toBeTruthy();
    expect(screen.getByText('禁止出域')).toBeTruthy();
    expect(screen.getByText('强制掩码')).toBeTruthy();
    expect(screen.getByText('不设限')).toBeTruthy();
    expect(screen.getByText('4 级 机密')).toBeTruthy();
  });

  it('NER 层命中只提示不管控，且未登记的类型原样回显、不编中文名', async () => {
    await renderModal(result({
      sensitivity_level: 'medium',
      classification_level: 2,
      columns: [
        column({ field: 'addr', pii_type: 'address', severity: 'high', layer: 'ner', confidence: 0.7, classification_level: 2, note: '命中来自 ner 层' }),
        column({ field: 'ext', pii_type: 'some_future_type', severity: 'high', layer: 'regex', confidence: 0.9, classification_level: 2 }),
      ],
    }));

    expect(screen.getByText('地址')).toBeTruthy();
    expect(screen.getByText('仅提示，不管控')).toBeTruthy();
    expect(screen.getByText('some_future_type')).toBeTruthy();
    expect(screen.queryByText('强制掩码')).toBeNull();
    expect(screen.queryByText('禁止出域')).toBeNull();
  });

  it('只扫了前 500 行时必须说出来，不能让部分扫描冒充全量结论', async () => {
    await renderModal(result({
      scanned_rows: 500,
      truncated: true,
      columns: [column({ field: 'id_card', pii_type: 'id_card', layer: 'regex', confidence: 1, classification_level: 4, enforced: true })],
    }));

    const warning = document.querySelector('.ant-typography-warning');
    expect(warning?.textContent).toContain('500');
  });
});
