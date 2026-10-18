import { useEffect, useState } from 'react';
import { Alert } from 'antd';
import { metaApi, type CapabilityMatrix } from '../../services/metaApi';

interface DataplaneStatus {
  enabled: boolean;
  mode: 'full' | 'control_plane_only' | string;
  reason: string | null;
  note: string;
}

/** 矩阵载荷上挂在档位声明（capability_matrix.get_capability_matrix 的第三个键）。
 * metaApi 的 CapabilityMatrix 只声明了 matrix/levels，档位是这里唯一的消费方，故就地扩展而不放宽共享类型。 */
type CapabilityMatrixWithDataplane = CapabilityMatrix & { dataplane?: DataplaneStatus };

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
    metaApi
      .getCapabilityMatrix()
      .then((matrix) => {
        if (alive) setDp(((matrix ?? {}) as CapabilityMatrixWithDataplane).dataplane ?? null);
      })
      .catch((err) => {
        // 探针失败刻意**不**再叠一条错误 Alert：执行入口自己会如实报 400 RUNTIME_UNAVAILABLE，
        // 全局横幅不该重复噪音。但「档位=控制面」与「探针失败」是两种不同的事实，
        // 静默会让控制面档 + 401/网关错误看起来像完整部署，所以失败必须可诊断：warn 一次。
        console.warn('[DataplaneBanner] 能力矩阵探针失败，无法确认数据面档位', err);
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
      description={dp.reason ? `${dp.note}（原因：${dp.reason}）` : dp.note}
      data-testid="dataplane-banner"
    />
  );
}
