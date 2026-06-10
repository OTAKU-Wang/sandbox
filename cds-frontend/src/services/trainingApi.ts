import api from './api';

export interface TrainingJob {
  job_id: string;
  contract_id?: string;
  session_id?: string;
  job_type?: string;
  base_model?: string;
  model_name?: string;
  status: string;
  config: TrainingConfig;
  metrics?: Record<string, unknown> | null;
  output_path?: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  error_message: string | null;
}

export interface TrainingConfig {
  learning_rate: number;
  epochs: number;
  batch_size: number;
  max_seq_length?: number;
  warmup_steps?: number;
  weight_decay?: number;
  gradient_accumulation_steps?: number;
  max_grad_norm?: number;
  model_name?: string;
  dp_config?: DPConfig;
}

export interface DPConfig {
  noise_multiplier: number;
  max_grad_norm: number;
  delta: number;
}

export interface ValidationIssue {
  field: string;
  severity: 'error' | 'warning' | 'info';
  message: string;
  current_value: string;
  expected_range: string;
}

export interface ValidationResult {
  valid: boolean;
  issues: ValidationIssue[];
  warnings: number;
  errors: number;
}

export interface AuditEntry {
  entry_id: string;
  event_type: string;
  job_id: string;
  timestamp: string;
  actor: string;
  details: Record<string, unknown>;
  prev_hash: string;
  entry_hash: string;
}

export interface CheckpointInfo {
  checkpoint_id: string;
  job_id: string;
  epoch: number;
  step: number;
  plaintext_hash: string;
  ciphertext_hash: string;
  size_bytes: number;
  created_at: string;
}

interface TrainingJobResponse {
  job_id: string;
  job_type?: string;
  status: string;
  base_model?: string;
  config?: TrainingConfig;
  metrics?: Record<string, unknown> | null;
  output_path?: string | null;
  error_message?: string | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
}

const normalizeJob = (job: TrainingJobResponse): TrainingJob => ({
  job_id: job.job_id,
  job_type: job.job_type,
  base_model: job.base_model,
  model_name: job.config?.model_name || job.base_model,
  status: job.status,
  config: job.config || {
    model_name: job.base_model,
    learning_rate: 0,
    epochs: 0,
    batch_size: 0,
  },
  metrics: job.metrics,
  output_path: job.output_path,
  error_message: job.error_message || null,
  created_at: job.created_at,
  started_at: job.started_at || null,
  completed_at: job.completed_at || null,
});

const validateTrainingConfig = (config: TrainingConfig): ValidationResult => {
  const issues: ValidationIssue[] = [];
  const add = (field: string, severity: ValidationIssue['severity'], message: string, current_value: unknown, expected_range: string) => {
    issues.push({ field, severity, message, current_value: String(current_value ?? ''), expected_range });
  };

  if (!config.learning_rate || config.learning_rate <= 0 || config.learning_rate > 1) {
    add('learning_rate', 'error', '学习率必须在合理范围内', config.learning_rate, '(0, 1]');
  } else if (config.learning_rate > 0.01) {
    add('learning_rate', 'warning', '学习率偏高，可能导致训练不稳定', config.learning_rate, '<= 0.01');
  }
  if (!config.epochs || config.epochs < 1 || config.epochs > 1000) {
    add('epochs', 'error', 'Epochs 必须在允许范围内', config.epochs, '1 - 1000');
  }
  if (!config.batch_size || config.batch_size < 1 || config.batch_size > 4096) {
    add('batch_size', 'error', 'Batch Size 必须在允许范围内', config.batch_size, '1 - 4096');
  }
  if (config.max_seq_length && (config.max_seq_length < 1 || config.max_seq_length > 32768)) {
    add('max_seq_length', 'warning', '序列长度过大可能造成显存或内存压力', config.max_seq_length, '1 - 32768');
  }
  if (config.dp_config) {
    if (config.dp_config.noise_multiplier <= 0) {
      add('dp_config.noise_multiplier', 'error', 'DP 噪声乘数必须大于 0', config.dp_config.noise_multiplier, '> 0');
    }
    if (config.dp_config.max_grad_norm <= 0) {
      add('dp_config.max_grad_norm', 'error', 'DP 梯度裁剪必须大于 0', config.dp_config.max_grad_norm, '> 0');
    }
    if (config.dp_config.delta <= 0 || config.dp_config.delta >= 1) {
      add('dp_config.delta', 'error', 'DP delta 必须在 0 和 1 之间', config.dp_config.delta, '(0, 1)');
    }
  }

  return {
    valid: !issues.some((issue) => issue.severity === 'error'),
    issues,
    errors: issues.filter((issue) => issue.severity === 'error').length,
    warnings: issues.filter((issue) => issue.severity === 'warning').length,
  };
};

export const trainingApi = {
  // Training jobs
  listJobs: (params?: { contract_id?: string; status?: string }): Promise<TrainingJob[]> =>
    api.get('/training/jobs', { params }).then((res) => {
      const data = res as unknown as { items?: TrainingJobResponse[] } | TrainingJobResponse[];
      return (Array.isArray(data) ? data : data.items || []).map(normalizeJob);
    }),

  getJob: (id: string): Promise<TrainingJob> =>
    api.get(`/training/jobs/${id}`).then((res) => normalizeJob(res as unknown as TrainingJobResponse)),

  cancelJob: (id: string): Promise<TrainingJob> =>
    api.post(`/training/jobs/${id}/cancel`).then((res) => normalizeJob(res as unknown as TrainingJobResponse)),

  // Config validation
  validateConfig: (config: TrainingConfig): Promise<ValidationResult> =>
    Promise.resolve(validateTrainingConfig(config)),

  // Audit log
  getAuditLog: (job_id: string): Promise<AuditEntry[]> =>
    api.get(`/training/jobs/${job_id}/audit`),

  // Checkpoints
  listCheckpoints: (job_id: string): Promise<CheckpointInfo[]> =>
    api.get(`/training/jobs/${job_id}/checkpoints`),
};
