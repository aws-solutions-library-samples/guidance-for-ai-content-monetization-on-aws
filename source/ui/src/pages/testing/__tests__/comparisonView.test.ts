import { describe, expect, it } from 'vitest';

import { parseRunRecord } from '../../../services/comparisonRunService';
import { rawRecord } from '../../../services/__tests__/comparisonFixtures';
import {
  buildAtAGlance,
  computeCostModel,
  computeHeadline,
  CostInputError,
  coveragePercent,
  coverageTone,
  DEFAULT_COST_INPUTS,
  formatBytes,
  formatCount,
  formatMs,
  formatUsd,
  gainActuals,
  gainPhrase,
  verdictSentence,
} from '../comparisonView';

describe('formatters', () => {
  it('formats times, sizes and money without a currency prefix', () => {
    expect(formatMs(250)).toBe('250 ms');
    expect(formatMs(1500)).toBe('1.50 s');
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(2048)).toBe('2.0 KB');
    expect(formatBytes(3 * 1024 * 1024)).toBe('3.00 MB');
    expect(formatUsd(0.002)).toBe('0.002000 USD');
    expect(formatUsd(12.5)).toBe('12.50 USD');
    expect(formatUsd(0.5)).not.toContain('$');
  });
});

describe('computeHeadline', () => {
  it('compares document tokens, total time and bytes against Path B', () => {
    const h = computeHeadline(parseRunRecord(rawRecord()));
    expect(h?.tokens.pct).toBe(98); // 16000 -> 300
    expect(h?.tokensLowerBound.pct).toBe(67); // 900 -> 300
    expect(h?.time.pct).toBe(95); // 1500 -> 80
    expect(h?.bytes.pct).toBe(100); // 400000 -> 2000 (99.5 rounds to 100)
    expect(h?.tokens.favourable).toBe(true);
  });

  it('shows an unfavourable result as a negative figure', () => {
    const raw = rawRecord() as any; // eslint-disable-line @typescript-eslint/no-explicit-any
    raw.paths.api.content_tokens = 32000;
    raw.paths.api.attempts[0].wall_clock_ms = 3000;
    const h = computeHeadline(parseRunRecord(raw));
    expect(h?.tokens).toMatchObject({ pct: -100, favourable: false, scrape: 16000, markdown: 32000 });
    expect(h?.time.favourable).toBe(false);
  });

  it('is null until Path B is obtained', () => {
    const raw = rawRecord() as any; // eslint-disable-line @typescript-eslint/no-explicit-any
    raw.paths.api.outcome = 'pending';
    expect(computeHeadline(parseRunRecord(raw))).toBeNull();
  });
});

describe('headline actuals and phrasing (R3.0)', () => {
  it('carries the actual values behind each percentage', () => {
    const record = parseRunRecord(rawRecord());
    const h = computeHeadline(record)!;
    expect(h.tokens.scrape).toBe(record.paths.scrape.content_tokens);
    expect(h.tokens.markdown).toBe(record.paths.api.content_tokens);
    expect(gainActuals(h.tokens, 'count')).toBe(`${formatCount(h.tokens.scrape!)} → ${formatCount(h.tokens.markdown!)}`);
    expect(gainActuals(h.bytes, 'bytes')).toMatch(/→/);
    expect(gainActuals(h.time, 'ms')).toMatch(/ms|s/);
  });

  it('uses "fewer" for counts and "less" for time, and "more" when unfavourable', () => {
    const fav = { pct: 49, favourable: true, scrape: 1536, markdown: 790 };
    const unfav = { pct: -12, favourable: false, scrape: 100, markdown: 112 };
    expect(gainPhrase(fav, 'tokens')).toBe('49% fewer tokens');
    expect(gainPhrase(fav, 'bytes')).toBe('49% fewer bytes');
    expect(gainPhrase(fav, 'time')).toBe('49% less time');
    expect(gainPhrase(unfav, 'tokens')).toBe('12% more tokens');
    expect(gainPhrase(unfav, 'time')).toBe('12% more time');
    expect(gainPhrase({ pct: null, favourable: null, scrape: null, markdown: null }, 'tokens')).toBeNull();
  });

  it('never claims 100% while the Markdown side is non-zero (the actuals would contradict it)', () => {
    // 573,383 -> 2,588 rounds to 100% but the Markdown is not empty.
    expect(gainPhrase({ pct: 100, favourable: true, scrape: 573_383, markdown: 2_588 }, 'tokens')).toBe('>99% fewer tokens');
    expect(gainPhrase({ pct: 100, favourable: true, scrape: 400_000, markdown: 2_000 }, 'bytes')).toBe('>99% fewer bytes');
    expect(gainPhrase({ pct: 100, favourable: true, scrape: 1_000, markdown: 1 }, 'time')).toBe('>99% less time');
    // A genuine 100% (nothing on the Markdown side) is still 100%.
    expect(gainPhrase({ pct: 100, favourable: true, scrape: 1_000, markdown: 0 }, 'tokens')).toBe('100% fewer tokens');
  });

  it('returns null actuals when a side is missing', () => {
    expect(gainActuals({ pct: null, favourable: null, scrape: 10, markdown: null }, 'count')).toBeNull();
  });
});

