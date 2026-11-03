import api from './api';

// 计量对账单（Task 11）唯一数据源：后端契约 = app/api/billing.py，
// 挂载在 /api/v1/deliveries/invoices*（gateway 组）。行不可变语义在 UI 侧的
// 映射：页面不提供「编辑账单」，只有 生成 / 查询 / 导出 三个动作。

export type InvoiceStatus = 'generated' | 'confirmed' | 'disputed' | 'settled' | (string & {});

export interface InvoiceLineItem {
  meter: string;
  count: number;
  unit: string;
  unit_price: number;
  amount: number;
  note: string | null;
}

export interface BillingInvoice {
  id: string;
  invoice_no: string;
  contract_id: string;
  product_id: string | null;
  buyer_id: string | null;
  billing_period: string;
  api_calls_count: number;
  compute_cpu_hours: number;
  storage_gb_days: number;
  egress_bytes: number;
  unit_price_config: Record<string, unknown>;
  total_amount: number;
  line_items: InvoiceLineItem[];
  blockchain_tx_hash: string | null;
  status: InvoiceStatus;
  created_at: string | null;
}

export interface InvoiceListResult {
  items: BillingInvoice[];
  total: number;
  page: number;
  page_size: number;
}

export interface InvoiceListParams {
  contract_id?: string;
  billing_period?: string;
  page?: number;
  page_size?: number;
}

export interface InvoiceGeneratePayload {
  contract_id: string;
  billing_period: string;
}

export const INVOICE_STATUS_LABELS: Record<string, string> = {
  generated: '已生成',
  confirmed: '已确认',
  disputed: '争议中',
  settled: '已结算',
};

export const billingApi = {
  listInvoices: (params?: InvoiceListParams): Promise<InvoiceListResult> =>
    api.get('/deliveries/invoices', { params }) as unknown as Promise<InvoiceListResult>,

  generateInvoice: (payload: InvoiceGeneratePayload): Promise<BillingInvoice> =>
    api.post('/deliveries/invoices/generate', payload) as unknown as Promise<BillingInvoice>,

  exportInvoiceCsv: async (invoiceId: string): Promise<Blob> => {
    const blob = (await api.get(`/deliveries/invoices/${invoiceId}/export`, {
      responseType: 'blob',
    })) as unknown as Blob;
    // responseType='blob' 时连错误体都是 Blob：还原成文本再抛，页面才看得到 W2 文案。
    if (blob.type.includes('application/json')) {
      throw new Error(await blob.text());
    }
    return blob;
  },
};

export function invoiceStatusLabel(status: string): string {
  return INVOICE_STATUS_LABELS[status] ?? status;
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = window.URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.URL.revokeObjectURL(url);
}
