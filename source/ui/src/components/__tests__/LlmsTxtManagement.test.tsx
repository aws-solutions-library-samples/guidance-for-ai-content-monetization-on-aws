import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import LlmsTxtManagement from '../LlmsTxtManagement'
import {
  LlmsTxtApiError,
  LlmsTxtClient,
  LlmsTxtDraft,
  LlmsTxtManagementState,
  LlmsTxtRun,
} from '../../services/llmsTxtService'

const published = {
  content: '# Published\n\n- Existing',
  run_id: 'llms_previous',
  revision: 4,
  published_at: '2026-08-27T10:00:00Z',
}

const reviewDraft: LlmsTxtDraft = {
  run_id: 'llms_1',
  status: 'in_review',
  source: 'manual',
  revision: 1,
  etag: '"rev-1"',
  content: '# Draft\n\n- New',
  diff: '--- published/llms.txt\n+++ draft/llms.txt\n- Existing\n+ New',
  updated_at: '2026-08-28T12:00:00Z',
}

function state(overrides: Partial<LlmsTxtManagementState> = {}): LlmsTxtManagementState {
  return {
    published,
    active_run: null,
    draft: reviewDraft,
    ...overrides,
  }
}

function run(status: LlmsTxtRun['status'], overrides: Partial<LlmsTxtRun> = {}): LlmsTxtRun {
  return {
    run_id: 'llms_1',
    status,
    created_at: '2026-08-28T12:00:00Z',
    updated_at: '2026-08-28T12:00:01Z',
    ...overrides,
  }
}

function mockService(initialState = state()) {
  return {
    getState: vi.fn().mockResolvedValue(initialState),
    startRun: vi.fn(),
    getRun: vi.fn(),
    saveDraft: vi.fn(),
    publishDraft: vi.fn(),
  } satisfies LlmsTxtClient
}

async function click(element: HTMLElement) {
  await act(async () => {
    fireEvent.click(element)
    await Promise.resolve()
  })
}

