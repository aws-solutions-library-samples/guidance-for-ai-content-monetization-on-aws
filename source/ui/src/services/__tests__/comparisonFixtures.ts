/** Shared test fixtures: a schema-2 raw record and the two route responses. */

import type { PollResponse, SubmitResponse } from '../comparisonRunClient';

const fig = (value: number | null, provenance = 'measured') => ({ value, provenance });

export const RUN_ID = `${'a'.repeat(64)}-1700000000`;

export const articleHtml = `<!doctype html><html><head><title>t</title>
<link rel="canonical" href="https://publisher.example/a.html">
<script>var x = 1;</script><style>p{color:red}</style></head>
<body><nav>Home About Contact</nav>
<article><h1>Headline</h1><p>One two three four five six seven eight nine ten.</p></article>
<aside>Related stories</aside><footer>Copyright</footer></body></html>`;

export function submitResponse(overrides: Partial<SubmitResponse> = {}): SubmitResponse {
  return {
    run_id: RUN_ID,
    submitted_at: '2026-11-03T10:00:00Z',
    ingest: 'submitted',
    origin: 'owned',
    allow_list_mode: 'restricted',
    article: {
      website_url: 'https://publisher.example/a.html',
      final_url: 'https://publisher.example/a.html',
      canonical_url: 'https://publisher.example/a.html',
      canonical_injected: false,
      markdown_key: 'content/publisher.example/a.md',
      licence_advertised: null,
    },
    scrape: {
      status: 200,
      document_bytes: 40_000,
      total_bytes: 400_000,
      requests: 12,
      external_refs: 3,
      document_ms: 300,
      total_ms: 1500,
      subresources_probed: 8,
      subresources_skipped: 1,
    },
    html: articleHtml,
    ...overrides,
  };
}

export function readyPoll(overrides: Partial<PollResponse> = {}): PollResponse {
  return {
    run_id: RUN_ID,
    status: 'ready',
    elapsed_ms: 9000,
    api: {
      content_id: 'cnt_1',
      resolve_ms: 20,
      fetch_ms: 60,
      bytes: 2000,
      markdown: '# Headline\n\nOne two three four five six seven eight nine ten.\n',
      etag: '"abc"',
      updated_at: '2026-11-03T10:00:09Z',
      provenance: { title: 'Headline', author: 'A. Writer', pubdate: '2026-11-01', canonical_url: 'https://publisher.example/a.html' },
    },
    ...overrides,
  };
}

export const pendingPoll = (elapsed = 5000): PollResponse => ({
  run_id: RUN_ID,
  status: 'pending',
  elapsed_ms: elapsed,
});

/** Word-count based fake tokenizer: deterministic, no tokenizer download. */
export const fakeCount = async (text: string): Promise<number> => (text.match(/\S+/g) ?? []).length;

const step = (id: string, status = 'ok', extra: Record<string, unknown> = {}) => ({
  id,
  status,
  reason: null,
  wall_clock_ms: fig(10),
  bytes: fig(100),
  tokens: fig(10),
  ...extra,
});

export function rawRecord(): Record<string, unknown> {
  return {
    schema_version: 2,
    run_id: RUN_ID,
    started_at: '2026-11-03T10:00:00Z',
    producer: 'route+browser',
    tokenizer: 'o200k_base (gpt-tokenizer 4.1.0)',
    article: {
      website_url: 'https://publisher.example/a.html',
      final_url: 'https://publisher.example/a.html',
      canonical_url: 'https://publisher.example/a.html',
      markdown_key: 'content/publisher.example/a.md',
    },
    paths: {
      scrape: {
        outcome: 'obtained',
        attempts: [{ n: 1, status: 200, bytes: 400_000, wall_clock_ms: 1500, challenge: null }],
        steps: [step('fetch_document'), step('fetch_subresources'), step('strip_scripts_styles'), step('tokenize')],
        content_tokens: 16000,
        visible_text_tokens: 900,
        visible_words: 700,
        article_words: 500,
        licence_advertised: null,
      },
      api: {
        outcome: 'obtained',
        auth: 'cognito_user',
        attempts: [{ n: 1, status: 200, bytes: 2000, wall_clock_ms: 80, challenge: null }],
        steps: [
          step('ingest'),
          step('resolve'),
          step('fetch_markdown'),
          step('conditional_refetch', 'not_measured', {
            reason: 'Read from the stored Markdown object, not through the content API, so there is no conditional request to time',
            wall_clock_ms: fig(null, 'not_measured'),
          }),
          step('tokenize'),
        ],
        content_tokens: 300,
        words: 200,
        conditional_refetch: null,
        provenance: { title: 'T', author: 'A', pubdate: '2026-11-01', canonical_url: 'https://publisher.example/a.html' },
      },
    },
    measured: { success_rate: null, bytes_per_attempt: 400_000, challenge_rate: null },
  };
}
