/**
 * Comparison run client (spec `scrape-vs-api-comparison`, design §3a/§3b).
 *
 * `submitRun` / `pollRun` call the Cognito-authenticated `ui_backend` run
 * route; `toRunRecord` assembles the schema-2 run record from the two
 * responses plus the browser-side cleanup, and the strict parser validates it
 * before the view renders anything.
 */

import { fetchAuthSession, getCurrentUser } from 'aws-amplify/auth';

import type { CleanupResult, MarkdownMeasurement } from './comparisonMeasure';
import {
  parseRunRecord,
  RUN_SCHEMA_VERSION,
  type ApiStepId,
  type ApiStepStatus,
  type RecordFigure,
  type RunRecord,
  type ScrapeStepId,
  type ScrapeStepStatus,
  type StepRecord,
} from './comparisonRunService';

export type IngestMode = 'submitted' | 'reused';
export type AllowListMode = 'restricted' | 'open';
/** R15.4: `owned` = the page is on one of the deployment's publisher hosts. */
export type RunOrigin = 'owned' | 'external';

export interface SubmitResponse {
  readonly run_id: string;
  readonly submitted_at: string;
  readonly ingest: IngestMode;
  readonly origin: RunOrigin;
  readonly allow_list_mode: AllowListMode;
  readonly article: {
    readonly website_url: string;
    readonly final_url: string;
    readonly canonical_url: string;
    readonly canonical_injected: boolean;
    readonly canonical_overridden?: boolean;
    readonly markdown_key: string;
    readonly licence_advertised?: string | null;
  };
  readonly scrape: {
    readonly status: number;
    readonly document_bytes: number;
    readonly total_bytes: number;
    readonly requests: number;
    readonly external_refs: number;
    readonly document_ms: number;
    readonly total_ms: number;
    readonly subresources_probed: number;
    readonly subresources_skipped: number;
  };
  readonly html: string;
}

export interface PollResponse {
  readonly run_id: string;
  readonly status: 'pending' | 'ready';
  readonly elapsed_ms: number;
  readonly api?: {
    readonly content_id: string;
    readonly resolve_ms: number;
    readonly fetch_ms: number;
    readonly bytes: number;
    readonly markdown: string;
    readonly etag?: string | null;
    readonly updated_at: string;
    readonly provenance?: {
      readonly title?: string | null;
      readonly author?: string | null;
      readonly pubdate?: string | null;
      readonly canonical_url?: string | null;
    };
  };
}

/** Stable submit-refusal codes (R7.2, R1.7). */
export type SubmitErrorCode =
  | 'invalid_url'
  | 'url_not_allowed'
  | 'url_unresolvable'
  | 'fetch_failed'
  | 'fetch_too_large'
  | 'fetch_timeout'
  | 'fetch_not_html'
  | 'canonical_ineligible';

export class RunRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly errorCode: string | null,
    readonly details: unknown = null
  ) {
    super(message);
    this.name = 'RunRequestError';
  }
}

/** The gateway answered 401: the portal's session is gone. */
export class SessionExpiredError extends Error {
  constructor() {
    super('Your session has expired. Sign in again to continue.');
    this.name = 'SessionExpiredError';
  }
}

/** Hint shown next to a refusal: the named cause and the fix (design §5). */
export const SUBMIT_ERROR_HINTS: Readonly<Record<SubmitErrorCode, string>> = {
  invalid_url: 'Enter a full https:// URL with a DNS host name: no credentials, no port, no IP address.',
  url_not_allowed:
    'This host is not on the deployment allow-list. Ask the deployer to add it to COMPARISON_ALLOWED_HOSTS, or use the fixture article.',
  url_unresolvable: 'The host name did not resolve to a public address. Check the spelling, or use a public host.',
  fetch_failed: 'The site answered with an error or could not be reached. Open the URL in a browser to check it is up.',
  fetch_too_large: 'The document is larger than the 5 MB limit. Pick an article page rather than a listing or a download.',
  fetch_timeout: 'The site did not answer within 10 seconds. Try again, or pick a faster page.',
  fetch_not_html: 'The URL did not return HTML. Point at an article page.',
  canonical_ineligible:
    'The page canonical URL cannot be mapped to a content address. Give the page a canonical link in its head.',
};

/**
 * What this deployment may fetch, in plain words (R7.3, R15.7 amended 2026-11-12). The mode's own
 * name stays in the page's "Run details".
 */
export function allowListSentence(mode: AllowListMode): string {
  return mode === 'restricted'
    ? 'This deployment fetches only the hosts on its allow-list.'
    : 'This deployment may fetch any public https page.';
}

