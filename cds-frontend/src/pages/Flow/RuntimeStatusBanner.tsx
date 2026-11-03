import { Alert, Tooltip } from 'antd';
import { useFlowRuntimeStatus } from '../../services/flow';

/**
 * 运行时不可用的诚实披露（禁伪造铁律：没有 L2' 就必须上屏说）。
 * 但原型 2-1/2-2/2-3 三屏标题卡下面都没有横幅——披露压成**一行**，
 * 正文起点才贴得上图的 y≈249；细节（节点数、其它档位以各自探测为准）收进 Tooltip，
 * 一个字都不丢。
 */
export function RuntimeStatusBanner() {
  const { data, isError } = useFlowRuntimeStatus();
  if (isError || !data || data.available) return null;
  const counts = data.total_nodes > 0 ? `已注册节点 ${data.total_nodes} 个，在线 ${data.ready_nodes} 个` : '';
  const head = data.message ?? data.reason ?? '沙箱运行时不可用。';
  const detail = (
    <span>
      {head}
      {counts ? `（${counts}）` : ''}
      <span style={{ color: '#8c8c8c' }}>平台管理与编排功能正常运行，沙箱计算任务将在计算节点就绪后执行。</span>
    </span>
  );
  return (
    <Tooltip title={detail} styles={{ root: { maxWidth: 520 } }}>
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 12 }}
        message={
          <span style={{ display: 'block', whiteSpace: 'nowrap' }}>
            当前未接入可用的机密计算算力节点（L2&apos;）
            <span style={{ color: '#8c8c8c', marginLeft: 8 }}>悬停查看算力节点状态</span>
          </span>
        }
      />
    </Tooltip>
  );
}
