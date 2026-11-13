/**
 * Scrape vs licensed Markdown — run record, schema 2 (spec `scrape-vs-api-comparison`, design §2).
 *
 * A run record is the document one comparison run produces. In V1 the page
 * assembles it from the two route responses plus the browser-side cleanup
 * steps (`comparisonRunClient.toRunRecord`); the V2 harness will emit the same
 * record. The cost model and the view never know which producer made it.
 *
 * The parser is deliberately strict:
 *  - schema 2 only; a schema-1 record is rejected with its version named;
 *  - `steps[]` is mandatory on both paths and every figure carries a provenance;
 *  - a record that claims a satisfied challenge, carries a credential-like key
 *    or a Bearer-like value is rejected rather than displayed (R2.6).
 */

export const RUN_SCHEMA_VERSION = 2;

/** Where a figure comes from. A figure without one fails the parser (R3.1). */
export type FigureProvenance = 'measured' | 'sourced' | 'assumption' | 'modelled' | 'not_measured';

export const FIGURE_PROVENANCES: readonly FigureProvenance[] = [
  'measured',
  'sourced',
  'assumption',
  'modelled',
  'not_measured',
];

export interface RecordFigure {
  /** `null` only when provenance is `not_measured`. */
  readonly value: number | null;
  readonly provenance: FigureProvenance;
}

/** A challenge is only ever observed. There is no "solved" state (R2.6). */
export type ChallengeObservation =
  | 'bot_management_observed_not_satisfied'
  | 'payment_observed_not_satisfied'
  | null;

export type Producer = 'route+browser' | 'harness';
export type ScrapeOutcome = 'obtained' | 'refused' | 'failed';
export type ApiOutcome = 'obtained' | 'pending' | 'precondition_failed';
export type ScrapeStepId =
  | 'fetch_document'
  | 'fetch_subresources'
  | 'strip_scripts_styles'
  | 'extract_article'
  | 'remove_boilerplate'
  | 'normalize'
  | 'tokenize';
export type ApiStepId = 'ingest' | 'resolve' | 'fetch_markdown' | 'conditional_refetch' | 'tokenize';
export type ScrapeStepStatus = 'ok' | 'failed' | 'skipped';
export type ApiStepStatus = 'ok' | 'failed' | 'pending' | 'skipped' | 'not_measured';

export const SCRAPE_STEP_IDS: readonly ScrapeStepId[] = [
  'fetch_document',
  'fetch_subresources',
  'strip_scripts_styles',
  'extract_article',
  'remove_boilerplate',
  'normalize',
  'tokenize',
];
export const API_STEP_IDS: readonly ApiStepId[] = [
  'ingest',
  'resolve',
  'fetch_markdown',
  'conditional_refetch',
  'tokenize',
];

export interface StepRecord<Id extends string = string, Status extends string = string> {
  readonly id: Id;
  readonly status: Status;
  readonly reason: string | null;
  readonly wall_clock_ms?: RecordFigure;
  readonly bytes?: RecordFigure;
  readonly tokens?: RecordFigure;
  readonly cost_usd?: RecordFigure;
}

export interface Attempt {
  readonly n: number;
  /** HTTP status, or null when the transport failed before a response. */
  readonly status: number | null;
  readonly bytes: number;
  readonly wall_clock_ms: number;
  readonly challenge: ChallengeObservation;
  /** Path S only. */
  readonly document_bytes?: number;
  readonly requests?: number;
  readonly external_refs?: number;
  readonly document_ms?: number;
}

export interface ScrapePathRecord {
  readonly outcome: ScrapeOutcome;
  readonly attempts: readonly Attempt[];
  readonly steps: readonly StepRecord<ScrapeStepId, ScrapeStepStatus>[];
  /** Tokens of the raw HTML document as handed to a model (R3.4). */
  readonly content_tokens: number | null;
  readonly visible_text_tokens: number | null;
  readonly visible_words: number | null;
  readonly article_words: number | null;
  readonly licence_advertised: string | null;
}

export interface ApiProvenance {
  readonly title: string;
  readonly author: string;
  readonly pubdate: string;
  readonly canonical_url: string;
}

export interface ApiPathRecord {
  readonly outcome: ApiOutcome;
  readonly auth: 'cognito_user';
  readonly attempts: readonly Attempt[];
  readonly steps: readonly StepRecord<ApiStepId, ApiStepStatus>[];
  readonly content_tokens: number | null;
  readonly words: number | null;
  readonly conditional_refetch: { readonly status: number; readonly bytes: number } | null;
  readonly provenance: ApiProvenance | null;
}

