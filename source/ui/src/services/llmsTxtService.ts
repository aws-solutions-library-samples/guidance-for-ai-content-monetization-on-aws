import { fetchAuthSession, getCurrentUser } from 'aws-amplify/auth'

export type LlmsTxtRunStatus =
  | 'queued'
  | 'processing'
  | 'completed'
  | 'failed'
  | 'no_change'

export type LlmsTxtDraftStatus = 'staged' | 'in_review' | 'published' | 'superseded'
export type LlmsTxtSource = 'manual' | 'pipeline'

export interface LlmsTxtDraft {
  run_id: string
  status: LlmsTxtDraftStatus
  source: LlmsTxtSource
  revision: number
  etag: string
  content: string
  diff?: string
  diff_summary?: {
    added_lines: number
    removed_lines: number
  }
  snapshot_fingerprint?: string
  renderer_revision?: string | null
  updated_at?: string
  published_at?: string
}

export interface LlmsTxtRun {
  run_id: string
  status: LlmsTxtRunStatus
  source?: LlmsTxtSource
  created_at: string
  updated_at?: string
  started_at?: string
  completed_at?: string
  content_count?: number
  batch_count?: number
  snapshot_fingerprint?: string
  renderer_revision?: string | null
  draft?: LlmsTxtDraft
  error_code?: string
  error_message?: string
}

export interface LlmsTxtPublishedManifest {
  content: string
  run_id?: string | null
  revision?: number | null
  snapshot_fingerprint?: string | null
  renderer_revision?: string | null
  published_at?: string | null
}

export interface LlmsTxtManagementState {
  published: LlmsTxtPublishedManifest
  active_run: LlmsTxtRun | null
  draft: LlmsTxtDraft | null
}

export interface LlmsTxtRunStartResponse {
  run: LlmsTxtRun
  already_running: boolean
}

export interface LlmsTxtClient {
  getState(): Promise<LlmsTxtManagementState>
  startRun(): Promise<LlmsTxtRunStartResponse>
  getRun(runId: string): Promise<LlmsTxtRun>
  saveDraft(runId: string, content: string, etag: string): Promise<LlmsTxtDraft>
  publishDraft(runId: string, etag: string): Promise<LlmsTxtDraft>
}

interface LlmsTxtErrorBody {
  error?: string
  message?: string
  details?: string
}

export class LlmsTxtApiError extends Error {
  readonly status: number
  readonly details?: string

  constructor(message: string, status: number, details?: string) {
    super(message)
    this.name = 'LlmsTxtApiError'
    this.status = status
    this.details = details
  }
}

export class LlmsTxtService implements LlmsTxtClient {
  private readonly apiBaseUrl: string

  constructor(apiBaseUrl = import.meta.env.VITE_API_BASE_URL || 'https://api.example.com') {
    this.apiBaseUrl = apiBaseUrl.replace(/\/+$/, '')
  }

  async getState(): Promise<LlmsTxtManagementState> {
    return this.request<LlmsTxtManagementState>('/content/llms-txt')
  }

  async startRun(): Promise<LlmsTxtRunStartResponse> {
    return this.request<LlmsTxtRunStartResponse>('/content/llms-txt/runs', {
      method: 'POST',
    })
  }

  async getRun(runId: string): Promise<LlmsTxtRun> {
    return this.request<LlmsTxtRun>(
      `/content/llms-txt/runs/${encodeURIComponent(runId)}`,
    )
  }

  async saveDraft(runId: string, content: string, etag: string): Promise<LlmsTxtDraft> {
    return this.request<LlmsTxtDraft>(
      `/content/llms-txt/drafts/${encodeURIComponent(runId)}`,
      {
        method: 'PUT',
        headers: { 'If-Match': etag },
        body: JSON.stringify({ content }),
      },
    )
  }

  async publishDraft(runId: string, etag: string): Promise<LlmsTxtDraft> {
    return this.request<LlmsTxtDraft>(
      `/content/llms-txt/drafts/${encodeURIComponent(runId)}/publish`,
      {
        method: 'POST',
        headers: { 'If-Match': etag },
      },
    )
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = await this.getAuthHeaders()
    const response = await fetch(`${this.apiBaseUrl}${path}`, {
      ...init,
      headers: {
        ...headers,
        ...init.headers,
      },
    })

    if (!response.ok) {
      throw await this.createApiError(response)
    }

    return response.json() as Promise<T>
  }

  private async getAuthHeaders(): Promise<Record<string, string>> {
    try {
      await getCurrentUser()
      const session = await fetchAuthSession()
      const token = session.tokens?.idToken?.toString()
      if (!token) {
        throw new Error('Cognito ID token is unavailable')
      }
      return {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      }
    } catch (error) {
      throw new LlmsTxtApiError(
        error instanceof Error ? error.message : 'Authentication is required',
        401,
      )
    }
  }

  private async createApiError(response: Response): Promise<LlmsTxtApiError> {
    let body: LlmsTxtErrorBody = {}
    try {
      body = (await response.json()) as LlmsTxtErrorBody
    } catch {
      // Some gateway failures return an empty or non-JSON body.
    }
    const message =
      body.error ||
      body.message ||
      `llms.txt request failed (HTTP ${response.status})`
    return new LlmsTxtApiError(message, response.status, body.details)
  }
}

export const llmsTxtService = new LlmsTxtService()