/**
 * Where this run's licensed Markdown comes from and who sees it, in the operator's words (R15.7,
 * amended 2026-11-12). A run never creates a catalogue record: only the customer's own,
 * already-published article is reused.
 */
export function originLabel(submit: Pick<SubmitResponse, 'origin' | 'ingest'>): string {
  if (submit.origin === 'owned') {
    return submit.ingest === 'reused'
      ? 'This is your content: the comparison uses your published Markdown, the one partners and search use.'
      : "This is your content: this run's Markdown is kept for this comparison only, outside your catalogue, so partners and search never see it.";
  }
  return 'This page is external: its Markdown is kept for this comparison only, outside your catalogue, so partners and search never see it.';
}

/**
 * Where this run's licensed Markdown landed (R15.7), for the page's "Run details": the object key
 * and what it is. A submitted run writes to the comparison partition (`comparison/…`, R15.1), which
 * nothing partner-facing reads; a reused run points at the published catalogue object.
 */
export function markdownLocation(submit: Pick<SubmitResponse, 'ingest' | 'article'>): {
  readonly key: string;
  readonly note: string;
} {
  return {
    key: submit.article.markdown_key,
    note: submit.ingest === 'reused' ? 'your published catalogue object' : 'comparison partition, outside your catalogue',
  };
}

/**
 * R1.7 (amended 2026-11-10): re-ingesting only changes anything when a published record exists for
 * the URL, which the page learns from a run that reused it (owned + reused). A fresh run of such a
 * URL keeps the offer; any other outcome withdraws it.
 */
export function publishedRecordUrl(
  submittedUrl: string,
  submit: Pick<SubmitResponse, 'origin' | 'ingest'>,
  askedForFreshRun: boolean
): string | null {
  if (submit.origin !== 'owned') return null;
  return submit.ingest === 'reused' || askedForFreshRun ? submittedUrl : null;
}

function apiBaseUrl(): string {
  return import.meta.env.VITE_API_BASE_URL || 'https://api.example.com';
}

async function authHeaders(): Promise<Record<string, string>> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  try {
    const user = await getCurrentUser();
    if (user) {
      const session = await fetchAuthSession();
      const token = session.tokens?.idToken?.toString();
      if (token) headers.Authorization = `Bearer ${token}`;
    }
  } catch {
    // No session: the gateway answers 401 and the page shows the expired-session state.
  }
  return headers;
}

async function readError(response: Response): Promise<RunRequestError> {
  let body: Record<string, unknown> = {};
  try {
    body = (await response.json()) as Record<string, unknown>;
  } catch {
    // non-JSON error body
  }
  const code = typeof body.error_code === 'string' ? body.error_code : null;
  const message =
    (typeof body.error === 'string' && body.error) ||
    (typeof body.message === 'string' && body.message) ||
    response.statusText ||
    `Request failed with status ${response.status}`;
  return new RunRequestError(message, response.status, code, body.details ?? null);
}

export async function submitRun(url: string, reingest = false): Promise<SubmitResponse> {
  const response = await fetch(`${apiBaseUrl()}/test/comparison-runs`, {
    method: 'POST',
    headers: await authHeaders(),
    body: JSON.stringify({ url, reingest }),
  });
  if (response.status === 401) throw new SessionExpiredError();
  if (!response.ok) throw await readError(response);
  return (await response.json()) as SubmitResponse;
}

export async function pollRun(runId: string): Promise<PollResponse> {
  const response = await fetch(`${apiBaseUrl()}/test/comparison-runs/${encodeURIComponent(runId)}`, {
    method: 'GET',
    headers: await authHeaders(),
  });
  if (response.status === 401) throw new SessionExpiredError();
  if (!response.ok) throw await readError(response);
  return (await response.json()) as PollResponse;
}

export const DEFAULT_POLL_LIMIT_MS = 180_000;

export function pollLimitMs(): number {
  const raw = Number(import.meta.env.VITE_COMPARISON_POLL_LIMIT_MS);
  return Number.isFinite(raw) && raw > 0 ? raw : DEFAULT_POLL_LIMIT_MS;
}

export interface PollUntilOptions {
  readonly limitMs?: number;
  readonly initialDelayMs?: number;
  readonly maxDelayMs?: number;
  readonly onPending?: (poll: PollResponse) => void;
  readonly sleep?: (ms: number) => Promise<void>;
  readonly now?: () => number;
  readonly signal?: AbortSignal;
}

