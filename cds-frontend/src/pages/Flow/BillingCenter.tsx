import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Descriptions, Drawer, Input, Select, Space, Table, Tag, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { DownloadOutlined, FileAddOutlined, ReloadOutlined } from '@ant-design/icons';
import {
  billingApi,
  downloadBlob,
  invoiceStatusLabel,
  type BillingInvoice,
  type InvoiceListParams,
} from '../../services/billingApi';
import { QueryErrorAlert } from '../../components/Feedback/QueryFeedback';
import { getErrorMessage } from '../../components/Feedback/errorMessage';
import { useContractOptions } from './useContractOptions';

const PERIOD_PATTERN = /^\d{4}-\d{2}$/;

const STATUS_COLORS: Record<string, string> = {
  generated: 'blue',
  confirmed: 'green',
  disputed: 'orange',
  settled: 'purple',
};

function formatGb(bytes: number): string {
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

export default function BillingCenter() {
  const queryClient = useQueryClient();
  const [contractId, setContractId] = useState<string | undefined>();
  const [period, setPeriod] = useState<string>('');
  const [detail, setDetail] = useState<BillingInvoice | null>(null);

  const contracts = useContractOptions();
  const contractTitle = useMemo(() => {
    const map = new Map((contracts.data ?? []).map((c) => [c.id, c.title || c.contract_no]));
    return (id: string) => map.get(id) ?? `${id.slice(0, 8)}…`;
  }, [contracts.data]);

  const params: InvoiceListParams = {};
  if (contractId) params.contract_id = contractId;
  if (PERIOD_PATTERN.test(period.trim())) params.billing_period = period.trim();

  const invoices = useQuery({
    queryKey: ['billing', 'invoices', params],
    queryFn: () => billingApi.listInvoices(params),
  });

  const generate = useMutation({
    mutationFn: (payload: { contract_id: string; billing_period: string }) => billingApi.generateInvoice(payload),
    onSuccess: (inv) => {
      message.success(`账单 ${inv.invoice_no} 已生成，合计 ¥${inv.total_amount.toFixed(2)}`);
      void queryClient.invalidateQueries({ queryKey: ['billing', 'invoices'] });
    },
    onError: (err) => message.error(getErrorMessage(err, '生成账单失败')),
  });

  const exportCsv = useMutation({
    mutationFn: async (inv: BillingInvoice) => {
      const blob = await billingApi.exportInvoiceCsv(inv.id);
      downloadBlob(blob, `${inv.invoice_no}.csv`);
    },
    onError: (err) => message.error(getErrorMessage(err, '导出失败')),
  });

  const onGenerate = () => {
    const trimmed = period.trim();
    if (!contractId) {
      message.warning('请先选择合约');
      return;
    }
    if (!PERIOD_PATTERN.test(trimmed)) {
      message.warning('账期格式为 YYYY-MM，例如 2026-11');
      return;
    }
    generate.mutate({ contract_id: contractId, billing_period: trimmed });
  };

  const columns: ColumnsType<BillingInvoice> = [
    { title: '账单编号', dataIndex: 'invoice_no', width: 190 },
    { title: '账期', dataIndex: 'billing_period', width: 90 },
    { title: '合约', dataIndex: 'contract_id', render: (id: string) => contractTitle(id), ellipsis: true },
    { title: 'API调用', dataIndex: 'api_calls_count', width: 100 },
    { title: 'CPU小时', dataIndex: 'compute_cpu_hours', width: 100, render: (v: number) => v.toFixed(2) },
    { title: '出域', dataIndex: 'egress_bytes', width: 110, render: (v: number) => formatGb(v) },
    { title: '金额(元)', dataIndex: 'total_amount', width: 110, render: (v: number) => v.toFixed(2) },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (s: string) => <Tag color={STATUS_COLORS[s] ?? 'default'}>{invoiceStatusLabel(s)}</Tag>,
    },
    {
      title: '操作',
      key: 'actions',
      width: 160,
      render: (_, row) => (
        <Space>
          <Button size="small" onClick={() => setDetail(row)}>
            明细
          </Button>
          <Button size="small" icon={<DownloadOutlined />} loading={exportCsv.isPending} onClick={() => exportCsv.mutate(row)}>
            CSV
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <Card
      title="计量对账"
      extra={
        <Space wrap>
          <Select
            style={{ minWidth: 220 }}
            allowClear
            showSearch
            optionFilterProp="label"
            placeholder="按合约筛选"
            loading={contracts.isFetching}
            value={contractId}
            onChange={(v) => setContractId(v)}
            options={(contracts.data ?? []).map((c) => ({ value: c.id, label: c.title || c.contract_no }))}
          />
          <Input
            style={{ width: 120 }}
            placeholder="账期 2026-11"
            value={period}
            onChange={(e) => setPeriod(e.target.value)}
          />
          <Button icon={<FileAddOutlined />} loading={generate.isPending} onClick={onGenerate}>
            生成账单
          </Button>
          <Button icon={<ReloadOutlined />} onClick={() => void invoices.refetch()}>
            刷新
          </Button>
        </Space>
      }
    >
      {invoices.isError && <QueryErrorAlert error={invoices.error} />}
      {generate.isError && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={getErrorMessage(generate.error, '生成账单失败')}
        />
      )}
      <Table<BillingInvoice>
        rowKey="id"
        size="middle"
        loading={invoices.isFetching}
        columns={columns}
        dataSource={invoices.data?.items ?? []}
        pagination={{
          pageSize: 20,
          total: invoices.data?.total ?? 0,
          showTotal: (t) => `共 ${t} 张账单`,
        }}
      />
      <Drawer title="费用明细" width={640} open={detail !== null} onClose={() => setDetail(null)}>
        {detail && (
          <>
            <Descriptions size="small" column={2} bordered>
              <Descriptions.Item label="账单编号" span={2}>
                {detail.invoice_no}
              </Descriptions.Item>
              <Descriptions.Item label="账期">{detail.billing_period}</Descriptions.Item>
              <Descriptions.Item label="状态">{invoiceStatusLabel(detail.status)}</Descriptions.Item>
              <Descriptions.Item label="合约" span={2}>
                {contractTitle(detail.contract_id)}
              </Descriptions.Item>
              <Descriptions.Item label="合计金额" span={2}>
                ¥{detail.total_amount.toFixed(2)}
              </Descriptions.Item>
            </Descriptions>
            <Alert
              type="info"
              showIcon
              style={{ margin: '12px 0' }}
              message={String(detail.unit_price_config?.note ?? '单价快照生成时刻固化，历史账单不随调价变化')}
            />
            <Table
              rowKey="meter"
              size="small"
              pagination={false}
              dataSource={detail.line_items}
              columns={[
                { title: '计量项', dataIndex: 'meter' },
                { title: '用量', dataIndex: 'count', render: (v: number) => (typeof v === 'number' ? v.toLocaleString() : String(v)) },
                { title: '单位', dataIndex: 'unit', width: 80 },
                { title: '单价', dataIndex: 'unit_price', width: 90 },
                { title: '金额(元)', dataIndex: 'amount', width: 100, render: (v: number) => v.toFixed(4) },
                { title: '备注', dataIndex: 'note', ellipsis: true },
              ]}
            />
          </>
        )}
      </Drawer>
    </Card>
  );
}
