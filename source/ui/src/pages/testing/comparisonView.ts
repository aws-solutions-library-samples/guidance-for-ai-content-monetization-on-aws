/**
 * View model for the scrape-vs-licensed-Markdown comparison page: formatters,
 * the relative-gain headline (R3.2) and the cost model wiring (R4). Pure; no
 * React. Vendor names and price strings never appear here (R12.2): prices are
 * operator-editable inputs whose defaults are labelled Assumptions.
 */

import {
  allocateStepCosts,
  apiCostPerThousand,
  breakEvenPerArticle,
  cleanupComputeCostPerThousand,
  savingAtPrice,
  scrapeCostPerThousand,
  tokenDeltaCostPerThousand,
  unblockingPerArticle,
  weakestProvenance,
  type ApiBreakdown,
  type Figure,
  type Labelled,
  type SavingVerdict,
  type ScrapeBreakdown,
} from '../../services/comparisonCostModel';
import { articleTextShare, savingPercent } from '../../services/comparisonMeasure';
import type { FigureProvenance, RunRecord } from '../../services/comparisonRunService';

export const FIGURE_LABELS: Readonly<Record<FigureProvenance, string>> = {
  measured: 'Measured',
  sourced: 'Sourced',
  assumption: 'Assumption',
  modelled: 'Modelled',
  not_measured: 'Not measured',
};

export const SCRAPE_STEP_LABELS: Readonly<Record<string, string>> = {
  fetch_document: 'Fetch document',
  fetch_subresources: 'Fetch subresources (size probes)',
  strip_scripts_styles: 'Strip scripts, styles, templates',
  extract_article: 'Extract article region',
  remove_boilerplate: 'Remove boilerplate (heuristic)',
  normalize: 'Normalise whitespace',
  tokenize: 'Tokenize',
};

export const API_STEP_LABELS: Readonly<Record<string, string>> = {
  ingest: 'Ingest (one-time preparation)',
  resolve: 'Resolve (url_hash lookup)',
  fetch_markdown: 'Fetch Markdown (published object)',
  conditional_refetch: 'Conditional re-fetch (If-None-Match)',
  tokenize: 'Tokenize',
};

