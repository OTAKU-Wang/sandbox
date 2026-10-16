import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Descriptions, Modal, Spin, Table, Tag, Typography } from 'antd';
import { auditApi, type MerkleProofStep } from '../../services/auditApi';
import { QueryErrorAlert, getErrorMessage } from '../Feedback/QueryFeedback';
import SimulationBadge, { simulationStateForValue } from '../Feedback/SimulationBadge';

const { Text } = Typography;

/** PZ-06 链上锚定口径 → 人话；原始披露值同时以 code 原样展示，不做翻译替换。 */
const CHAIN_STATE_TEXT: Record<string, string> = {
  'pg-log': '本地 PG 追加日志（防篡改，未上链）',
  'fisco-sim': 'FISCO BCOS 模拟锚定',
  'ant-sim': '蚂蚁链模拟锚定',
  not_configured: '未配置链后端',
};

export interface AuditVerifyModalProps {
  recordId: string | null;
  open: boolean;
  onClose: () => void;
}

type VerdictLevel = 'ok' | 'degraded' | 'failed' | 'unanchored';

interface Verdict {
  level: VerdictLevel;
  title: string;
  description: string;
}

/**
 * 判定验真结论。铁律：只有「后端重算比对通过」且「锚定后端确为联盟链」才是成功态；
 * 本地 PG 追加日志、模拟链、任何降级路径一律降级为 warning，绝不显示绿色「验证通过」。
 */
function deriveVerdict(
  data: Awaited<ReturnType<typeof auditApi.verify>> | undefined,
  proofError: unknown,
): Verdict {
  if (!data) {
    return { level: 'failed', title: '暂无法判定', description: '后端未返回验真结果，请勿据此认定存证有效。' };
  }
  if (!data.tx_hash) {
    return {
      level: 'unanchored',
      title: '该记录尚未锚定存证',
      description: data.reason || '记录未锚定到区块链，无 tx_hash，无法验真。',
    };
  }
  if (!data.verified) {
    return {
      level: 'failed',
      title: '验真未通过',
      description:
        '后端按确定性叶子顺序重算 SM3 与 Merkle root 后，与存证记录中的 root 不一致，或找不到匹配的锚定记录。该记录的完整性不可信，请立即上报。',
    };
  }
  const backend = data.backend;
  if (!backend) {
    return {
      level: 'degraded',
      title: 'root 比对一致，但存证后端未知',
      description: '后端未披露锚定后端（backend=null），无法证明该 root 来自可信锚定源，不作成功结论。',
    };
  }
  if (!backend.is_consortium_chain) {
    return {
      level: 'degraded',
      title: '本地防篡改校验通过（非联盟链上链）',
      description:
        '重算的 Merkle root 与本地存证记录一致，但锚定后端不是联盟链：这只是平台自身的追加式防篡改日志，不构成第三方/链上证据。存证后端见下方披露。',
    };
  }
  if (proofError) {
    return {
      level: 'degraded',
      title: 'root 比对一致，但 Merkle 路径获取失败',
      description:
        `${getErrorMessage(proofError, 'Merkle 路径接口调用失败')} —— 无法出具可离线复核的审计路径，验真结论不完整，不作成功结论。`,
    };
  }
  return {
    level: 'ok',
    title: '联盟链存证验真通过',
    description: '后端重新计算 SM3 叶子与 Merkle root，并与链上锚定值比对一致。',
  };
}

const VERDICT_ALERT_TYPE: Record<VerdictLevel, 'success' | 'warning' | 'error' | 'info'> = {
  ok: 'success',
  degraded: 'warning',
  failed: 'error',
  unanchored: 'info',
};

