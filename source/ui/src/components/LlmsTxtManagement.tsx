import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  ColumnLayout,
  Container,
  Header,
  Modal,
  SpaceBetween,
  Spinner,
  StatusIndicator,
  Textarea,
} from '@cloudscape-design/components'

import {
  LlmsTxtApiError,
  LlmsTxtClient,
  LlmsTxtManagementState,
  LlmsTxtRun,
  LlmsTxtRunStatus,
  llmsTxtService,
} from '../services/llmsTxtService'

const ACTIVE_RUN_STATUSES: ReadonlySet<LlmsTxtRunStatus> = new Set([
  'queued',
  'processing',
])

interface LlmsTxtManagementProps {
  service?: LlmsTxtClient
  pollIntervalMs?: number
}

type Action = 'run' | 'save' | 'publish' | null

function statusType(status: LlmsTxtRunStatus) {
  switch (status) {
    case 'queued':
      return 'pending' as const
    case 'processing':
      return 'in-progress' as const
    case 'completed':
      return 'success' as const
    case 'no_change':
      return 'info' as const
    case 'failed':
      return 'error' as const
  }
}

function statusLabel(status: LlmsTxtRunStatus) {
  return status === 'no_change'
    ? 'No change'
    : status.charAt(0).toUpperCase() + status.slice(1)
}

function formatDate(value?: string | null) {
  return value ? new Date(value).toLocaleString() : 'Not published'
}

