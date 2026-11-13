import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('aws-amplify/auth', () => ({
  getCurrentUser: vi.fn().mockResolvedValue({ username: 'u' }),
  fetchAuthSession: vi.fn().mockResolvedValue({ tokens: { idToken: { toString: () => 'id-token' } } }),
}));

import {
  CONDITIONAL_REFETCH_REASON,
  pollRun,
  pollUntilReady,
  RunRequestError,
  SessionExpiredError,
  submitRun,
  toRunRecord,
  allowListSentence,
  markdownLocation,
  originLabel,
  publishedRecordUrl,
} from '../comparisonRunClient';
import { measureMarkdown, runCleanup, TOKENIZER_LABEL } from '../comparisonMeasure';
import { fakeCount, pendingPoll, readyPoll, RUN_ID, submitResponse } from './comparisonFixtures';

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

describe('submitRun / pollRun', () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it('POSTs the url with the bearer token and returns the 202 body', async () => {
    fetchMock.mockResolvedValue(json(202, submitResponse()));
    const res = await submitRun('https://publisher.example/a.html', true);
    expect(res.run_id).toBe(RUN_ID);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/test\/comparison-runs$/);
    expect(init.method).toBe('POST');
    expect(init.headers.Authorization).toBe('Bearer id-token');
    expect(JSON.parse(init.body)).toEqual({ url: 'https://publisher.example/a.html', reingest: true });
  });

  it('maps a 400 to RunRequestError carrying the error_code', async () => {
    fetchMock.mockResolvedValue(json(400, { error: 'host not allowed', error_code: 'url_not_allowed' }));
    await expect(submitRun('https://x.example')).rejects.toMatchObject({
      name: 'RunRequestError',
      status: 400,
      errorCode: 'url_not_allowed',
      message: 'host not allowed',
    });
  });

  it('keeps error details on a non-400 refusal', async () => {
    fetchMock.mockResolvedValue(
      json(409, { error: 'conflict', error_code: 'conflict', details: { content_id: 'cnt_9' } })
    );
    const err = await submitRun('https://x.example', true).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(RunRequestError);
    expect((err as RunRequestError).details).toEqual({ content_id: 'cnt_9' });
  });

  it('maps a 401 to SessionExpiredError for both calls', async () => {
    fetchMock.mockImplementation(async () => json(401, { error: 'Unauthorized' }));
    await expect(submitRun('https://x.example')).rejects.toBeInstanceOf(SessionExpiredError);
    await expect(pollRun(RUN_ID)).rejects.toBeInstanceOf(SessionExpiredError);
  });

  it('GETs the run by id', async () => {
    fetchMock.mockResolvedValue(json(200, pendingPoll()));
    const res = await pollRun(RUN_ID);
    expect(res.status).toBe('pending');
    expect(String(fetchMock.mock.calls[0][0])).toMatch(new RegExp(`/test/comparison-runs/${RUN_ID}$`));
    expect(fetchMock.mock.calls[0][1].method).toBe('GET');
  });
});