function DisclosureBlock({ backend }: { backend: NonNullable<Awaited<ReturnType<typeof auditApi.verify>>['backend']> }) {
  const anchorState = backend.chain_anchoring;
  return (
    <Descriptions column={1} bordered size="small" title="存证后端（诚实披露）">
      <Descriptions.Item label="后端">
        {backend.backend_label} <Text code>{backend.backend}</Text>
      </Descriptions.Item>
      <Descriptions.Item label="是否联盟链">
        {backend.is_consortium_chain ? (
          <Tag color="green">是</Tag>
        ) : (
          <Tag color="gold">否</Tag>
        )}{' '}
        <Text code>{String(backend.is_consortium_chain)}</Text>
      </Descriptions.Item>
      {anchorState ? (
        <Descriptions.Item label="链上锚定状态">
          <SimulationBadge
            state={simulationStateForValue(anchorState) ?? 'unsupported'}
            label={CHAIN_STATE_TEXT[anchorState] ?? anchorState}
          />{' '}
          <Text code>{anchorState}</Text>
        </Descriptions.Item>
      ) : (
        <Descriptions.Item label="链上锚定状态">
          <Text type="secondary">后端未披露 chain_anchoring 字段</Text>
        </Descriptions.Item>
      )}
    </Descriptions>
  );
}

const proofColumns = [
  {
    title: '层级',
    dataIndex: 'step',
    key: 'step',
    width: 70,
    render: (_: unknown, __: MerkleProofStep, index: number) => index + 1,
  },
  {
    title: '兄弟节点哈希（SM3）',
    dataIndex: 'hash',
    key: 'hash',
    render: (hash: string) => (
      <Text code copyable={{ text: hash }} style={{ wordBreak: 'break-all' }}>
        {hash}
      </Text>
    ),
  },
  {
    title: '位置',
    dataIndex: 'position',
    key: 'position',
    width: 90,
    render: (position: MerkleProofStep['position']) => (position === 'left' ? '左兄弟' : '右兄弟'),
  },
];

/**
 * 存证验真弹窗：调用 GET /audit/verify/{id} 与 GET /audit/merkle-proof/{id}，
 * 展示后端真实重算结果、叶子哈希、Merkle 路径、锚定交易/区块高度，以及诚实披露块。
 */
