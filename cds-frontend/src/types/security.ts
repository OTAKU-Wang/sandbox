export interface SecurityFinding {
  stage: string;
  severity: string;
  type: string;
  message: string;
  samples?: string[];
}

export interface SecurityReport {
  passed?: boolean;
  blocked?: boolean;
  stage_results?: Record<string, boolean>;
  findings?: SecurityFinding[];
  findings_count?: number;
  max_severity?: string | null;
  dp_applied?: boolean;
  watermark?: string | null;
  signature?: string | null;
  redacted_output?: string | null;
  error?: string;
}
