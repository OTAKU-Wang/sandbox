import api from './api';

export interface RagDoc {
  doc_id: string;
  text: string;
}

export interface IngestCorpusRequest {
  session_id: string;
  docs: RagDoc[];
  chunk_size?: number;
  overlap?: number;
  embedding_backend?: 'auto' | 'tf' | 'transformers' | 'regex';
}

export interface IngestCorpusResult {
  corpus_id: string;
  doc_count: number;
  chunk_count: number;
  embedding_engine: string;
  dim: number;
  storage_object_ref: string;
  checksum: string;
  encrypted: boolean;
}

export interface CreateRagQueryTaskRequest {
  session_id: string;
  rag_query: string;
  timeout_seconds?: number;
  purpose?: string | null;
}

export interface CreatedTask {
  task_id: string;
  session_id: string;
  status: string;
  task_type: string;
  purpose: string | null;
  created_at: string;
}

export interface TaskSubmission {
  task_id: string;
  status: string;
  message: string;
}

export interface RagTaskResult {
  task_id: string;
  status: string;
  message?: string;
  redacted_output?: string | null;
  inspection?: Record<string, unknown> | null;
  rag_meta?: {
    embedding_engine: string;
    answer_mode: string;
    top_k: number;
    source_doc_ids: string[];
    answer_chars: number;
    truncated: boolean;
    backend: string;
  } | null;
}

/**
 * RAG phase-1 API (gap T11): in-domain retrieval-QA.
 * Corpus ingestion + RAG query tasks that reuse the sandbox task lifecycle
 * and the contract output gateway.
 */
export const ragApi = {
  ingestCorpus: (data: IngestCorpusRequest): Promise<IngestCorpusResult> =>
    api.post('/rag/corpus', data),

  createRagQueryTask: (data: CreateRagQueryTaskRequest): Promise<CreatedTask> =>
    api.post('/sandbox-tasks', null, {
      params: {
        session_id: data.session_id,
        task_type: 'rag_query',
        rag_query: data.rag_query,
        timeout_seconds: data.timeout_seconds ?? 3600,
        purpose: data.purpose ?? undefined,
      },
    }),

  submitTask: (sessionId: string, taskId: string): Promise<TaskSubmission> =>
    api.post(`/sandbox-tasks/${taskId}/submit`, null, { params: { session_id: sessionId } }),

  getTaskResult: (sessionId: string, taskId: string): Promise<RagTaskResult> =>
    api.get(`/sandbox-tasks/${taskId}/result`, { params: { session_id: sessionId } }),
};