describe('buildAtAGlance (R3.0b)', () => {
  const record = parseRunRecord(rawRecord());
  const headline = computeHeadline(record)!;

  it('builds one row per metric with publisher vocabulary and evidence labels', () => {
    const rows = buildAtAGlance({ record, headline, coverage: null, cost: null });
    const ids = rows.map((r) => r.id);
    expect(ids).toEqual(['tokens', 'tokens_lower_bound', 'bytes', 'requests', 'time', 'article_share', 'licence', 'provenance']);
    for (const r of rows) {
      expect(r.provenance).toBe('measured');
      expect(JSON.stringify(r)).not.toMatch(/Path [SB]/);
    }
    const tokens = rows.find((r) => r.id === 'tokens')!;
    expect(tokens.scrape).toBe(formatCount(record.paths.scrape.content_tokens!));
    expect(tokens.markdown).toBe(formatCount(record.paths.api.content_tokens!));
    expect(tokens.difference).toMatch(/fewer tokens|more tokens/);
  });

  it('adds the modelled cost row, labelled Modelled, as cost per 1,000 with a saving', () => {
    const cost = {
      scrapeTotal: { value: 2.1, provenance: 'assumption' as const, inputs: [] },
      api: { value: 0.504, provenance: 'assumption' as const, inputs: [], delivery: 0.004, licence: 0.5 },
    };
    const rows = buildAtAGlance({ record, headline, coverage: null, cost });
    const row = rows.find((r) => r.id === 'cost')!;
    expect(row.provenance).toBe('modelled');
    expect(row.scrape).toBe('2.10 USD');
    expect(row.markdown).toBe('0.5040 USD');
    expect(row.difference).toBe('1.60 USD saved per 1,000');
    expect(row.tone).toBe('favourable');
    expect(row.note).toContain('0.5000 USD per 1,000');
  });

  it('flags a modelled cost that is higher on the licensed side as unfavourable', () => {
    const cost = {
      scrapeTotal: { value: 0.3, provenance: 'assumption' as const, inputs: [] },
      api: { value: 5, provenance: 'assumption' as const, inputs: [], delivery: 0, licence: 5 },
    };
    const row = buildAtAGlance({ record, headline, coverage: null, cost }).find((r) => r.id === 'cost')!;
    expect(row.tone).toBe('unfavourable');
    expect(row.difference).toBe('4.70 USD more per 1,000');
  });

  it('adds the coverage row from R14 and grades it', () => {
    const full = buildAtAGlance({ record, headline, coverage: { coverage: 1, articleMatched: 34, articleSentences: 34 }, cost: null });
    expect(full.find((r) => r.id === 'coverage')).toMatchObject({ markdown: '100% (34 of 34 sentences)', difference: 'same article', tone: 'favourable' });
    const lossy = buildAtAGlance({ record, headline, coverage: { coverage: 0.6, articleMatched: 6, articleSentences: 10 }, cost: null });
    expect(lossy.find((r) => r.id === 'coverage')).toMatchObject({ difference: '40% of sentences missing', tone: 'unfavourable' });
  });

  it('shows the licence and provenance rows as the qualitative advantages, with the provenance values', () => {
    const rows = buildAtAGlance({ record, headline, coverage: null, cost: null });
    expect(rows.find((r) => r.id === 'licence')).toMatchObject({ scrape: 'none', markdown: 'licensed', tone: 'favourable' });
    const prov = rows.find((r) => r.id === 'provenance')!;
    expect(prov.scrape).toBe('none');
    expect(prov.markdown).toBe('title, author, date, canonical URL');
    expect(prov.note).toContain(`Author: ${record.paths.api.provenance!.author}`);
  });

  it('claims only the provenance fields the Markdown actually carries', () => {
    const raw = rawRecord() as any; // eslint-disable-line @typescript-eslint/no-explicit-any
    raw.paths.api.provenance = { title: 'T', author: '', pubdate: '2026-11-02', canonical_url: 'https://publisher.example/a.html' };
    const prov = buildAtAGlance({ record: parseRunRecord(raw), headline, coverage: null, cost: null }).find((r) => r.id === 'provenance')!;
    expect(prov.markdown).toBe('title, date, canonical URL');
    expect(prov.note).toContain('Author: n/a');
    expect(prov.tone).toBe('favourable');
    raw.paths.api.provenance = null;
    const none = buildAtAGlance({ record: parseRunRecord(raw), headline, coverage: null, cost: null }).find((r) => r.id === 'provenance')!;
    expect(none).toMatchObject({ markdown: 'not available', tone: 'none' });
    expect(none.note).toBeUndefined();
  });

  it('shows the article-text share of the page (R5.3) as a neutral measured row', () => {
    const row = buildAtAGlance({ record, headline, coverage: null, cost: null }).find((r) => r.id === 'article_share')!;
    expect(row).toMatchObject({ tone: 'neutral', provenance: 'measured', markdown: 'only the article' });
    expect(row.scrape).toMatch(/^\d+\.\d%$/);
    const raw = rawRecord() as any; // eslint-disable-line @typescript-eslint/no-explicit-any
    raw.paths.scrape.article_words = null;
    const na = buildAtAGlance({ record: parseRunRecord(raw), headline, coverage: null, cost: null }).find((r) => r.id === 'article_share')!;
    expect(na.scrape).toBe('not available');
    expect(na.note).toMatch(/no <article> element/);
  });
});