export interface MeasuredInputs {
  readonly success_rate: number | null;
  readonly bytes_per_attempt: number | null;
  readonly challenge_rate: number | null;
}

export interface RunRecord {
  readonly schema_version: typeof RUN_SCHEMA_VERSION;
  readonly run_id: string;
  readonly started_at: string;
  readonly producer: Producer;
  readonly tokenizer: string;
  readonly article: {
    readonly website_url: string;
    readonly final_url: string;
    readonly canonical_url: string;
    readonly markdown_key: string | null;
  };
  readonly paths: { readonly scrape: ScrapePathRecord; readonly api: ApiPathRecord };
  readonly measured: MeasuredInputs;
}

export class RunRecordError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'RunRecordError';
  }
}

const SCRAPE_OUTCOMES: readonly ScrapeOutcome[] = ['obtained', 'refused', 'failed'];
const API_OUTCOMES: readonly ApiOutcome[] = ['obtained', 'pending', 'precondition_failed'];
const SCRAPE_STATUSES: readonly ScrapeStepStatus[] = ['ok', 'failed', 'skipped'];
const API_STATUSES: readonly ApiStepStatus[] = ['ok', 'failed', 'pending', 'skipped', 'not_measured'];
const PRODUCERS: readonly Producer[] = ['route+browser', 'harness'];
const CHALLENGES: readonly ChallengeObservation[] = [
  'bot_management_observed_not_satisfied',
  'payment_observed_not_satisfied',
  null,
];

/** Keys that must never appear anywhere in a record (R2.6). */
const FORBIDDEN_KEY =
  /^(cookies?|set-cookie|authorization|x-api-key|x-partner-key|api[-_]?key|licen[cs]e[-_]?key|token|access[-_]?token|password|secret)$/i;
/** A Bearer credential pasted into a value. */
const BEARER_VALUE = /\bbearer\s+[A-Za-z0-9._~+/-]{8,}=*/i;

function fail(message: string): never {
  throw new RunRecordError(`Invalid run record: ${message}`);
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function assertNoCredentials(value: unknown, path: string): void {
  if (Array.isArray(value)) {
    value.forEach((item, i) => assertNoCredentials(item, `${path}[${i}]`));
    return;
  }
  if (typeof value === 'string') {
    if (BEARER_VALUE.test(value)) fail(`${path} looks like a Bearer credential, which a record must not contain`);
    return;
  }
  if (!isObject(value)) return;
  for (const [key, child] of Object.entries(value)) {
    if (FORBIDDEN_KEY.test(key)) {
      fail(`${path}.${key} looks like a credential or session value, which a record must not contain`);
    }
    assertNoCredentials(child, `${path}.${key}`);
  }
}

function nonNegative(value: unknown, path: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
    fail(`${path} must be a non-negative number`);
  }
  return value;
}

function nonNegativeOrNull(value: unknown, path: string): number | null {
  if (value === null || value === undefined) return null;
  return nonNegative(value, path);
}

function rateOrNull(value: unknown, path: string): number | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > 1) {
    fail(`${path} must be null or a rate in [0, 1]`);
  }
  return value;
}

function nonEmptyString(value: unknown, path: string): string {
  if (typeof value !== 'string' || value.trim() === '') {
    fail(`${path} must be a non-empty string`);
  }
  return value;
}

function oneOf<T>(value: unknown, allowed: readonly T[], path: string): T {
  if (!allowed.includes(value as T)) fail(`${path} must be one of ${allowed.map(String).join(', ')}`);
  return value as T;
}

function parseFigure(raw: unknown, path: string): RecordFigure {
  if (!isObject(raw)) fail(`${path} must be an object {value, provenance}`);
  const provenance = oneOf(raw.provenance, FIGURE_PROVENANCES, `${path}.provenance`);
  if (provenance === 'not_measured') {
    if (raw.value !== null && raw.value !== undefined) nonNegative(raw.value, `${path}.value`);
    return { value: (raw.value ?? null) as number | null, provenance };
  }
  return { value: nonNegative(raw.value, `${path}.value`), provenance };
}

