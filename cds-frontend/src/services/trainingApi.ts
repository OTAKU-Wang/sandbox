import api from './api';

export interface TrainingJob {
  job_id: string;
  contract_id: string;
  session_id: string;
  model_name: string;
  status: string;
  config: TrainingConfig;
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

export const trainingApi = {
  // Training jobs
  listJobs: (params?: { contract_id?: string; status?: string }): Promise<TrainingJob[]> =>
    api.get('/training/jobs', { params }),

  getJob: (id: string): Promise<TrainingJob> =>
    api.get(`/training/jobs/${id}`),

  // Config validation
  validateConfig: (config: TrainingConfig): Promise<ValidationResult> =>
    api.post('/training/validate-config', config),

  // Audit log
  getAuditLog: (job_id: string): Promise<AuditEntry[]> =>
    api.get(`/training/jobs/${job_id}/audit`),

  // Checkpoints
  listCheckpoints: (job_id: string): Promise<CheckpointInfo[]> =>
    api.get(`/training/jobs/${job_id}/checkpoints`),
};