export function formatMs(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(2)} s`;
}

/** Decoded sizes, not compressed transfer size (R2.4). */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

export function formatCount(n: number): string {
  return n.toLocaleString('en-US');
}

/** Money is written with its unit after the number, never with a currency prefix. */
export function formatUsd(value: number): string {
  const digits = Math.abs(value) >= 1 ? 2 : Math.abs(value) >= 0.01 ? 4 : 6;
  return `${value.toFixed(digits)} USD`;
}

export interface GainFigure {
  /** Signed percentage saved by the licensed Markdown; negative means it is larger/slower. */
  readonly pct: number | null;
  readonly favourable: boolean | null;
  /** The actual values behind the percentage (R3.0): scraping vs licensed Markdown. */
  readonly scrape: number | null;
  readonly markdown: number | null;
}

export interface Headline {
  readonly tokens: GainFigure;
  /** Same comparison against the visible-text tokens: the lower bound a cleaning scraper reaches. */
  readonly tokensLowerBound: GainFigure;
  readonly time: GainFigure;
  readonly bytes: GainFigure;
}

const gain = (scrape: number | null | undefined, markdown: number | null | undefined): GainFigure => {
  const pct = savingPercent(scrape, markdown);
  return { pct, favourable: pct === null ? null : pct > 0, scrape: scrape ?? null, markdown: markdown ?? null };
};

/** The relative gains of the licensed Markdown over scraping, or null until the Markdown is obtained. */
export function computeHeadline(record: RunRecord): Headline | null {
  const { scrape, api } = record.paths;
  if (api.outcome !== 'obtained' || api.attempts.length === 0) return null;
  const apiAttempt = api.attempts[0];
  const scrapeAttempt = scrape.attempts[0];
  return {
    tokens: gain(scrape.content_tokens, api.content_tokens),
    tokensLowerBound: gain(scrape.visible_text_tokens, api.content_tokens),
    time: gain(scrapeAttempt?.wall_clock_ms, apiAttempt.wall_clock_ms),
    bytes: gain(scrapeAttempt?.bytes, apiAttempt.bytes),
  };
}

/**
 * "fewer" for counts, "less" for time (R3.0). Returns e.g. "49% fewer tokens" / "62% less time",
 * or "12% more tokens" for an unfavourable result, or null when undefined.
 */
export function gainPhrase(g: GainFigure, noun: 'tokens' | 'bytes' | 'time'): string | null {
  if (g.pct === null) return null;
  // A saving that rounds to 100% while the Markdown side is non-zero is ">99%", never "100%":
  // the actuals beneath it would contradict the figure.
  const rounded = Math.abs(g.pct);
  const magnitude = rounded === 100 && g.markdown !== null && g.markdown > 0 && g.pct > 0 ? '>99%' : `${rounded}%`;
  if (noun === 'time') return g.pct >= 0 ? `${magnitude} less time` : `${magnitude} more time`;
  return g.pct >= 0 ? `${magnitude} fewer ${noun}` : `${magnitude} more ${noun}`;
}

/** The "x → y" actuals line beneath a gain, formatted for the unit; null when either side is missing. */
export function gainActuals(g: GainFigure, unit: 'count' | 'bytes' | 'ms'): string | null {
  if (g.scrape === null || g.markdown === null) return null;
  const fmt = unit === 'bytes' ? formatBytes : unit === 'ms' ? formatMs : formatCount;
  return `${fmt(g.scrape)} → ${fmt(g.markdown)}`;
}

// ---- verdict (R3.0) ---------------------------------------------------------

export type RowTone = 'favourable' | 'unfavourable' | 'neutral' | 'none';

/** Coverage from R14, as the verdict and the table consume it. */
export interface CoverageSummary {
  readonly coverage: number | null;
  readonly articleMatched: number;
  readonly articleSentences: number;
}

/** Whole-percent coverage, or null while unavailable. */
export function coveragePercent(c: CoverageSummary | null): number | null {
  return c === null || c.coverage === null ? null : Math.round(c.coverage * 100);
}

/** Same thresholds everywhere: 95% and above is the same article, 80% and above most of it. */
export function coverageTone(pct: number | null): RowTone {
  if (pct === null) return 'none';
  return pct >= 95 ? 'favourable' : pct >= 80 ? 'neutral' : 'unfavourable';
}

/**
 * The one-line verdict (R3.0), e.g. "Same article, 49% fewer tokens, 45% fewer bytes, in 62% less
 * time; 100% of the article's sentences kept." Unfavourable figures read "more", as they are.
 */
export function verdictSentence(headline: Headline, coverage: CoverageSummary | null): string {
  const gains = [gainPhrase(headline.tokens, 'tokens'), gainPhrase(headline.bytes, 'bytes')].filter(
    (p): p is string => p !== null
  );
  const time = gainPhrase(headline.time, 'time');
  const tail = [...gains, ...(time ? [`in ${time}`] : [])].join(', ');
  const pct = coveragePercent(coverage);
  if (pct === null) return tail ? `Licensed Markdown: ${tail}.` : 'Licensed Markdown obtained.';
  const lead = pct >= 95 ? 'Same article' : pct >= 80 ? 'Most of the article' : 'Only part of the article';
  return `${lead}${tail ? `, ${tail}` : ''}; ${pct}% of the article's sentences kept.`;
}

// ---- at-a-glance table (R3.0b) ---------------------------------------------

export interface AtAGlanceRow {
  readonly id: string;
  readonly metric: string;
  readonly scrape: string;
  readonly markdown: string;
  readonly difference: string;
  readonly tone: RowTone;
  readonly provenance: FigureProvenance;
  /** Secondary note shown under the metric name. */
  readonly note?: string;
}

export interface AtAGlanceInput {
  readonly record: RunRecord;
  readonly headline: Headline;
  /** Coverage from R14; null while unavailable. */
  readonly coverage: CoverageSummary | null;
  /** The cost model's per-1,000 figures; null when the model could not be computed. */
  readonly cost: { readonly scrapeTotal: Figure; readonly api: ApiBreakdown } | null;
}

/** Colour tone of a gain: favourable, unfavourable, or none while undefined. */
export function gainTone(g: GainFigure): RowTone {
  if (g.favourable === null) return 'none';
  return g.favourable ? 'favourable' : 'unfavourable';
}

function pctWord(g: GainFigure, noun: 'tokens' | 'bytes' | 'time'): string {
  return gainPhrase(g, noun) ?? 'not available';
}