export type PollOutcome =
  | { readonly kind: 'ready'; readonly poll: PollResponse }
  | { readonly kind: 'timeout'; readonly poll: PollResponse | null; readonly limitMs: number }
  | { readonly kind: 'aborted' };

/**
 * Poll one run with backoff until it is ready or the limit passes. The limit
 * is measured from this call, so "check again" gets a fresh window on the
 * same `run_id`. The route itself never times a run out.
 */
export async function pollUntilReady(runId: string, options: PollUntilOptions = {}): Promise<PollOutcome> {
  const limitMs = options.limitMs ?? pollLimitMs();
  const sleep = options.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const now = options.now ?? (() => Date.now());
  const maxDelay = options.maxDelayMs ?? 8000;
  let delay = options.initialDelayMs ?? 1000;
  const started = now();
  let last: PollResponse | null = null;

  for (;;) {
    if (options.signal?.aborted) return { kind: 'aborted' };
    last = await pollRun(runId);
    if (last.status === 'ready' && last.api) return { kind: 'ready', poll: last };
    options.onPending?.(last);
    if (now() - started + delay >= limitMs) return { kind: 'timeout', poll: last, limitMs };
    await sleep(delay);
    delay = Math.min(maxDelay, Math.round(delay * 1.5));
  }
}

const measuredFig = (value: number): RecordFigure => ({ value, provenance: 'measured' });
const notMeasuredFig: RecordFigure = { value: null, provenance: 'not_measured' };

// The run reads the Markdown object directly (R11.1). The conditional re-fetch needs the content API
// read, which the run does not use yet (R11.3).
export const CONDITIONAL_REFETCH_REASON = 'Read from the stored Markdown object, not through the content API, so there is no conditional request to time';

export interface ToRunRecordOptions {
  /** Markdown measured in the browser; required when the poll is ready. */
  readonly markdown?: MarkdownMeasurement;
  /** The poll window passed without the pipeline publishing. */
  readonly timedOutAfterMs?: number;
}

function scrapeStepsFrom(submit: SubmitResponse, cleanup: CleanupResult) {
  const s = submit.scrape;
  const okStatus = s.status >= 200 && s.status < 300;
  const steps: StepRecord<ScrapeStepId, ScrapeStepStatus>[] = [
    {
      id: 'fetch_document',
      status: okStatus ? 'ok' : 'failed',
      reason: okStatus ? null : `HTTP ${s.status}`,
      wall_clock_ms: measuredFig(s.document_ms),
      bytes: measuredFig(s.document_bytes),
      tokens: measuredFig(cleanup.documentTokens),
    },
    {
      id: 'fetch_subresources',
      status: s.subresources_probed > 0 ? 'ok' : 'skipped',
      reason:
        `${s.subresources_probed} same-origin probed (HEAD), ${s.subresources_skipped} skipped, ` +
        `${s.external_refs} cross-origin counted and not fetched`,
      wall_clock_ms: measuredFig(Math.max(0, s.total_ms - s.document_ms)),
      bytes: measuredFig(Math.max(0, s.total_bytes - s.document_bytes)),
    },
  ];
  for (const c of cleanup.steps) {
    steps.push({
      id: c.id,
      status: c.status,
      reason: c.reason,
      wall_clock_ms: measuredFig(c.wall_clock_ms),
      bytes: measuredFig(c.bytes),
      tokens: measuredFig(c.tokens),
    });
  }
  return steps;
}