export default function AuditVerifyModal({ recordId, open, onClose }: AuditVerifyModalProps) {
  const enabled = open && !!recordId;

  const verifyQuery = useQuery({
    queryKey: ['audit-verify', recordId],
    queryFn: () => auditApi.verify(recordId as string),
    enabled,
    retry: 0,
  });

  const anchored = !!verifyQuery.data?.tx_hash;

  const proofQuery = useQuery({
    queryKey: ['audit-merkle-proof', recordId],
    queryFn: () => auditApi.getMerkleProof(recordId as string),
    enabled: enabled && anchored,
    retry: 0,
  });

  const verdict = deriveVerdict(verifyQuery.data, proofQuery.isError ? proofQuery.error : null);
  const verifyData = verifyQuery.data;
  const proofData = proofQuery.data;
  const backend = verifyData?.backend ?? proofData?.backend ?? null;
  const blockNumber = verifyData?.block_number ?? proofData?.block_number ?? null;
  const anchorId = verifyData?.anchor_id ?? proofData?.anchor_id ?? null;
  const confirmed = verifyData?.confirmed ?? proofData?.confirmed ?? false;
  const txHash = verifyData?.tx_hash ?? proofData?.tx_hash ?? null;
  const leafHash = proofData?.record_hash ?? null;
  const root = verifyData?.merkle_root ?? proofData?.merkle_root ?? null;

  return (
    <Modal
      title="存证验真"
      open={open}
      onCancel={onClose}
      width={760}
      footer={[
        <Button key="close" onClick={onClose}>
          关闭
        </Button>,
      ]}
    >
      {verifyQuery.isFetching && !verifyQuery.data ? (
        <Spin tip="正在向存证后端验真" style={{ display: 'block', margin: '48px auto' }} />
      ) : null}

      {verifyQuery.isError ? (
        <QueryErrorAlert error={verifyQuery.error} message="验真请求失败" onRetry={() => verifyQuery.refetch()} />
      ) : null}

      {verifyData ? (
        <>
          <Alert
            type={VERDICT_ALERT_TYPE[verdict.level]}
            showIcon
            message={verdict.title}
            description={verdict.description}
            style={{ marginBottom: 16 }}
          />

          {verifyData?.verification_note ? (
            <Alert
              type={verifyData.backend && !verifyData.backend.is_consortium_chain ? 'warning' : 'info'}
              message="后端验真说明"
              description={verifyData.verification_note}
              style={{ marginBottom: 16 }}
            />
          ) : null}

          <Descriptions column={1} bordered size="small" title="验真结果" style={{ marginBottom: 16 }}>
            <Descriptions.Item label="记录 ID">
              <Text code copyable={{ text: verifyData.record_id }}>{verifyData.record_id}</Text>
            </Descriptions.Item>
            <Descriptions.Item label="叶子哈希（SM3）">
              {leafHash ? (
                <Text code copyable={{ text: leafHash }} style={{ wordBreak: 'break-all' }}>
                  {leafHash}
                </Text>
              ) : (
                <Text type="secondary">
                  {proofQuery.isFetching ? '获取中…' : '未获取到记录哈希（见下方 Merkle 路径错误）'}
                </Text>
              )}
            </Descriptions.Item>
            <Descriptions.Item label="重算 Merkle root">
              {root ? (
                <Text code copyable={{ text: root }} style={{ wordBreak: 'break-all' }}>
                  {root}
                </Text>
              ) : (
                <Text type="secondary">无（该记录未参与任何 Merkle 批次）</Text>
              )}
            </Descriptions.Item>
            <Descriptions.Item label="存证交易哈希">
              {txHash ? (
                <Text code copyable={{ text: txHash }} style={{ wordBreak: 'break-all' }}>
                  {txHash}
                </Text>
              ) : (
                <Text type="secondary">未锚定</Text>
              )}
            </Descriptions.Item>
            <Descriptions.Item label="锚定 ID">
              {anchorId ? <Text code copyable={{ text: anchorId }}>{anchorId}</Text> : <Text type="secondary">无</Text>}
            </Descriptions.Item>
            <Descriptions.Item label="区块高度">
              {blockNumber === null || blockNumber === undefined ? (
                <Text type="secondary">后端未提供区块高度（本地追加日志无链上高度）</Text>
              ) : (
                <Text code>{blockNumber}</Text>
              )}
            </Descriptions.Item>
            <Descriptions.Item label="锚定确认状态">
              {confirmed ? <Tag color="green">已确认</Tag> : <Tag>未确认</Tag>}
            </Descriptions.Item>
          </Descriptions>

          {anchored ? (
            <div style={{ marginBottom: 16 }}>
              <Text strong style={{ display: 'block', marginBottom: 8 }}>
                Merkle 审计路径
              </Text>
              {proofQuery.isFetching && !proofQuery.data ? <Spin style={{ display: 'block', margin: '16px auto' }} /> : null}
              {proofQuery.isError ? (
                <QueryErrorAlert
                  error={proofQuery.error}
                  message="Merkle 路径获取失败"
                  onRetry={() => proofQuery.refetch()}
                />
              ) : null}
              {proofQuery.data ? (
                <>
                  <Text type="secondary" style={{ display: 'block', marginBottom: 8 }}>
                    叶子序号 {proofQuery.data.leaf_index}（自 0 起），本批次共 {proofQuery.data.total_leaves} 片叶子
                  </Text>
                  {proofQuery.data.proof_path.length === 0 ? (
                    <Alert
                      type="info"
                      showIcon
                      message="本批次仅此一条记录，Merkle root 即叶子哈希本身，无需兄弟节点。"
                    />
                  ) : (
                    <Table<MerkleProofStep>
                      rowKey={(_, index) => String(index)}
                      size="small"
                      pagination={false}
                      columns={proofColumns}
                      dataSource={proofQuery.data.proof_path}
                    />
                  )}
                </>
              ) : null}
            </div>
          ) : null}

          {backend ? (
            <DisclosureBlock backend={backend} />
          ) : anchored ? (
            <Alert
              type="warning"
              showIcon
              message="未取得存证后端披露信息"
              description="后端响应中 backend 字段为 null：无法判断本次存证使用的是联盟链、本地日志还是模拟实现，因此不作任何成功结论。"
            />
          ) : null}
        </>
      ) : null}
    </Modal>
  );
}