async function flushPromises() {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('LlmsTxtManagement', () => {
  it('renders the published manifest, review draft, and unified diff', async () => {
    const service = mockService()
    render(<LlmsTxtManagement service={service} />)

    expect(
      await screen.findByRole('textbox', { name: 'Published llms.txt manifest' }),
    ).toHaveValue('# Published\n\n- Existing')
    expect(screen.getByRole('textbox', { name: 'llms.txt draft' })).toHaveValue(
      '# Draft\n\n- New',
    )
    expect(screen.getByLabelText('Unified diff')).toHaveTextContent('- Existing')
    expect(screen.getByLabelText('Unified diff')).toHaveTextContent('+ New')
    expect(screen.getByRole('button', { name: 'Save draft' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Publish' })).toBeEnabled()
  })

  it('starts a run, polls through processing, and loads the generated draft', async () => {
    vi.useFakeTimers()
    const service = mockService(state({ draft: null }))
    service.startRun.mockResolvedValue({
      run: run('queued'),
      already_running: false,
    })
    service.getRun
      .mockResolvedValueOnce(run('processing'))
      .mockResolvedValueOnce(
        run('completed', { content_count: 3, batch_count: 1, draft: reviewDraft }),
      )
    service.getState
      .mockResolvedValueOnce(state({ draft: null }))
      .mockResolvedValueOnce(state())

    render(<LlmsTxtManagement service={service} pollIntervalMs={5} />)
    await flushPromises()
    expect(screen.getByText('No draft is ready for review.')).toBeInTheDocument()

    await click(screen.getByRole('button', { name: 'Generate draft' }))
    expect(service.startRun).toHaveBeenCalledOnce()
    expect(screen.getByText('Queued')).toBeInTheDocument()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(20)
    })
    expect(service.getRun).toHaveBeenCalledTimes(2)
    expect(screen.getByText('Completed')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'llms.txt draft' })).toHaveValue(
      '# Draft\n\n- New',
    )
  })

  it('saves an edited revision and explicitly publishes it after confirmation', async () => {
    const savedDraft = {
      ...reviewDraft,
      revision: 2,
      etag: '"rev-2"',
      content: '# Draft\n\n- Edited',
    }
    const publishedDraft: LlmsTxtDraft = {
      ...savedDraft,
      status: 'published',
      published_at: '2026-08-28T12:05:00Z',
    }
    const service = mockService()
    service.saveDraft.mockResolvedValue(savedDraft)
    service.publishDraft.mockResolvedValue(publishedDraft)

    render(<LlmsTxtManagement service={service} />)
    const editor = await screen.findByRole('textbox', { name: 'llms.txt draft' })
    fireEvent.change(editor, { target: { value: '# Draft\n\n- Edited' } })

    expect(screen.getByRole('button', { name: 'Publish' })).toBeDisabled()
    await click(screen.getByRole('button', { name: 'Save draft' }))

    expect(service.saveDraft).toHaveBeenCalledWith(
      'llms_1',
      '# Draft\n\n- Edited',
      '"rev-1"',
    )
    expect(await screen.findByText('Draft revision 2 saved.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Publish' })).toBeEnabled()

    await click(screen.getByRole('button', { name: 'Publish' }))
    expect(screen.getByText('Publish draft revision 2 as the active llms.txt manifest?')).toBeInTheDocument()
    await click(screen.getByRole('button', { name: 'Publish revision 2' }))

    expect(service.publishDraft).toHaveBeenCalledWith('llms_1', '"rev-2"')
    expect(await screen.findByText('Revision 2 published.')).toBeInTheDocument()
  })

  it('retains local edits and blocks writes after an If-Match conflict', async () => {
    const latestDraft = {
      ...reviewDraft,
      revision: 2,
      etag: '"rev-2"',
      content: '# Draft\n\n- Remote edit',
    }
    const service = mockService()
    service.saveDraft.mockRejectedValue(
      new LlmsTxtApiError('Draft revision changed', 412),
    )
    service.getState
      .mockResolvedValueOnce(state())
      .mockResolvedValueOnce(state({ draft: latestDraft }))

    render(<LlmsTxtManagement service={service} />)
    const editor = await screen.findByRole('textbox', { name: 'llms.txt draft' })
    fireEvent.change(editor, { target: { value: '# Draft\n\n- Local edit' } })
    await click(screen.getByRole('button', { name: 'Save draft' }))

    expect(await screen.findByText('Draft revision changed')).toBeInTheDocument()
    expect(editor).toHaveValue('# Draft\n\n- Local edit')
    expect(screen.getByRole('button', { name: 'Save draft' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Publish' })).toBeDisabled()

    await click(screen.getByRole('button', { name: 'Reload latest draft' }))
    await waitFor(() =>
      expect(screen.getByRole('textbox', { name: 'llms.txt draft' })).toHaveValue(
        '# Draft\n\n- Remote edit',
      ),
    )
    expect(screen.queryByText('Draft revision changed')).not.toBeInTheDocument()
  })

  it.each([
    {
      terminal: run('no_change', { content_count: 3 }),
      heading: 'No manifest changes',
      detail: 'Published content has no manifest-relevant updates.',
    },
    {
      terminal: run('failed', {
        error_code: 'model_output_invalid',
        error_message: 'Generated sections were invalid.',
      }),
      heading: 'model_output_invalid',
      detail: 'Generated sections were invalid.',
    },
  ])('renders $terminal.status terminal state', async ({ terminal, heading, detail }) => {
    vi.useFakeTimers()
    const service = mockService(state({ active_run: run('processing') }))
    service.getRun.mockResolvedValue(terminal)
    service.getState
      .mockResolvedValueOnce(state({ active_run: run('processing') }))
      .mockResolvedValueOnce(state({ active_run: null }))

    render(<LlmsTxtManagement service={service} pollIntervalMs={5} />)
    await flushPromises()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5)
    })
    expect(service.getRun).toHaveBeenCalledOnce()
    expect(screen.getByText(heading)).toBeInTheDocument()
    expect(screen.getByText(detail)).toBeInTheDocument()
  })

  it('shows load failures and retries the management request', async () => {
    const service = mockService()
    service.getState
      .mockRejectedValueOnce(new Error('Service unavailable'))
      .mockResolvedValueOnce(state())

    render(<LlmsTxtManagement service={service} />)
    expect(await screen.findByText('Service unavailable')).toBeInTheDocument()

    await click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() =>
      expect(screen.getByRole('textbox', { name: 'llms.txt draft' })).toHaveValue(
        '# Draft\n\n- New',
      ),
    )
    expect(service.getState).toHaveBeenCalledTimes(2)
  })

  it('does not poll after unmounting an active run', async () => {
    vi.useFakeTimers()
    const service = mockService(state({ active_run: run('processing') }))
    const view = render(
      <LlmsTxtManagement service={service} pollIntervalMs={1000} />,
    )
    await act(async () => {
      await Promise.resolve()
    })
    view.unmount()

    await act(async () => {
      vi.advanceTimersByTime(1000)
    })
    expect(service.getRun).not.toHaveBeenCalled()
  })
})