describe('pollUntilReady', () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it('backs off while pending and returns when ready', async () => {
    fetchMock
      .mockResolvedValueOnce(json(200, pendingPoll(1000)))
      .mockResolvedValueOnce(json(200, pendingPoll(2500)))
      .mockResolvedValueOnce(json(200, readyPoll()));
    const delays: number[] = [];
    const seen: number[] = [];
    const out = await pollUntilReady(RUN_ID, {
      sleep: async (ms) => void delays.push(ms),
      now: () => 0,
      limitMs: 60_000,
      onPending: (p) => void seen.push(p.elapsed_ms),
    });
    expect(out.kind).toBe('ready');
    expect(delays).toEqual([1000, 1500]);
    expect(seen).toEqual([1000, 2500]);
  });

  it('caps the delay at the maximum', async () => {
    fetchMock.mockImplementation(async () => json(200, pendingPoll()));
    const delays: number[] = [];
    let t = 0;
    await pollUntilReady(RUN_ID, {
      sleep: async (ms) => {
        delays.push(ms);
        t += ms;
      },
      now: () => t,
      limitMs: 40_000,
      maxDelayMs: 4000,
    });
    expect(Math.max(...delays)).toBe(4000);
  });

  it('times out at the limit and keeps the last poll', async () => {
    fetchMock.mockImplementation(async () => json(200, pendingPoll(7000)));
    let t = 0;
    const out = await pollUntilReady(RUN_ID, {
      sleep: async (ms) => void (t += ms),
      now: () => t,
      limitMs: 5000,
    });
    expect(out).toMatchObject({ kind: 'timeout', limitMs: 5000 });
    expect(out.kind === 'timeout' && out.poll?.elapsed_ms).toBe(7000);
  });

  it('stops when aborted', async () => {
    const controller = new AbortController();
    controller.abort();
    const out = await pollUntilReady(RUN_ID, { signal: controller.signal });
    expect(out.kind).toBe('aborted');
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('toRunRecord', () => {
  it('builds a valid schema-2 record for a ready run, labelling every figure', async () => {
    const sub = submitResponse();
    const cleanup = await runCleanup(sub.html, fakeCount);
    const poll = readyPoll();
    const md = await measureMarkdown(poll.api!.markdown, fakeCount);
    const rec = toRunRecord(sub, poll, cleanup, TOKENIZER_LABEL, { markdown: md });

    expect(rec.schema_version).toBe(2);
    expect(rec.producer).toBe('route+browser');
    expect(rec.paths.scrape.steps.map((s) => s.id)).toEqual([
      'fetch_document',
      'fetch_subresources',
      'strip_scripts_styles',
      'extract_article',
      'remove_boilerplate',
      'normalize',
      'tokenize',
    ]);
    expect(rec.paths.api.steps.map((s) => s.id)).toEqual(['ingest', 'resolve', 'fetch_markdown', 'conditional_refetch', 'tokenize']);
    expect(rec.paths.scrape.attempts[0]).toMatchObject({ bytes: 400_000, wall_clock_ms: 1500, document_bytes: 40_000, requests: 12 });
    expect(rec.paths.scrape.steps[1].bytes?.value).toBe(360_000);
    expect(rec.paths.scrape.content_tokens).toBe(cleanup.documentTokens);
    expect(rec.paths.api.outcome).toBe('obtained');
    expect(rec.paths.api.attempts[0]).toMatchObject({ bytes: 2000, wall_clock_ms: 80 });
    expect(rec.paths.api.content_tokens).toBe(md.tokens);
    expect(rec.paths.api.provenance?.author).toBe('A. Writer');
    expect(rec.measured).toEqual({ success_rate: null, bytes_per_attempt: 400_000, challenge_rate: null });
  });

  it('records the conditional re-fetch as not measured with the reason', async () => {
    const sub = submitResponse();
    const cleanup = await runCleanup(sub.html, fakeCount);
    const md = await measureMarkdown('# t', fakeCount);
    const rec = toRunRecord(sub, readyPoll(), cleanup, TOKENIZER_LABEL, { markdown: md });
    const step = rec.paths.api.steps.find((s) => s.id === 'conditional_refetch');
    expect(step).toMatchObject({ status: 'not_measured', reason: CONDITIONAL_REFETCH_REASON });
    expect(step?.wall_clock_ms).toEqual({ value: null, provenance: 'not_measured' });
  });

  it('marks ingest as skipped when an existing record was reused', async () => {
    const sub = submitResponse({ ingest: 'reused' });
    const cleanup = await runCleanup(sub.html, fakeCount);
    const md = await measureMarkdown('# t', fakeCount);
    const rec = toRunRecord(sub, readyPoll(), cleanup, TOKENIZER_LABEL, { markdown: md });
    expect(rec.paths.api.steps[0]).toMatchObject({ id: 'ingest', status: 'skipped' });
  });

  it('builds a pending Path B while the pipeline runs', async () => {
    const sub = submitResponse();
    const cleanup = await runCleanup(sub.html, fakeCount);
    const rec = toRunRecord(sub, pendingPoll(4000), cleanup, TOKENIZER_LABEL);
    expect(rec.paths.api.outcome).toBe('pending');
    expect(rec.paths.api.steps[0]).toMatchObject({ id: 'ingest', status: 'pending' });
    expect(rec.paths.api.steps[0].wall_clock_ms?.value).toBe(4000);
    expect(rec.paths.api.attempts).toEqual([]);
    expect(rec.paths.api.content_tokens).toBeNull();
  });

  it('builds a precondition failure after the poll window', async () => {
    const sub = submitResponse();
    const cleanup = await runCleanup(sub.html, fakeCount);
    const rec = toRunRecord(sub, pendingPoll(181_000), cleanup, TOKENIZER_LABEL, { timedOutAfterMs: 180_000 });
    expect(rec.paths.api.outcome).toBe('precondition_failed');
    expect(rec.paths.api.steps[0]).toMatchObject({ id: 'ingest', status: 'failed' });
    expect(rec.paths.api.steps[0].reason).toMatch(/180000 ms/);
    expect(rec.paths.scrape.outcome).toBe('obtained');
  });

  it('classifies Path S outcomes by status and never records a satisfied challenge', async () => {
    const cleanup = await runCleanup('<body>x</body>', fakeCount);
    const base = submitResponse();
    for (const [status, outcome] of [[403, 'refused'], [429, 'refused'], [500, 'failed'], [200, 'obtained']] as const) {
      const rec = toRunRecord({ ...base, scrape: { ...base.scrape, status } }, pendingPoll(), cleanup, TOKENIZER_LABEL);
      expect(rec.paths.scrape.outcome).toBe(outcome);
      expect(rec.paths.scrape.attempts[0].challenge).toBeNull();
    }
  });

  it('requires the markdown measurement for a ready poll', async () => {
    const sub = submitResponse();
    const cleanup = await runCleanup(sub.html, fakeCount);
    expect(() => toRunRecord(sub, readyPoll(), cleanup, TOKENIZER_LABEL)).toThrow(/markdown measurement/);
  });
});

describe('allowListSentence (R7.3, R15.7 amended 2026-11-12)', () => {
  it('says in plain words what the deployment may fetch', () => {
    expect(allowListSentence('open')).toBe('This deployment may fetch any public https page.');
    expect(allowListSentence('restricted')).toBe('This deployment fetches only the hosts on its allow-list.');
  });
});

describe('originLabel (R15.7, amended 2026-11-12)', () => {
  it('names where the licensed Markdown comes from and who sees it; a run adds nothing to the catalogue', () => {
    expect(originLabel({ origin: 'owned', ingest: 'reused' })).toBe(
      'This is your content: the comparison uses your published Markdown, the one partners and search use.'
    );
    expect(originLabel({ origin: 'owned', ingest: 'submitted' })).toBe(
      "This is your content: this run's Markdown is kept for this comparison only, outside your catalogue, so partners and search never see it."
    );
    expect(originLabel({ origin: 'external', ingest: 'submitted' })).toBe(
      'This page is external: its Markdown is kept for this comparison only, outside your catalogue, so partners and search never see it.'
    );
  });
});

describe('markdownLocation (R15.7)', () => {
  it('names the partition key and that it is outside the catalogue for a submitted run', () => {
    const sub = submitResponse({ ingest: 'submitted', article: { ...submitResponse().article, markdown_key: 'comparison/abc.md' } });
    expect(markdownLocation(sub)).toEqual({ key: 'comparison/abc.md', note: 'comparison partition, outside your catalogue' });
  });

  it('names the catalogue key for a reused run', () => {
    const sub = submitResponse({ ingest: 'reused', article: { ...submitResponse().article, markdown_key: 'content/publisher.example/a.md' } });
    expect(markdownLocation(sub).key).toBe('content/publisher.example/a.md');
    expect(markdownLocation(sub).note).toBe('your published catalogue object');
  });
});

describe('publishedRecordUrl (R1.7)', () => {
  const URL_A = 'https://publisher.example/a.html';
  it('offers a fresh run only for a URL whose published record a run reused', () => {
    expect(publishedRecordUrl(URL_A, { origin: 'owned', ingest: 'reused' }, false)).toBe(URL_A);
  });
  it('keeps the offer after a fresh run of that URL', () => {
    expect(publishedRecordUrl(URL_A, { origin: 'owned', ingest: 'submitted' }, true)).toBe(URL_A);
  });
  it('withdraws it for an owned article not yet published and for an external page', () => {
    expect(publishedRecordUrl(URL_A, { origin: 'owned', ingest: 'submitted' }, false)).toBeNull();
    expect(publishedRecordUrl(URL_A, { origin: 'external', ingest: 'submitted' }, false)).toBeNull();
    expect(publishedRecordUrl(URL_A, { origin: 'external', ingest: 'submitted' }, true)).toBeNull();
  });
});
