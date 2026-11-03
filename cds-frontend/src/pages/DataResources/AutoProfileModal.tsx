import { Alert, Button, Modal, Space, Table, Tag, Tooltip, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { AutoProfileColumn, AutoProfileResult } from '../../services/dataResourceApi';

const { Text } = Typography;

/** 展示名，不是识别规则（识别只在后端 pii_ner 一处）；未登记类型原样回显，不编名字。 */
const PII_LABELS: Record<string, string> = {
  id_card: '身份证号',
  social_credit_code: '统一社会信用代码',
  bank_card: '银行卡号',
  phone: '手机号',
  passport: '护照号',
  email: '邮箱',
  ip_address: 'IP 地址',
  person_name: '姓名',
  address: '地址',
  organization: '机构名称',
  date_of_birth: '出生日期',
};

const SENSITIVITY_META: Record<AutoProfileResult['sensitivity_level'], { text: string; color: 'error' | 'warning' | 'success' }> = {
  high: { text: '高敏感', color: 'error' },
  medium: { text: '中敏感', color: 'warning' },
  low: { text: '低敏感', color: 'success' },
};

const LEVEL_META: Record<number, { text: string; color: string }> = {
  4: { text: '4 级 机密', color: 'red' },
  3: { text: '3 级 受控', color: 'orange' },
  2: { text: '2 级 内部', color: 'gold' },
  1: { text: '1 级 公开', color: 'green' },
};

function piiLabel(piiType: string | null): string {
  if (!piiType) return '-';
  return PII_LABELS[piiType] ?? piiType;
}

interface Props {
  result: AutoProfileResult | null;
  resourceName?: string;
  onClose: () => void;
}

export default function AutoProfileModal({ result, resourceName, onClose }: Props) {
  const columns: ColumnsType<AutoProfileColumn> = [
    { title: '字段', dataIndex: 'field', key: 'field', width: 160, render: (f: string) => <Text code>{f}</Text> },
    {
      title: '识别结果',
      key: 'pii',
      width: 190,
      render: (_, row) => (row.pii_type ? (
        <Tag color="volcano">{piiLabel(row.pii_type)}</Tag>
      ) : (
        <Text type="secondary">未检出敏感特征</Text>
      )),
    },
    {
      title: '识别依据',
      key: 'evidence',
      width: 190,
      render: (_, row) => (row.pii_type ? (
        <Text type="secondary" style={{ fontSize: 12 }}>
          {row.layer === 'regex' ? '格式规则' : `NER(${row.layer})`} · 置信度 {row.confidence?.toFixed(2) ?? '-'} · 命中 {row.evidence_rows}/{row.samples_checked} 个取样值
        </Text>
      ) : (
        <Text type="secondary" style={{ fontSize: 12 }}>已检查 {row.samples_checked} 个取样值</Text>
      )),
    },
    {
      title: '敏感级',
      key: 'level',
      width: 120,
      render: (_, row) => (row.classification_level ? (
        <Tag color={LEVEL_META[row.classification_level]?.color}>{LEVEL_META[row.classification_level]?.text ?? `${row.classification_level} 级`}</Tag>
      ) : (
        <Text type="secondary">-</Text>
      )),
    },
    {
      title: '出域管控',
      key: 'enforcement',
      render: (_, row) => {
        if (row.classification_level === 4) return <Tag color="red">禁止出域</Tag>;
        if (row.classification_level === 3) return <Tag color="orange">强制掩码</Tag>;
        if (row.note) {
          return (
            <Tooltip title={row.note}>
              <Tag>仅提示，不管控</Tag>
            </Tooltip>
          );
        }
        return <Text type="secondary">不设限</Text>;
      },
    },
  ];

  const piiColumns = (result?.columns ?? []).filter((col) => col.pii_type).length;
  const enforced = (result?.columns ?? []).filter((col) => col.enforced);
  const meta = result ? SENSITIVITY_META[result.sensitivity_level] : null;

  return (
    <Modal
      title={`智能探查分级${resourceName ? ` · ${resourceName}` : ''}`}
      open={!!result}
      onCancel={onClose}
      footer={<Button onClick={onClose}>关闭</Button>}
      width={1000}
    >
      {result && meta && (
        <>
          <Alert
            type={meta.color}
            showIcon
            style={{ marginBottom: 12 }}
            message={`推荐敏感等级：${meta.text}${result.classification_level ? ` · 整体 ${result.classification_level} 级` : ''}`}
            description={
              <Space direction="vertical" size={2}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  已探查 {result.scanned_rows} 行 × {result.columns.length} 列，其中 {piiColumns} 列检出敏感特征；
                  识别引擎：{result.engine}，每列取样：{result.columns_sampled_per_field} 个值。
                </Text>
                {result.truncated && (
                  <Text type="warning" style={{ fontSize: 12 }}>
                    当前样本规模较大，系统已基于前 {result.scanned_rows} 行数据完成特征推断与分级。
                  </Text>
                )}
                <Text type="secondary" style={{ fontSize: 12 }}>
                  分级结论已写回资源元数据；出域管控按此执行：
                  {enforced.length === 0 ? (
                    <Tag style={{ marginInlineStart: 4 }}>本次无字段受限</Tag>
                  ) : (
                    enforced.map((col) => (
                      <Tag key={col.field} color={col.classification_level === 4 ? 'red' : 'orange'} style={{ marginInlineEnd: 4 }}>
                        {col.field} {col.classification_level === 4 ? '禁止出域' : '强制掩码'}
                      </Tag>
                    ))
                  )}
                </Text>
              </Space>
            }
          />
          <Table
            size="small"
            rowKey="field"
            columns={columns}
            dataSource={result.columns}
            pagination={false}
            scroll={{ x: 'max-content', y: 420 }}
          />
        </>
      )}
    </Modal>
  );
}
