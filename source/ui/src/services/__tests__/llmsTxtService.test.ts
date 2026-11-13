import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('aws-amplify/auth', () => ({
  getCurrentUser: vi.fn(),
  fetchAuthSession: vi.fn(),
}))

import { fetchAuthSession, getCurrentUser } from 'aws-amplify/auth'
import {
  LlmsTxtApiError,
  LlmsTxtManagementState,
  LlmsTxtService,
} from '../llmsTxtService'

const managementState: LlmsTxtManagementState = {
  published: { content: '# Published' },
  active_run: null,
  draft: null,
}

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  )
}

describe('LlmsTxtService', () => {
  const service = new LlmsTxtService('https://api.example.test/')

  beforeEach(() => {
    vi.mocked(getCurrentUser).mockResolvedValue({ username: 'publisher' } as never)
    vi.mocked(fetchAuthSession).mockResolvedValue({
      tokens: {
        idToken: { toString: () => 'cognito-id-token' },
      },
    } as never)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it('gets management state with a Cognito ID token', async () => {
    const fetchMock = vi.fn().mockReturnValue(jsonResponse(managementState))
    vi.stubGlobal('fetch', fetchMock)

    await expect(service.getState()).resolves.toEqual(managementState)
    expect(getCurrentUser).toHaveBeenCalledOnce()
    expect(fetchAuthSession).toHaveBeenCalledOnce()
    expect(fetchMock).toHaveBeenCalledWith(
      'https://api.example.test/content/llms-txt',
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: 'Bearer cognito-id-token',
          'Content-Type': 'application/json',
        }),
      }),
    )
  })

  it('starts a generation run with POST', async () => {
    const response = {
      run: {
        run_id: 'llms_1',
        status: 'queued',
        created_at: '2026-08-28T12:00:00Z',
      },
      already_running: false,
    }
    const fetchMock = vi.fn().mockReturnValue(jsonResponse(response, 202))
    vi.stubGlobal('fetch', fetchMock)

    await expect(service.startRun()).resolves.toEqual(response)
    expect(fetchMock).toHaveBeenCalledWith(
      'https://api.example.test/content/llms-txt/runs',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('gets a run using an encoded run ID', async () => {
    const run = {
      run_id: 'llms/run 1',
      status: 'processing',
      created_at: '2026-08-28T12:00:00Z',
    }
    const fetchMock = vi.fn().mockReturnValue(jsonResponse(run))
    vi.stubGlobal('fetch', fetchMock)

    await service.getRun('llms/run 1')
    expect(fetchMock).toHaveBeenCalledWith(
      'https://api.example.test/content/llms-txt/runs/llms%2Frun%201',
      expect.any(Object),
    )
  })

  it('saves a draft with content and If-Match', async () => {
    const draft = {
      run_id: 'llms_1',
      status: 'in_review',
      source: 'manual',
      revision: 3,
      etag: '"rev-3"',
      content: '# Edited',
    }
    const fetchMock = vi.fn().mockReturnValue(jsonResponse(draft))
    vi.stubGlobal('fetch', fetchMock)

    await expect(service.saveDraft('llms_1', '# Edited', '"rev-2"')).resolves.toEqual(
      draft,
    )
    expect(fetchMock).toHaveBeenCalledWith(
      'https://api.example.test/content/llms-txt/drafts/llms_1',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({ content: '# Edited' }),
        headers: expect.objectContaining({ 'If-Match': '"rev-2"' }),
      }),
    )
  })

  it('publishes a draft with If-Match', async () => {
    const draft = {
      run_id: 'llms_1',
      status: 'published',
      revision: 2,
      etag: '"rev-2"',
      content: '# Published',
    }
    const fetchMock = vi.fn().mockReturnValue(jsonResponse(draft))
    vi.stubGlobal('fetch', fetchMock)

    await expect(service.publishDraft('llms_1', '"rev-2"')).resolves.toEqual(draft)
    expect(fetchMock).toHaveBeenCalledWith(
      'https://api.example.test/content/llms-txt/drafts/llms_1/publish',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ 'If-Match': '"rev-2"' }),
      }),
    )
  })

  it('surfaces API status and message for revision conflicts', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockReturnValue(jsonResponse({ error: 'Draft revision changed' }, 412)),
    )

    const error = await service
      .saveDraft('llms_1', '# Edited', '"stale"')
      .catch((caught) => caught)

    expect(error).toBeInstanceOf(LlmsTxtApiError)
    expect(error).toMatchObject({
      message: 'Draft revision changed',
      status: 412,
    })
  })

  it('fails before fetch when no Cognito ID token is available', async () => {
    vi.mocked(fetchAuthSession).mockResolvedValue({ tokens: undefined } as never)
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    await expect(service.getState()).rejects.toMatchObject({
      status: 401,
      message: 'Cognito ID token is unavailable',
    })
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
