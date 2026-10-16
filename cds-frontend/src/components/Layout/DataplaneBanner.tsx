import { useEffect, useState } from 'react';
import { Alert } from 'antd';
import api from '../../services/api';

interface DataplaneStatus {
  enabled: boolean;
  mode: 'full' | 'control_plane_only' | string;
  reason: string | null;
  note: string;
}

/**
 * SS-13 交付档全局横幅。
 *
 * 无 KVM / 未启用数据面的节点上，平台按「控制面档」交付：目录、契约、审批、审计、交付中心照常可用，
 * 沙箱执行不可用。这里把档位**提前说清楚**，而不是等用户点执行才吃到 400 RUNTIME_UNAVAILABLE。
 * 它只陈述档位，不做任何前端侧的降级判断——真正的拦截永远在后端。
 */
export default function DataplaneBanner() {
  const [dp, setDp] = useState<DataplaneStatus | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .get('/meta/capability-matrix')
      .then((resp) => {
        if (alive) setDp(((resp.data ?? {}) as { dataplane?: DataplaneStatus }).dataplane ?? null);
      })
      .catch(() => {
        // 探针不可达时保持沉默：执行入口自己会如实报错，横幅不该再叠一条错误噪音
      });
    return () => {
      alive = false;
    };
  }, []);

  if (!dp || dp.enabled) return null;

  return (
    <Alert
      type="warning"
      showIcon
      style={{ marginBottom: 16 }}
      message="当前部署为控制面档：沙箱执行不可用"
      description={dp.note}
      data-testid="dataplane-banner"
    />
  );
}
