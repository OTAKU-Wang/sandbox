import { useEffect, useState } from 'react';
import { Alert, Card, Steps, Tag, Typography } from 'antd';
import type { SandboxSession } from '../../types/models';
import type { SessionLogEntry } from '../../services/sandboxApi';
import { SESSION_STATUS_META, formatDuration } from './sessionWorkbench';

const { Text } = Typography;

/** 会话状态机可视化（PZ-16 ②）—
 * pending → key_distributing → ready → running → 终态（suspended/completed/terminated/failed）
 * + 步骤耗时（来自 session 的 created_at/started_at/ended_at）
 * + 失败态：error_message + 审计尾（/logs 最新 5 条）+ request_id（若审计尾 detail 携带）。
 */
export default function SessionStatusStrip({
  session,
  auditTail,
}: {
  session: SandboxSession;
  auditTail?: SessionLogEntry[];
}) {
  const started = session.started_at ? new Date(session.started_at).getTime() : null;
  const ended = session.ended_at ? new Date(session.ended_at).getTime() : null;
  const created = session.created_at ? new Date(session.created_at).getTime() : null;

  // 运行中会话的耗时需要时钟推进：用定时器订阅（而非渲染期 Date.now()，保持组件纯函数）
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (ended !== null) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [ended]);

  const provisionMs = created !== null && started !== null ? started - created : null;
  const runningMs =
    started !== null ? (ended !== null ? ended - started : now - started) : null;
  const totalMs = created !== null ? ((ended ?? now) - created) : null;

  let current = 0;
  let stepStatus: 'process' | 'finish' | 'error' | 'wait' = 'process';
  const status = session.status;
  if (status === 'key_distributing') current = 1;
  else if (status === 'ready') current = 2;
  else if (status === 'running' || status === 'suspended') current = 3;
  else if (status === 'failed') { current = 4; stepStatus = 'error'; }
  else if (['completed', 'terminated', 'revoked', 'expired'].includes(status)) { current = 4; stepStatus = 'finish'; }

  const isFailed = status === 'failed';
  const tail = (auditTail ?? []).slice(0, 5);
  const tailRequestId = tail
    .map((e) => (e.detail && typeof e.detail === 'object' ? (e.detail as Record<string, unknown>).request_id : undefined))
    .find((v) => typeof v === 'string');

  return (
    <Card size="small" style={{ marginBottom: 16, border: '1px solid #d9d9d9' }}>
      <Steps
        size="small"
        status={stepStatus === 'error' ? 'error' : 'process'}
        current={current}
        items={[
          {
            title: '待处理',
            description: 'pending / provisioning',
          },
          {
            title: '密钥分发',
            description: 'key_distributing',
          },
          {
            title: '就绪',
            description: `ready（含前序阶段 ${formatDuration(provisionMs)}）`,
          },
          {
            title: '运行中',
            description: `running${status === 'suspended' ? '（已暂停）' : ''} · ${formatDuration(runningMs)}`,
          },
          {
            title: '终态',
            description: (
              <span>
                <Tag color={SESSION_STATUS_META[status]?.color ?? 'default'}>
                  {SESSION_STATUS_META[status]?.label ?? status}
                </Tag>
                总耗时 {formatDuration(totalMs)}
              </span>
            ),
          },
        ]}
      />
      {isFailed && (
        <Alert
          type="error"
          showIcon
          style={{ marginTop: 12 }}
          message={
            <span>
              会话失败：{session.error_message || '未提供错误信息'}
              {tailRequestId && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {' '}· request_id: <Text code copyable={{ text: tailRequestId }}>{tailRequestId}</Text>
                </Text>
              )}
            </span>
          }
          description={
            tail.length > 0 ? (
              <div>
                <Text type="secondary" style={{ fontSize: 12 }}>审计尾（最近 {tail.length} 条）：</Text>
                <pre
                  style={{
                    margin: '8px 0 0',
                    padding: 8,
                    background: '#fff1f0',
                    border: '1px solid #ffccc7',
                    borderRadius: 4,
                    fontSize: 12,
                    overflow: 'auto',
                    maxHeight: 160,
                  }}
                >
                  {tail
                    .map((e) => {
                      const d = e.detail && typeof e.detail === 'object'
                        ? JSON.stringify(e.detail, null, 0)
                        : String(e.detail ?? '');
                      return `${new Date(e.created_at).toLocaleString()}  ${e.action}  ${d.slice(0, 300)}`;
                    })
                    .join('\n')}
                </pre>
              </div>
            ) : (
              <Text type="secondary">审计轨迹为空（尚无 /logs 条目）。</Text>
            )
          }
        />
      )}
    </Card>
  );
}