/** One row per metric, Scraping | Licensed Markdown | Difference, each with its evidence label. */
export function buildAtAGlance({ record, headline, coverage, cost }: AtAGlanceInput): AtAGlanceRow[] {
  const { scrape, api } = record.paths;
  const sAttempt = scrape.attempts[0];
  const aAttempt = api.attempts[0];
  const rows: AtAGlanceRow[] = [];

  rows.push({
    id: 'tokens',
    metric: 'Tokens handed to the model',
    note: 'Raw HTML document vs Markdown. Scripts, styles and images are not counted.',
    scrape: scrape.content_tokens === null ? '—' : formatCount(scrape.content_tokens),
    markdown: api.content_tokens === null ? '—' : formatCount(api.content_tokens),
    difference: pctWord(headline.tokens, 'tokens'),
    tone: gainTone(headline.tokens),
    provenance: 'measured',
  });
  rows.push({
    id: 'tokens_lower_bound',
    metric: 'Tokens after a perfect cleaner',
    note: 'The visible-text lower bound a cleaning scraper can reach, at best.',
    scrape: scrape.visible_text_tokens === null ? '—' : formatCount(scrape.visible_text_tokens),
    markdown: api.content_tokens === null ? '—' : formatCount(api.content_tokens),
    difference: pctWord(headline.tokensLowerBound, 'tokens'),
    tone: gainTone(headline.tokensLowerBound),
    provenance: 'measured',
  });
  rows.push({
    id: 'bytes',
    metric: 'Bytes transferred',
    note: 'Page weight incl. same-origin subresources vs the Markdown object. Decoded sizes.',
    scrape: sAttempt ? formatBytes(sAttempt.bytes) : '—',
    markdown: aAttempt ? formatBytes(aAttempt.bytes) : '—',
    difference: pctWord(headline.bytes, 'bytes'),
    tone: gainTone(headline.bytes),
    provenance: 'measured',
  });
  const sRequests = sAttempt?.requests ?? null;
  rows.push({
    id: 'requests',
    metric: 'HTTP requests',
    note: sAttempt?.external_refs ? `plus ${formatCount(sAttempt.external_refs)} cross-origin assets a scraper would also load` : undefined,
    scrape: sRequests === null ? '—' : formatCount(sRequests),
    markdown: '1',
    difference: sRequests === null ? 'not available' : sRequests > 1 ? `${formatCount(sRequests - 1)} fewer requests` : 'same',
    tone: sRequests === null ? 'none' : sRequests > 1 ? 'favourable' : 'neutral',
    provenance: 'measured',
  });
  rows.push({
    id: 'time',
    metric: 'Time to obtain the article',
    note: 'Scrape total vs resolve + fetch. The one-time ingest is excluded.',
    scrape: sAttempt ? formatMs(sAttempt.wall_clock_ms) : '—',
    markdown: aAttempt ? formatMs(aAttempt.wall_clock_ms) : '—',
    difference: pctWord(headline.time, 'time'),
    tone: gainTone(headline.time),
    provenance: 'measured',
  });

  if (cost) {
    const saving = cost.scrapeTotal.value - cost.api.value;
    rows.push({
      id: 'cost',
      metric: 'Cost per 1,000 retrievals',
      note: `Modelled from the Cost Model inputs. The licensed side is dominated by the publisher's price (${formatUsd(cost.api.licence)} per 1,000).`,
      scrape: formatUsd(cost.scrapeTotal.value),
      markdown: formatUsd(cost.api.value),
      difference: saving >= 0 ? `${formatUsd(saving)} saved per 1,000` : `${formatUsd(-saving)} more per 1,000`,
      tone: saving >= 0 ? 'favourable' : 'unfavourable',
      provenance: 'modelled',
    });
  }

  const pct = coveragePercent(coverage);
  if (coverage && pct !== null) {
    rows.push({
      id: 'coverage',
      metric: 'Article content kept',
      note: 'Sentence-level, measured on normalised text. Details on the Content fidelity tab.',
      scrape: 'all (it is the page)',
      markdown: `${pct}% (${formatCount(coverage.articleMatched)} of ${formatCount(coverage.articleSentences)} sentences)`,
      difference: pct >= 95 ? 'same article' : `${100 - pct}% of sentences missing`,
      tone: coverageTone(pct),
      provenance: 'measured',
    });
  }

  // R5.3: the article-text share of the HTML page, what a scraper has to dig the article out of.
  const share = articleTextShare(scrape.article_words, scrape.visible_words);
  rows.push({
    id: 'article_share',
    metric: 'Article text share of the page',
    note:
      share === null
        ? 'The page has no <article> element, so the share cannot be measured.'
        : `${formatCount(scrape.article_words ?? 0)} of ${formatCount(scrape.visible_words ?? 0)} visible words are inside <article>. The rest is what a scraper must strip.`,
    scrape: share === null ? 'not available' : `${(share * 100).toFixed(1)}%`,
    markdown: 'only the article',
    difference: share === null ? 'not available' : `${(100 - share * 100).toFixed(1)}% of the page is not the article`,
    tone: 'neutral',
    provenance: 'measured',
  });

  rows.push({
    id: 'licence',
    metric: 'Licence obtained',
    note: scrape.licence_advertised ? `The page advertises terms: ${scrape.licence_advertised}` : 'The page advertises no licence terms.',
    scrape: 'none',
    markdown: 'licensed',
    difference: 'a scrape obtains no licence',
    tone: 'favourable',
    provenance: 'measured',
  });
  const prov = api.provenance;
  // Only the fields the Markdown actually carries are claimed; "n/a" stays visible in the note.
  const delivered = prov
    ? [
        [prov.title, 'title'],
        [prov.author, 'author'],
        [prov.pubdate, 'date'],
        [prov.canonical_url, 'canonical URL'],
      ]
        .filter(([value]) => Boolean(value))
        .map(([, name]) => name)
    : [];
  rows.push({
    id: 'provenance',
    metric: 'Provenance delivered',
    note: prov
      ? `Title: ${prov.title || 'n/a'} · Author: ${prov.author || 'n/a'} · Published: ${prov.pubdate || 'n/a'} · Canonical: ${prov.canonical_url || 'n/a'}`
      : undefined,
    scrape: 'none',
    markdown: delivered.length > 0 ? delivered.join(', ') : 'not available',
    difference: delivered.length > 0 ? 'structured, verifiable' : 'not available',
    tone: delivered.length > 0 ? 'favourable' : 'none',
    provenance: 'measured',
  });

  return rows;
}

