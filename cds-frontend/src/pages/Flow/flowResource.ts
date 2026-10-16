import type { FlowResourceField, SandboxResourceSpec } from '../../services/flow';
import { FLOW_RESOURCE_LIMITS } from '../../services/flow';

export const RESOURCE_FIELDS: FlowResourceField[] = ['cpu_cores', 'memory_mb', 'disk_gb', 'gpu_count', 'timeout_seconds'];

export const numberRangeRule = (label: string, min: number, max: number) => ({
  type: 'number' as const,
  min,
  max,
  message: `${label}需在 ${min} ~ ${max} 之间`,
});

export function formatResourceSpec(spec: SandboxResourceSpec | null | undefined, withImage = false): string {
  if (!spec) return '';
  const parts = RESOURCE_FIELDS.map((field) =>
    typeof spec[field] === 'number' ? `${FLOW_RESOURCE_LIMITS[field].label} ${spec[field]}` : null,
  ).filter(Boolean) as string[];
  if (withImage && spec.image_template) parts.push(`镜像 ${spec.image_template}`);
  return parts.join(' ｜ ');
}