function Diff({ value }: { value?: string }) {
  if (!value) {
    return <Box color="text-body-secondary">No differences from the published manifest.</Box>
  }

  return (
    <Box>
      <pre
        aria-label="Unified diff"
        style={{
          background: '#f2f3f3',
          border: '1px solid #d5dbdb',
          borderRadius: 4,
          fontFamily: 'monospace',
          fontSize: 12,
          lineHeight: 1.5,
          margin: 0,
          maxHeight: 320,
          overflow: 'auto',
          padding: 12,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {value.split('\n').map((line, index) => (
          <span
            key={`${index}-${line}`}
            style={{
              background:
                line.startsWith('+') && !line.startsWith('+++')
                  ? '#e6f4ea'
                  : line.startsWith('-') && !line.startsWith('---')
                    ? '#fce8e6'
                    : undefined,
              color:
                line.startsWith('+') && !line.startsWith('+++')
                  ? '#1e4620'
                  : line.startsWith('-') && !line.startsWith('---')
                    ? '#7d2105'
                    : undefined,
              display: 'block',
            }}
          >
            {line || ' '}
          </span>
        ))}
      </pre>
    </Box>
  )
}

export default function LlmsTxtManagement({
  service = llmsTxtService,
  pollIntervalMs = 2000,
}: LlmsTxtManagementProps) {
  const [management, setManagement] = useState<LlmsTxtManagementState | null>(null)
  const [currentRun, setCurrentRun] = useState<LlmsTxtRun | null>(null)
  const [editorContent, setEditorContent] = useState('')
  const [loading, setLoading] = useState(true)
  const [action, setAction] = useState<Action>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [conflict, setConflict] = useState(false)
  const [publishConfirmVisible, setPublishConfirmVisible] = useState(false)

  const draft = management?.draft ?? null
  const isRunActive = currentRun ? ACTIVE_RUN_STATUSES.has(currentRun.status) : false
  const isDirty = draft ? editorContent !== draft.content : false

  const applyState = useCallback((state: LlmsTxtManagementState) => {
    setManagement(state)
    setCurrentRun(state.active_run)
    setEditorContent(state.draft?.content ?? '')
    setConflict(false)
  }, [])

  const loadState = useCallback(async () => {
    setError(null)
    const state = await service.getState()
    applyState(state)
    return state
  }, [applyState, service])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    loadState()
      .catch((loadError) => {
        if (!cancelled) {
          setError(
            loadError instanceof Error
              ? loadError.message
              : 'Failed to load llms.txt management state',
          )
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [loadState])

  useEffect(() => {
    if (!currentRun || !ACTIVE_RUN_STATUSES.has(currentRun.status)) {
      return
    }

    let cancelled = false
    let timer: number | undefined

    const poll = async () => {
      try {
        const run = await service.getRun(currentRun.run_id)
        if (cancelled) {
          return
        }
        if (ACTIVE_RUN_STATUSES.has(run.status)) {
          setCurrentRun(run)
          timer = window.setTimeout(poll, pollIntervalMs)
        } else {
          const state = await service.getState()
          if (cancelled) {
            return
          }
          applyState(state)
          setCurrentRun(run)
        }
      } catch (pollError) {
        if (!cancelled) {
          setError(
            pollError instanceof Error
              ? pollError.message
              : 'Failed to refresh the llms.txt generation run',
          )
        }
      }
    }

    timer = window.setTimeout(poll, pollIntervalMs)
    return () => {
      cancelled = true
      if (timer !== undefined) {
        window.clearTimeout(timer)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentionally keyed on currentRun.run_id/.status, not the whole object, to avoid re-firing the poller on unrelated field changes
  }, [applyState, currentRun?.run_id, currentRun?.status, pollIntervalMs, service])

  const runMetadata = useMemo(
    () => [
      { label: 'Content items', value: currentRun?.content_count ?? '-' },
      { label: 'Batches', value: currentRun?.batch_count ?? '-' },
      { label: 'Updated', value: formatDate(currentRun?.updated_at) },
    ],
    [currentRun],
  )

  const handleStartRun = async () => {
    setAction('run')
    setError(null)
    setNotice(null)
    try {
      const response = await service.startRun()
      setCurrentRun(response.run)
      setNotice(
        response.already_running
          ? 'The active generation run is now being monitored.'
          : 'Generation run queued.',
      )
    } catch (runError) {
      setError(
        runError instanceof Error ? runError.message : 'Failed to start llms.txt generation',
      )
    } finally {
      setAction(null)
    }
  }

  const handleSave = async () => {
    if (!draft) {
      return
    }
    setAction('save')
    setError(null)
    setNotice(null)
    try {
      const savedDraft = await service.saveDraft(draft.run_id, editorContent, draft.etag)
      setManagement((current) => (current ? { ...current, draft: savedDraft } : current))
      setEditorContent(savedDraft.content)
      setConflict(false)
      setNotice(`Draft revision ${savedDraft.revision} saved.`)
    } catch (saveError) {
      if (saveError instanceof LlmsTxtApiError && saveError.status === 412) {
        setConflict(true)
      } else {
        setError(saveError instanceof Error ? saveError.message : 'Failed to save the draft')
      }
    } finally {
      setAction(null)
    }
  }

  const handleReloadConflict = async () => {
    setLoading(true)
    setNotice(null)
    try {
      await loadState()
      setNotice('Latest draft revision loaded.')
    } catch (loadError) {
      setError(
        loadError instanceof Error ? loadError.message : 'Failed to load the latest draft',
      )
    } finally {
      setLoading(false)
    }
  }

  const handlePublish = async () => {
    if (!draft) {
      return
    }
    setPublishConfirmVisible(false)
    setAction('publish')
    setError(null)
    setNotice(null)
    try {
      const publishedDraft = await service.publishDraft(draft.run_id, draft.etag)
      setManagement((current) =>
        current
          ? {
              ...current,
              draft: publishedDraft,
              published: {
                content: publishedDraft.content,
                run_id: publishedDraft.run_id,
                revision: publishedDraft.revision,
                snapshot_fingerprint: publishedDraft.snapshot_fingerprint,
                published_at: publishedDraft.published_at,
              },
            }
          : current,
      )
      setEditorContent(publishedDraft.content)
      setNotice(`Revision ${publishedDraft.revision} published.`)
    } catch (publishError) {
      if (publishError instanceof LlmsTxtApiError && publishError.status === 412) {
        setConflict(true)
      } else {
        setError(
          publishError instanceof Error ? publishError.message : 'Failed to publish the draft',
        )
      }
    } finally {
      setAction(null)
    }
  }

  if (loading && !management) {
    return (
      <Box textAlign="center" padding="xxl">
        <Spinner size="large" />
      </Box>
    )
  }

  return (
    <>
      <SpaceBetween size="l">
        {error && (
          <Alert
            type="error"
            header="llms.txt request failed"
            action={<Button onClick={handleReloadConflict}>Retry</Button>}
          >
            {error}
          </Alert>
        )}
        {notice && (
          <Alert type="success" dismissible onDismiss={() => setNotice(null)}>
            {notice}
          </Alert>
        )}
        {conflict && (
          <Alert
            type="warning"
            header="Draft revision changed"
            action={<Button onClick={handleReloadConflict}>Reload latest draft</Button>}
          >
            Your edits remain in the editor. Reload the latest revision before saving or
            publishing.
          </Alert>
        )}

        <Container
          header={
            <Header
              variant="h2"
              actions={
                <Button
                  variant="primary"
                  iconName="refresh"
                  onClick={handleStartRun}
                  loading={action === 'run'}
                  disabled={isRunActive}
                >
                  Generate draft
                </Button>
              }
            >
              Generation
            </Header>
          }
        >
          {currentRun ? (
            <SpaceBetween size="m">
              <StatusIndicator type={statusType(currentRun.status)}>
                {statusLabel(currentRun.status)}
              </StatusIndicator>
              <ColumnLayout columns={3} variant="text-grid">
                {runMetadata.map((item) => (
                  <div key={item.label}>
                    <Box variant="awsui-key-label">{item.label}</Box>
                    <Box>{item.value}</Box>
                  </div>
                ))}
              </ColumnLayout>
              {currentRun.status === 'no_change' && (
                <Alert type="info" header="No manifest changes">
                  Published content has no manifest-relevant updates.
                </Alert>
              )}
              {currentRun.status === 'failed' && (
                <Alert type="error" header={currentRun.error_code || 'Generation failed'}>
                  {currentRun.error_message || 'The generation worker did not complete.'}
                </Alert>
              )}
            </SpaceBetween>
          ) : (
            <Box color="text-body-secondary">No active generation run.</Box>
          )}
        </Container>

        <Container
          header={
            <Header
              variant="h2"
              actions={
                <SpaceBetween direction="horizontal" size="xs">
                  <Button
                    onClick={handleSave}
                    loading={action === 'save'}
                    disabled={
                      !draft ||
                      draft.status !== 'in_review' ||
                      !isDirty ||
                      !editorContent.trim() ||
                      conflict
                    }
                  >
                    Save draft
                  </Button>
                  <Button
                    variant="primary"
                    onClick={() => setPublishConfirmVisible(true)}
                    loading={action === 'publish'}
                    disabled={
                      !draft ||
                      draft.status !== 'in_review' ||
                      isDirty ||
                      conflict
                    }
                  >
                    Publish
                  </Button>
                </SpaceBetween>
              }
              description={
                draft
                  ? `Revision ${draft.revision} · ${draft.status.replace('_', ' ')}`
                  : undefined
              }
            >
              Draft review
            </Header>
          }
        >
          {draft ? (
            <SpaceBetween size="l">
              <div>
                <Box variant="awsui-key-label" margin={{ bottom: 'xs' }}>
                  Unified diff
                </Box>
                <Diff value={draft.diff} />
              </div>
              <div>
                <Box variant="awsui-key-label" margin={{ bottom: 'xs' }}>
                  Draft
                </Box>
                <Textarea
                  ariaLabel="llms.txt draft"
                  value={editorContent}
                  onChange={({ detail }) => setEditorContent(detail.value)}
                  rows={20}
                  readOnly={draft.status !== 'in_review'}
                  disabled={action === 'publish'}
                />
              </div>
            </SpaceBetween>
          ) : (
            <Box color="text-body-secondary">No draft is ready for review.</Box>
          )}
        </Container>

        <Container
          header={
            <Header
              variant="h2"
              description={`Published ${formatDate(management?.published.published_at)}`}
            >
              Published manifest
            </Header>
          }
        >
          <Textarea
            ariaLabel="Published llms.txt manifest"
            value={management?.published.content ?? ''}
            rows={12}
            readOnly
          />
        </Container>
      </SpaceBetween>

      <Modal
        visible={publishConfirmVisible}
        onDismiss={() => setPublishConfirmVisible(false)}
        header="Publish llms.txt"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button onClick={() => setPublishConfirmVisible(false)}>Cancel</Button>
              <Button variant="primary" onClick={handlePublish}>
                Publish revision {draft?.revision}
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        Publish draft revision {draft?.revision} as the active llms.txt manifest?
      </Modal>
    </>
  )
}