describe('verdictSentence (R3.0)', () => {
  const record = parseRunRecord(rawRecord());
  const headline = computeHeadline(record)!; // tokens 98%, bytes 100%, time 95%

  it('reads lead, gains, time and coverage in one sentence', () => {
    expect(verdictSentence(headline, { coverage: 1, articleMatched: 34, articleSentences: 34 })).toBe(
      "Same article, 98% fewer tokens, >99% fewer bytes, in 95% less time; 100% of the article's sentences kept."
    );
    expect(verdictSentence(headline, { coverage: 0.9, articleMatched: 9, articleSentences: 10 })).toMatch(/^Most of the article, /);
    expect(verdictSentence(headline, { coverage: 0.5, articleMatched: 5, articleSentences: 10 })).toMatch(/^Only part of the article, .*; 50% of the article's sentences kept\.$/);
  });

  it('drops the coverage clause while coverage is unavailable and keeps unfavourable wording', () => {
    expect(verdictSentence(headline, null)).toBe('Licensed Markdown: 98% fewer tokens, >99% fewer bytes, in 95% less time.');
    const worse = { ...headline, time: { pct: -20, favourable: false, scrape: 100, markdown: 120 } };
    expect(verdictSentence(worse, null)).toContain('in 20% more time');
  });

  it('grades coverage with the same thresholds as the table', () => {
    expect(coverageTone(100)).toBe('favourable');
    expect(coverageTone(95)).toBe('favourable');
    expect(coverageTone(80)).toBe('neutral');
    expect(coverageTone(79)).toBe('unfavourable');
    expect(coverageTone(null)).toBe('none');
    expect(coveragePercent({ coverage: 0.928, articleMatched: 64, articleSentences: 69 })).toBe(93);
    expect(coveragePercent(null)).toBeNull();
  });
});

describe('computeCostModel', () => {
  it('works over operator inputs alone and labels the fallbacks as assumptions', () => {
    const r = computeCostModel(null, DEFAULT_COST_INPUTS);
    expect(r.breakEven.value).toBeGreaterThan(0);
    expect(r.measuredInputs.every((m) => m.provenance === 'assumption')).toBe(true);
    expect(r.scrapeTotal.provenance).toBe('assumption');
    expect(r.stepCostsUsd).toEqual({ scrape: {}, api: {} });
  });

  it('uses measured values from a run and allocates per-step cost', () => {
    const rec = parseRunRecord(rawRecord());
    const r = computeCostModel(rec, DEFAULT_COST_INPUTS);
    expect(r.measuredInputs.find((m) => m.label.startsWith('Bytes per scrape'))?.provenance).toBe('measured');
    const scrapeSum = Object.values(r.stepCostsUsd.scrape).reduce((a, b) => a + b, 0);
    expect(scrapeSum).toBeCloseTo(r.breakEven.value, 8);
    expect(r.stepCostsUsd.api.fetch_markdown).toBeCloseTo(r.api.value / 1000, 10);
    expect(r.tokenDelta?.value).toBeGreaterThan(0);
  });

  it('reports the unfavourable branch when P is at or above P*', () => {
    const r = computeCostModel(null, { ...DEFAULT_COST_INPUTS, publisherPricePerArticle: '1' });
    expect(r.saving.favourable).toBe(false);
    expect(r.saving.ratio).toBeLessThan(0);
    expect(r.beatsUnblocking).toBe(false);
  });

  it('rejects a non-numeric input by name', () => {
    expect(() => computeCostModel(null, { ...DEFAULT_COST_INPUTS, articlesPerMonth: '' })).toThrow(CostInputError);
    expect(() => computeCostModel(null, { ...DEFAULT_COST_INPUTS, successRate: '0' })).toThrow();
  });
});