// ---- cost model wiring ------------------------------------------------------

/** Operator-editable inputs (strings, so a half-typed number is representable). */
export interface CostInputs {
  successRate: string;
  challengeRate: string;
  bandwidthCostPerGb: string;
  challengeCostPerThousand: string;
  computeCostPerHour: string;
  browsersPerInstance: string;
  fixedUpkeepPerMonth: string;
  articlesPerMonth: string;
  deliveryCostPerGb: string;
  requestCostPerTenThousand: string;
  publisherPricePerArticle: string;
  unblockingCostPerThousand: string;
  modelCostPerMillionTokens: string;
}

export interface InputDefinition {
  readonly key: keyof CostInputs;
  readonly label: string;
  readonly unit: string;
  /** Category of source, never a vendor name. */
  readonly category: string;
}

export const COST_INPUT_DEFINITIONS: readonly InputDefinition[] = [
  { key: 'successRate', label: 'Scrape success rate', unit: 'share in (0, 1]', category: 'operator estimate' },
  { key: 'challengeRate', label: 'Challenge rate', unit: 'share in [0, 1]', category: 'operator estimate' },
  { key: 'bandwidthCostPerGb', label: 'Proxy bandwidth', unit: 'USD per GB', category: 'residential bandwidth, list price category' },
  { key: 'challengeCostPerThousand', label: 'Challenge pricing', unit: 'USD per 1,000 challenges', category: 'commercial challenge pricing, list price category' },
  { key: 'computeCostPerHour', label: 'Headless browser compute', unit: 'USD per instance hour', category: 'cloud compute, list price category' },
  { key: 'browsersPerInstance', label: 'Browsers per instance', unit: 'count', category: 'operator estimate' },
  { key: 'fixedUpkeepPerMonth', label: 'Fixed upkeep', unit: 'USD per month', category: 'engineering time estimate' },
  { key: 'articlesPerMonth', label: 'Articles per month', unit: 'count', category: 'operator estimate' },
  { key: 'deliveryCostPerGb', label: 'Edge delivery', unit: 'USD per GB', category: 'CDN delivery, list price category' },
  { key: 'requestCostPerTenThousand', label: 'Edge requests', unit: 'USD per 10,000 requests', category: 'CDN requests, list price category' },
  { key: 'publisherPricePerArticle', label: "Publisher's price P", unit: 'USD per article', category: "publisher's choice (modelled: nothing charges for access)" },
  { key: 'unblockingCostPerThousand', label: 'Turnkey unblocking comparator', unit: 'USD per 1,000 successes', category: 'commercial unblocking, list price category' },
  { key: 'modelCostPerMillionTokens', label: 'Model input tokens', unit: 'USD per 1M tokens', category: 'model list price category' },
];

