import { Alert, Button, Modal, Table, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { dataResourceApi } from '../../services/dataResourceApi';
import { CenteredLoading, QueryErrorAlert, getErrorMessage } from '../../components/Feedback/QueryFeedback';
import { parseCsvPreview } from '../../utils/csvPreview';

const { Text } = Typography;

interface Props {
  resourceId: string | null;
  resourceName?: string;
  onClose: () => void;
}

export default function DesensitizedPreviewModal({ resourceId, resourceName, onClose }: Props) {
  const previewQuery = useQuery({
    queryKey: ['data-resource-desensitize', resourceId],
    queryFn: () => dataResourceApi.desensitize(resourceId!),
    enabled: !!resourceId,
    retry: 0,
  });

  const payload = previewQuery.data;
  const preview = parseCsvPreview(payload?.data);
  const columns = preview.columns.map((c) => ({
    title: c === '_extra' ? '未命名列' : c,
    dataIndex: c,
    key: c,
    ellipsis: true,
    render: (v: string) => (v === '' || v == null ? <Text type="secondary">-</Text> : v),
  }));

  return (
    <Modal
      title={`脱敏预览${resourceName ? ` · ${resourceName}` : ''}`}
      open={!!resourceId}
      onCancel={onClose}
      footer={<Button onClick={onClose}>关闭</Button>}
      width={960}
    >
      {previewQuery.isPending && <CenteredLoading />}
      {previewQuery.isError && (
        <QueryErrorAlert
          error={previewQuery.error}
          message={`脱敏预览失败：${getErrorMessage(previewQuery.error)}`}
          onRetry={() => { void previewQuery.refetch(); }}
        />
      )}
      {payload && (
        <>
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message={
              payload.masking_applied
                ? '以下为脱敏后的样例数据：PII 字段已按策略掩码，不是原始明文，仅用于确认结构与作用域。'
                : '后端未标记本次结果已脱敏，请谨慎对待其中内容。'
            }
            description={
              <Text type="secondary" style={{ fontSize: 12 }}>
                本次返回 {payload.row_count ?? preview.totalRows} 行 · 格式 {payload.format ?? 'csv'}
              </Text>
            }
          />
          {preview.reason ? (
            <Alert type="info" showIcon message={preview.reason} />
          ) : (
            <>
              <div style={{ marginBottom: 8 }}>
                <Tag color="processing">展示 {preview.rows.length} / {preview.totalRows} 行</Tag>
                {preview.truncated && <Tag>已截断展示，完整数据请下载或在工作台内使用</Tag>}
              </div>
              <Table
                size="small"
                rowKey={(_, idx) => String(idx)}
                columns={columns}
                dataSource={preview.rows}
                pagination={false}
                scroll={{ x: 'max-content', y: 420 }}
              />
            </>
          )}
        </>
      )}
    </Modal>
  );
}
