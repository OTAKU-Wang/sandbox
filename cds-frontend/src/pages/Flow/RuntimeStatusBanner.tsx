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
  const counts = data.total_nodes > 0 ? `已注册节点 ${data.total_nodes} 个，online ${data.ready_nodes} 个` : '';
  const head = data.message ?? data.reason ?? '沙箱运行时不可用。';
  const detail = (
    <span>
      {head}
      {counts ? `（${counts}）` : ''}
      {/* 只说 MicroVM 这一层：其余等级能不能真跑，以开发环/会话页自己的探测为准。 */}
      <span style={{ color: '#8c8c8c' }}>其它隔离等级是否可用，以开发环与沙箱会话页的实际探测结果为准。</span>
    </span>
  );
  return (
    <Tooltip title={detail} styles={{ root: { maxWidth: 520 } }}>
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 12 }}
        message={
          // 真值：三屏标题卡下都没有横幅 ⇒ 只能占一行（下一块顶沿要贴 y≈249）。
          // 但 `head` 是后端原文，长度不可控：上一版把它拼在行内 + text-overflow:ellipsis，
          // 实测 2-3 行内容宽 2136 / 容器 1124 ⇒ 诚实披露被裁掉一半（违反 honest degradation）。
          // 现在行内只留不会变长的结论句，`head`/节点数全文字都在下面的 Tooltip 里（一字不丢）。
          <span style={{ display: 'block', whiteSpace: 'nowrap' }}>
            没有可用的 MicroVM 运行时（L2&apos;）
            <span style={{ color: '#8c8c8c', marginLeft: 8 }}>完整运行时消息悬停查看</span>
          </span>
        }
      />
    </Tooltip>
  );
}