export const DEFAULT_COST_INPUTS: CostInputs = {
  successRate: '0.8',
  challengeRate: '0.2',
  bandwidthCostPerGb: '5',
  challengeCostPerThousand: '3',
  computeCostPerHour: '0.5',
  browsersPerInstance: '4',
  fixedUpkeepPerMonth: '500',
  articlesPerMonth: '100000',
  deliveryCostPerGb: '0.08',
  requestCostPerTenThousand: '0.01',
  publisherPricePerArticle: '0.002',
  unblockingCostPerThousand: '2.5',
  modelCostPerMillionTokens: '3',
};

/** Fallbacks when no run has measured the figure yet. */
export const FALLBACK_BYTES_PER_ATTEMPT = 1_500_000;
export const FALLBACK_WALL_CLOCK_MS = 3000;
export const FALLBACK_MARKDOWN_BYTES = 12_000;

export interface MeasuredCostInput {
  readonly label: string;
  readonly value: string;
  readonly provenance: 'measured' | 'assumption';
}

export interface CostModelResult {
  readonly scrape: ScrapeBreakdown;
  readonly cleanup: Figure;
  /** Path S per 1,000 obtained articles including the cleanup-compute term. */
  readonly scrapeTotal: Figure;
  readonly api: ApiBreakdown;
  readonly breakEven: Figure;
  readonly saving: SavingVerdict;
  readonly unblocking: Figure;
  /** True when P is below the unblocking comparator. */
  readonly beatsUnblocking: boolean;
  readonly tokenDelta: Figure | null;
  readonly measuredInputs: readonly MeasuredCostInput[];
  /** Modelled cost per article, per step id. */
  readonly stepCostsUsd: { readonly scrape: Record<string, number>; readonly api: Record<string, number> };
}

export class CostInputError extends Error {}

function num(inputs: CostInputs, key: keyof CostInputs): number {
  const raw = inputs[key].trim();
  const value = raw === '' ? NaN : Number(raw);
  if (!Number.isFinite(value)) {
    const def = COST_INPUT_DEFINITIONS.find((d) => d.key === key);
    throw new CostInputError(`${def?.label ?? key} must be a number`);
  }
  return value;
}

const assumption = (value: number, sourceCategory?: string): Labelled => ({
  value,
  provenance: 'assumption',
  sourceCategory,
});

