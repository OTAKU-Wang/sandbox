import { useEffect, useState } from 'react';
import { Alert } from 'antd';
import { metaApi, type DataplaneStatus } from '../../services/metaApi';

/**
 * SS-13 交付档全局横幅。
 *
 * 无 KVM / 未启用数据面的节点上，平台按「控制面档」交付：目录、契约、审批、审计、交付中心照常可用，
 * 沙箱执行不可用。这里把档位**提前说清楚**，而不是等用户点执行才吃到 400 RUNTIME_UNAVAILABLE。
 * 它只陈述档位，不做任何前端侧的降级判断——真正的拦截永远在后端。
 */
/**
 * A300：横幅那句话由后端声明的 `dataplane.mode` 决定，而不是从「enabled 为假」倒推档位。
 *
 * 今天 `capability_matrix.py:350` 给的是 `mode = "full" if enabled else "control_plane_only"`，
 * 两者等价，所以旧写法看不出差别。但 `mode` 才是后端对**交付档**的正面声明：将来加第三档
 * （数据面在、但降级）时，只看 `enabled` 的这一支会继续把话说成「控制面档」——那是替后端编档位。
 * 认不出的 mode 照实说未记录并带上原值，不套进已有话术（honest degradation 的同一条口径）。
 */
const MODE_HEADLINE: Record<string, string> = {
  control_plane_only: '当前部署为控制面档：沙箱执行不可用',
  // mode 说 full 而 enabled 为假：两个字段互相打脸，那就把两个事实一起摆出来，不替谁圆场。
  full: '数据面未启用（后端档位声明为 full，与此矛盾）：沙箱执行不可用',
};

export default function DataplaneBanner() {
  const [dp, setDp] = useState<DataplaneStatus | null>(null);

  useEffect(() => {
    let alive = true;
    metaApi
      .getCapabilityMatrix()
      .then((matrix) => {
        if (alive) setDp(matrix?.dataplane ?? null);
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
      message={MODE_HEADLINE[dp.mode]
        ?? `数据面未启用（后端档位声明：${dp.mode || '未记录'}，本屏尚无对应话术）：沙箱执行不可用`}
      description={dp.reason ? `${dp.note}（原因：${dp.reason}）` : dp.note}
      data-testid="dataplane-banner"
    />
  );
}