function parseStep<Id extends string, Status extends string>(
  raw: unknown,
  path: string,
  ids: readonly Id[],
  statuses: readonly Status[]
): StepRecord<Id, Status> {
  if (!isObject(raw)) fail(`${path} must be an object`);
  const reason = raw.reason;
  if (reason !== null && reason !== undefined && typeof reason !== 'string') {
    fail(`${path}.reason must be a string or null`);
  }
  const step: {
    id: Id;
    status: Status;
    reason: string | null;
    wall_clock_ms?: RecordFigure;
    bytes?: RecordFigure;
    tokens?: RecordFigure;
    cost_usd?: RecordFigure;
  } = {
    id: oneOf(raw.id, ids, `${path}.id`),
    status: oneOf(raw.status, statuses, `${path}.status`),
    reason: (reason ?? null) as string | null,
  };
  for (const key of ['wall_clock_ms', 'bytes', 'tokens', 'cost_usd'] as const) {
    if (raw[key] !== undefined && raw[key] !== null) step[key] = parseFigure(raw[key], `${path}.${key}`);
  }
  return step;
}

function parseSteps<Id extends string, Status extends string>(
  raw: unknown,
  path: string,
  ids: readonly Id[],
  statuses: readonly Status[]
): StepRecord<Id, Status>[] {
  if (!Array.isArray(raw) || raw.length === 0) fail(`${path} must be a non-empty list (steps are mandatory on both paths)`);
  return raw.map((s, i) => parseStep(s, `${path}[${i}]`, ids, statuses));
}

function parseAttempt(raw: unknown, path: string): Attempt {
  if (!isObject(raw)) fail(`${path} must be an object`);
  if (!CHALLENGES.includes(raw.challenge as ChallengeObservation)) {
    fail(`${path}.challenge must record an observation only, never a satisfied challenge`);
  }
  const status = raw.status;
  if (status !== null && (typeof status !== 'number' || !Number.isInteger(status))) {
    fail(`${path}.status must be an HTTP status code or null`);
  }
  const optional = (key: 'document_bytes' | 'requests' | 'external_refs' | 'document_ms') =>
    raw[key] === undefined || raw[key] === null ? {} : { [key]: nonNegative(raw[key], `${path}.${key}`) };
  return {
    n: nonNegative(raw.n, `${path}.n`),
    status: status as number | null,
    bytes: nonNegative(raw.bytes, `${path}.bytes`),
    wall_clock_ms: nonNegative(raw.wall_clock_ms, `${path}.wall_clock_ms`),
    challenge: raw.challenge as ChallengeObservation,
    ...optional('document_bytes'),
    ...optional('requests'),
    ...optional('external_refs'),
    ...optional('document_ms'),
  };
}

function parseAttempts(raw: unknown, path: string): Attempt[] {
  if (!Array.isArray(raw)) fail(`${path} must be a list`);
  return raw.map((a, i) => parseAttempt(a, `${path}[${i}]`));
}

function parseScrape(raw: unknown): ScrapePathRecord {
  const path = 'paths.scrape';
  if (!isObject(raw)) fail(`${path} must be an object`);
  const licence = raw.licence_advertised;
  if (licence !== null && licence !== undefined && typeof licence !== 'string') {
    fail(`${path}.licence_advertised must be a string or null`);
  }
  return {
    outcome: oneOf(raw.outcome, SCRAPE_OUTCOMES, `${path}.outcome`),
    attempts: parseAttempts(raw.attempts, `${path}.attempts`),
    steps: parseSteps(raw.steps, `${path}.steps`, SCRAPE_STEP_IDS, SCRAPE_STATUSES),
    content_tokens: nonNegativeOrNull(raw.content_tokens, `${path}.content_tokens`),
    visible_text_tokens: nonNegativeOrNull(raw.visible_text_tokens, `${path}.visible_text_tokens`),
    visible_words: nonNegativeOrNull(raw.visible_words, `${path}.visible_words`),
    article_words: nonNegativeOrNull(raw.article_words, `${path}.article_words`),
    licence_advertised: (licence ?? null) as string | null,
  };
}

