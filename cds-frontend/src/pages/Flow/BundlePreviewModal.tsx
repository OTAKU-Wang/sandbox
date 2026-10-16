import { Alert, Button, Descriptions, Modal, Table, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { flowApi, flowKeys } from '../../services/flow';
import { CenteredLoading, QueryErrorAlert, getErrorMessage } from '../../components/Feedback/QueryFeedback';
import { parseCsvPreview } from '../../utils/csvPreview';

const { Text } = Typography;

interface Props {
  bundleId: string | null;
  onClose: () => void;
}

export default function BundlePreviewModal({ bundleId, onClose }: Props) {
  const previewQuery = useQuery({
    queryKey: [...flowKeys.bundles(null, null, null), 'preview', bundleId],
    queryFn: () => flowApi.previewBundle(bundleId!),
    enabled: !!bundleId,
    retry: 0,
  });

  const body = previewQuery.data;
  const preview = parseCsvPreview(body?.data);
  const columns = preview.columns.map((c) => ({
    title: c === '_extra' ? '未命名列' : c,
    dataIndex: c,
    key: c,
    ellipsis: true,
    render: (v: string) => (v === '' || v == null ? <Text type="secondary">-</Text> : v),
  }));

  return (
    <Modal
      title={`成果预览${body?.member ? ` · ${body.member}` : ''}`}
      open={!!bundleId}
      onCancel={onClose}
      footer={<Button onClick={onClose}>关闭</Button>}
      width={960}
    >
      {previewQuery.isPending && <CenteredLoading />}
      {previewQuery.isError && (
        <QueryErrorAlert
          error={previewQuery.error}
          message={`预览不可用：${getErrorMessage(previewQuery.error)}`}
          onRetry={() => { void previewQuery.refetch(); }}
        />
      )}
      {body && !body.available && (
        <Alert
          type="info"
          showIcon
          message={body.reason ?? '这个成果包没有可预览的表格内容'}
          description={
            body.members?.length
              ? `包内文件：${body.members.join('、')}`
              : '包内没有任何文件。'
          }
        />
      )}
      {body && body.available && (
        <>
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message="预览只回前若干行，且后端已对这段内容重新跑过出口审查；完整成果请下载成果包。"
            description={
              <Descriptions size="small" column={2} style={{ marginTop: 4 }}>
                <Descriptions.Item label="审查范围">{body.output_review?.bundle_scope ?? '未下发'}</Descriptions.Item>
                <Descriptions.Item label="打包时审查">
                  {body.output_review?.file_status ?? '未下发'}
                  {typeof body.output_review?.file_findings === 'number' ? ` · ${body.output_review?.file_findings} 项发现` : ''}
                </Descriptions.Item>
                <Descriptions.Item label="预览再审查">
                  {body.output_review?.preview_findings ?? 0} 项发现
                </Descriptions.Item>
                <Descriptions.Item label="行数">
                  {body.rows_returned} / {body.row_count_total}
                </Descriptions.Item>
              </Descriptions>
            }
          />
          {preview.truncated && <Tag style={{ marginBottom: 8 }}>已截断展示</Tag>}
          {preview.reason ? (
            <Alert type="info" showIcon message={preview.reason} />
          ) : (
            <Table
              size="small"
              rowKey={(_, idx) => String(idx)}
              columns={columns}
              dataSource={preview.rows}
              pagination={false}
              scroll={{ x: 'max-content', y: 420 }}
            />
          )}
        </>
      )}
    </Modal>
  );
}