function apiStepsFrom(
  submit: SubmitResponse,
  poll: PollResponse,
  options: ToRunRecordOptions
): { steps: StepRecord<ApiStepId, ApiStepStatus>[]; outcome: 'obtained' | 'pending' | 'precondition_failed' } {
  const refetch: StepRecord<ApiStepId, ApiStepStatus> = {
    id: 'conditional_refetch',
    status: 'not_measured',
    reason: CONDITIONAL_REFETCH_REASON,
    wall_clock_ms: notMeasuredFig,
  };
  const reused = submit.ingest === 'reused';
  const ingestOk: StepRecord<ApiStepId, ApiStepStatus> = reused
    ? {
        id: 'ingest',
        status: 'skipped',
        reason: 'Your content, reused from the catalogue; this run did not ingest.',
      }
    : {
        id: 'ingest',
        status: 'ok',
        reason: 'One-time preparation cost; excluded from the latency headline.',
        wall_clock_ms: measuredFig(poll.elapsed_ms),
      };

  if (poll.status === 'ready' && poll.api) {
    const md = options.markdown;
    if (!md) throw new Error('toRunRecord: markdown measurement is required for a ready poll');
    return {
      outcome: 'obtained',
      steps: [
        ingestOk,
        { id: 'resolve', status: 'ok', reason: null, wall_clock_ms: measuredFig(poll.api.resolve_ms) },
        {
          id: 'fetch_markdown',
          status: 'ok',
          reason: null,
          wall_clock_ms: measuredFig(poll.api.fetch_ms),
          bytes: measuredFig(poll.api.bytes),
        },
        refetch,
        {
          id: 'tokenize',
          status: 'ok',
          reason: null,
          wall_clock_ms: measuredFig(md.tokenizeMs),
          bytes: measuredFig(md.bytes),
          tokens: measuredFig(md.tokens),
        },
      ],
    };
  }

  const timedOut = options.timedOutAfterMs !== undefined;
  const waiting = (id: ApiStepId): StepRecord<ApiStepId, ApiStepStatus> => ({
    id,
    status: timedOut ? 'skipped' : 'pending',
    reason: timedOut ? 'The pipeline is still publishing the page.' : null,
  });
  return {
    outcome: timedOut ? 'precondition_failed' : 'pending',
    steps: [
      timedOut
        ? {
            id: 'ingest',
            status: 'failed',
            reason: `Still processing: the pipeline had not published after ${options.timedOutAfterMs} ms.`,
            wall_clock_ms: measuredFig(poll.elapsed_ms),
          }
        : {
            id: 'ingest',
            status: 'pending',
            reason: 'Waiting for the ingest pipeline to publish the page.',
            wall_clock_ms: measuredFig(poll.elapsed_ms),
          },
      waiting('resolve'),
      waiting('fetch_markdown'),
      refetch,
      waiting('tokenize'),
    ],
  };
}

/**
 * Build the schema-2 run record from the submit and poll responses plus the
 * browser-side cleanup. The strict parser validates it before it is returned.
 */
export function toRunRecord(
  submit: SubmitResponse,
  poll: PollResponse,
  cleanup: CleanupResult,
  tokenizerName: string,
  options: ToRunRecordOptions = {}
): RunRecord {
  const s = submit.scrape;
  const scrapeOutcome =
    s.status >= 200 && s.status < 300 ? 'obtained' : s.status === 401 || s.status === 403 || s.status === 429 ? 'refused' : 'failed';
  const api = apiStepsFrom(submit, poll, options);
  const ready = poll.status === 'ready' && poll.api !== undefined;
  const prov = poll.api?.provenance;

  return parseRunRecord({
    schema_version: RUN_SCHEMA_VERSION,
    run_id: submit.run_id,
    started_at: submit.submitted_at,
    producer: 'route+browser',
    tokenizer: tokenizerName,
    article: {
      website_url: submit.article.website_url,
      final_url: submit.article.final_url,
      canonical_url: submit.article.canonical_url,
      markdown_key: submit.article.markdown_key ?? null,
    },
    paths: {
      scrape: {
        outcome: scrapeOutcome,
        attempts: [
          {
            n: 1,
            status: s.status,
            bytes: s.total_bytes,
            wall_clock_ms: s.total_ms,
            challenge: null,
            document_bytes: s.document_bytes,
            requests: s.requests,
            external_refs: s.external_refs,
            document_ms: s.document_ms,
          },
        ],
        steps: scrapeStepsFrom(submit, cleanup),
        content_tokens: cleanup.documentTokens,
        visible_text_tokens: cleanup.visibleTextTokens,
        visible_words: cleanup.visibleWords,
        article_words: cleanup.articleWords,
        licence_advertised: submit.article.licence_advertised ?? null,
      },
      api: {
        outcome: api.outcome,
        auth: 'cognito_user',
        attempts: ready
          ? [
              {
                n: 1,
                status: 200,
                bytes: poll.api?.bytes ?? 0,
                wall_clock_ms: (poll.api?.resolve_ms ?? 0) + (poll.api?.fetch_ms ?? 0),
                challenge: null,
              },
            ]
          : [],
        steps: api.steps,
        content_tokens: ready ? (options.markdown?.tokens ?? null) : null,
        words: ready ? (options.markdown?.words ?? null) : null,
        conditional_refetch: null,
        provenance: ready
          ? {
              title: prov?.title ?? '',
              author: prov?.author ?? '',
              pubdate: prov?.pubdate ?? '',
              canonical_url: prov?.canonical_url ?? '',
            }
          : null,
      },
    },
    measured: { success_rate: null, bytes_per_attempt: s.total_bytes, challenge_rate: null },
  });
}