function parseApi(raw: unknown): ApiPathRecord {
  const path = 'paths.api';
  if (!isObject(raw)) fail(`${path} must be an object`);
  if (raw.auth !== 'cognito_user') fail(`${path}.auth must be cognito_user`);

  let refetch: ApiPathRecord['conditional_refetch'] = null;
  if (raw.conditional_refetch !== undefined && raw.conditional_refetch !== null) {
    const r = raw.conditional_refetch;
    if (!isObject(r)) fail(`${path}.conditional_refetch must be an object or null`);
    refetch = {
      status: nonNegative(r.status, `${path}.conditional_refetch.status`),
      bytes: nonNegative(r.bytes, `${path}.conditional_refetch.bytes`),
    };
  }

  let provenance: ApiProvenance | null = null;
  if (raw.provenance !== undefined && raw.provenance !== null) {
    const p = raw.provenance;
    if (!isObject(p)) fail(`${path}.provenance must be an object or null`);
    const str = (key: keyof ApiProvenance) => {
      if (typeof p[key] !== 'string') fail(`${path}.provenance.${key} must be a string`);
      return p[key] as string;
    };
    provenance = {
      title: str('title'),
      author: str('author'),
      pubdate: str('pubdate'),
      canonical_url: str('canonical_url'),
    };
  }

  return {
    outcome: oneOf(raw.outcome, API_OUTCOMES, `${path}.outcome`),
    auth: 'cognito_user',
    attempts: parseAttempts(raw.attempts, `${path}.attempts`),
    steps: parseSteps(raw.steps, `${path}.steps`, API_STEP_IDS, API_STATUSES),
    content_tokens: nonNegativeOrNull(raw.content_tokens, `${path}.content_tokens`),
    words: nonNegativeOrNull(raw.words, `${path}.words`),
    conditional_refetch: refetch,
    provenance,
  };
}

/** Validate an untrusted document and return a typed run record, or throw RunRecordError. */
export function parseRunRecord(raw: unknown): RunRecord {
  if (!isObject(raw)) fail('expected a JSON object');
  if (raw.schema_version !== RUN_SCHEMA_VERSION) {
    fail(
      `schema_version ${String(raw.schema_version)} is not supported; this page reads schema_version ${RUN_SCHEMA_VERSION} only`
    );
  }
  assertNoCredentials(raw, 'record');

  const article = raw.article;
  if (!isObject(article)) fail('article must be an object');
  const websiteUrl = nonEmptyString(article.website_url, 'article.website_url');
  if (!isHttpUrl(websiteUrl)) fail('article.website_url must be an http(s) URL');
  const markdownKey = article.markdown_key;
  if (markdownKey !== null && typeof markdownKey !== 'string') {
    fail('article.markdown_key must be a string or null');
  }

  const paths = raw.paths;
  if (!isObject(paths)) fail('paths must be an object');
  const measured = raw.measured;
  if (!isObject(measured)) fail('measured must be an object');

  return {
    schema_version: RUN_SCHEMA_VERSION,
    run_id: nonEmptyString(raw.run_id, 'run_id'),
    started_at: nonEmptyString(raw.started_at, 'started_at'),
    producer: oneOf(raw.producer, PRODUCERS, 'producer'),
    tokenizer: nonEmptyString(raw.tokenizer, 'tokenizer'),
    article: {
      website_url: websiteUrl,
      final_url: nonEmptyString(article.final_url, 'article.final_url'),
      canonical_url: nonEmptyString(article.canonical_url, 'article.canonical_url'),
      markdown_key: markdownKey as string | null,
    },
    paths: { scrape: parseScrape(paths.scrape), api: parseApi(paths.api) },
    measured: {
      success_rate: rateOrNull(measured.success_rate, 'measured.success_rate'),
      bytes_per_attempt: nonNegativeOrNull(measured.bytes_per_attempt, 'measured.bytes_per_attempt'),
      challenge_rate: rateOrNull(measured.challenge_rate, 'measured.challenge_rate'),
    },
  };
}

/** Parse JSON text into a run record. */
export function parseRunRecordText(text: string): RunRecord {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    throw new RunRecordError('Invalid run record: the text is not valid JSON');
  }
  return parseRunRecord(raw);
}

export interface LatencySummary {
  readonly attempts: number;
  readonly medianMs: number;
  readonly worstMs: number;
  /** A single attempt has one value; it must not be presented as a percentile (R2). */
  readonly single: boolean;
}

/** Median and worst wall-clock time, derived at read time from the attempt list. */
export function latencySummary(attempts: readonly Attempt[]): LatencySummary | null {
  if (attempts.length === 0) return null;
  const sorted = attempts.map((a) => a.wall_clock_ms).sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  const median = sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
  return {
    attempts: sorted.length,
    medianMs: median,
    worstMs: sorted[sorted.length - 1],
    single: sorted.length === 1,
  };
}

export function totalBytes(attempts: readonly Attempt[]): number {
  return attempts.reduce((sum, a) => sum + a.bytes, 0);
}

export function isHttpUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' || url.protocol === 'http:';
  } catch {
    return false;
  }
}