/** Compute the cost model over the operator inputs and, when present, the measured run. */
export function computeCostModel(record: RunRecord | null, inputs: CostInputs): CostModelResult {
  const scrapeAttempt = record?.paths.scrape.attempts[0];
  const apiBytes = record?.paths.api.outcome === 'obtained' ? record.paths.api.attempts[0]?.bytes : undefined;
  const bytesPerAttempt: Labelled =
    scrapeAttempt !== undefined
      ? { value: scrapeAttempt.bytes, provenance: 'measured' }
      : assumption(FALLBACK_BYTES_PER_ATTEMPT);
  const wallClock: Labelled =
    scrapeAttempt !== undefined
      ? { value: scrapeAttempt.wall_clock_ms, provenance: 'measured' }
      : assumption(FALLBACK_WALL_CLOCK_MS);
  const markdownBytes: Labelled =
    apiBytes !== undefined ? { value: apiBytes, provenance: 'measured' } : assumption(FALLBACK_MARKDOWN_BYTES);

  const cleanupSteps = record?.paths.scrape.steps.filter((s) =>
    ['strip_scripts_styles', 'extract_article', 'remove_boilerplate', 'normalize', 'tokenize'].includes(s.id)
  );
  const cleanupMs: Labelled = cleanupSteps
    ? {
        value: cleanupSteps.reduce((sum, s) => sum + (s.wall_clock_ms?.value ?? 0), 0),
        provenance: 'measured',
      }
    : assumption(0);

  const computePerHour = assumption(num(inputs, 'computeCostPerHour'));
  const browsers = assumption(num(inputs, 'browsersPerInstance'));

  const scrape = scrapeCostPerThousand({
    successRate: assumption(num(inputs, 'successRate')),
    bytesPerAttempt,
    challengeRate: assumption(num(inputs, 'challengeRate')),
    wallClockMsPerAttempt: wallClock,
    bandwidthCostPerGb: assumption(num(inputs, 'bandwidthCostPerGb')),
    challengeCostPerThousand: assumption(num(inputs, 'challengeCostPerThousand')),
    computeCostPerHour: computePerHour,
    browsersPerInstance: browsers,
    fixedUpkeepPerMonth: assumption(num(inputs, 'fixedUpkeepPerMonth')),
    articlesPerMonth: assumption(num(inputs, 'articlesPerMonth')),
  });
  const cleanup = cleanupComputeCostPerThousand(cleanupMs, computePerHour, browsers);
  const scrapeTotal: Figure = {
    value: scrape.value + cleanup.value,
    provenance: weakestProvenance([
      { value: scrape.value, provenance: scrape.provenance },
      { value: cleanup.value, provenance: cleanup.provenance },
    ]),
    inputs: [...scrape.inputs, ...cleanup.inputs],
  };

  const price = assumption(num(inputs, 'publisherPricePerArticle'));
  const api = apiCostPerThousand({
    markdownBytes,
    deliveryCostPerGb: assumption(num(inputs, 'deliveryCostPerGb')),
    requestCostPerTenThousand: assumption(num(inputs, 'requestCostPerTenThousand')),
    publisherPricePerArticle: price,
  });
  const breakEven = breakEvenPerArticle(scrapeTotal);
  const saving = savingAtPrice(price, breakEven);
  const unblocking = unblockingPerArticle(assumption(num(inputs, 'unblockingCostPerThousand')));

  let tokenDelta: Figure | null = null;
  if (record && record.paths.scrape.content_tokens !== null && record.paths.api.content_tokens !== null) {
    tokenDelta = tokenDeltaCostPerThousand({
      htmlTokens: { value: record.paths.scrape.content_tokens, provenance: 'measured' },
      markdownTokens: { value: record.paths.api.content_tokens, provenance: 'measured' },
      modelCostPerMillionTokens: assumption(num(inputs, 'modelCostPerMillionTokens')),
    });
  }

  const measuredInputs: MeasuredCostInput[] = [
    {
      label: 'Bytes per scrape attempt',
      value: formatBytes(bytesPerAttempt.value),
      provenance: bytesPerAttempt.provenance === 'measured' ? 'measured' : 'assumption',
    },
    {
      label: 'Scrape time per attempt',
      value: formatMs(wallClock.value),
      provenance: wallClock.provenance === 'measured' ? 'measured' : 'assumption',
    },
    {
      label: 'Cleanup time per article',
      value: formatMs(cleanupMs.value),
      provenance: cleanupMs.provenance === 'measured' ? 'measured' : 'assumption',
    },
    {
      label: 'Markdown bytes',
      value: formatBytes(markdownBytes.value),
      provenance: markdownBytes.provenance === 'measured' ? 'measured' : 'assumption',
    },
  ];

  return {
    scrape,
    cleanup,
    scrapeTotal,
    api,
    breakEven,
    saving,
    unblocking,
    beatsUnblocking: price.value < unblocking.value,
    tokenDelta,
    measuredInputs,
    stepCostsUsd: record
      ? allocateRecordCosts(record, scrape, cleanup, api)
      : { scrape: {}, api: {} },
  };
}

/** Per-step cost allocation (design §4): bandwidth by bytes over the fetch steps, the rest by time. */
function allocateRecordCosts(
  record: RunRecord,
  scrape: ScrapeBreakdown,
  cleanup: Figure,
  api: ApiBreakdown
): { scrape: Record<string, number>; api: Record<string, number> } {
  const steps = record.paths.scrape.steps;
  const perArticle = (v: number) => v / 1000;
  const byBytes: Record<string, number> = {};
  const byTime: Record<string, number> = {};
  for (const s of steps) {
    byTime[s.id] = s.wall_clock_ms?.value ?? 0;
    if (s.id === 'fetch_document' || s.id === 'fetch_subresources') byBytes[s.id] = s.bytes?.value ?? 0;
  }
  const wire = allocateStepCosts(perArticle(scrape.bandwidth + scrape.challenges), byBytes);
  const time = allocateStepCosts(perArticle(scrape.compute + scrape.upkeep + cleanup.value), byTime);
  const scrapeCosts: Record<string, number> = {};
  for (const s of steps) scrapeCosts[s.id] = (wire[s.id] ?? 0) + (time[s.id] ?? 0);

  // Path B: delivery and the publisher price land on the object read; ingest is one-time.
  return {
    scrape: scrapeCosts,
    api: { fetch_markdown: perArticle(api.value) },
  };
}
