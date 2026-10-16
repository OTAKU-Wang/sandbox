import { Alert } from 'antd';
import { useFlowRuntimeStatus } from '../../services/flow';

export function RuntimeStatusBanner() {
  const { data, isError } = useFlowRuntimeStatus();
  if (isError || !data || data.available) return null;
  const counts = data.total_nodes > 0 ? `（已注册节点 ${data.total_nodes} 个，online ${data.ready_nodes} 个）` : '';
  return (
    <Alert
      type="warning"
      showIcon
      style={{ marginBottom: 12 }}
      message="当前部署无法真实执行代码"
      description={
        <span>
          {data.message ?? data.reason ?? '沙箱运行时不可用。'}
          {counts}
        </span>
      }
    />
  );
}
