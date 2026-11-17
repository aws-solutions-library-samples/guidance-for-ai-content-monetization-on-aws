/**
 * Scrape vs Content API — cost model (spec `scrape-vs-api-comparison`, R3/R4).
 *
 * Pure functions only: no I/O, no React, no AWS SDK. Every figure returned
 * carries the provenance of the inputs it consumed, so a caller cannot render
 * a number whose origin it is unable to name.
 *
 * Vendor names and vendor price strings MUST NOT appear in this file; they are
 * kept out of the published source (R12). Prices arrive here as named,
 * operator-editable inputs carrying a *category* of source.
 */

/** Where a figure comes from. An unlabelled figure is a defect (R3). */
export type Provenance = 'measured' | 'sourced' | 'assumption';

/** Least-trusted first: a result is only as good as its weakest input. */
const PROVENANCE_RANK: Record<Provenance, number> = {
  assumption: 0,
  sourced: 1,
  measured: 2,
};

export interface Labelled {
  readonly value: number;
  readonly provenance: Provenance;
  /** Category of source (e.g. 'commercial unblocking, list price'). Never a vendor name. */
  readonly sourceCategory?: string;
}

/** A computed figure plus the provenance it inherited and the inputs it used. */
export interface Figure {
  readonly value: number;
  readonly provenance: Provenance;
  readonly inputs: readonly string[];
}

const BYTES_PER_GB = 1024 ** 3;
const MS_PER_HOUR = 3_600_000;
const PER_THOUSAND = 1000;

/**
 * A result inherits the provenance of its weakest input: one assumption makes
 * the whole figure an assumption. Conservative on purpose — it is what stops
 * a sourced-looking total from resting on a guess.
 */
export function weakestProvenance(inputs: readonly Labelled[]): Provenance {
  if (inputs.length === 0) {
    throw new Error('cost model: a figure must declare at least one input');
  }
  return inputs.reduce<Provenance>(
    (weakest, input) =>
      PROVENANCE_RANK[input.provenance] < PROVENANCE_RANK[weakest] ? input.provenance : weakest,
    'measured'
  );
}

function requireFinite(name: string, input: Labelled): number {
  if (!Number.isFinite(input.value)) {
    throw new Error(`cost model: ${name} must be a finite number`);
  }
  return input.value;
}

function requireNonNegative(name: string, input: Labelled): number {
  const value = requireFinite(name, input);
  if (value < 0) {
    throw new Error(`cost model: ${name} must not be negative`);
  }
  return value;
}

function requireRate(name: string, input: Labelled): number {
  const value = requireFinite(name, input);
  if (value < 0 || value > 1) {
    throw new Error(`cost model: ${name} must be a rate in [0, 1]`);
  }
  return value;
}

function requirePositive(name: string, input: Labelled): number {
  const value = requireFinite(name, input);
  if (value <= 0) {
    throw new Error(`cost model: ${name} must be greater than zero`);
  }
  return value;
}

/** Path S — obtaining articles by scraping the publisher's protected website. */
export interface ScrapeInputs {
  /** Share of attempts that end with the article in hand, in (0, 1]. */
  readonly successRate: Labelled;
  readonly bytesPerAttempt: Labelled;
  /** Share of attempts that meet a challenge. Priced, never solved (R5). */
  readonly challengeRate: Labelled;
  readonly wallClockMsPerAttempt: Labelled;
  readonly bandwidthCostPerGb: Labelled;
  readonly challengeCostPerThousand: Labelled;
  readonly computeCostPerHour: Labelled;
  readonly browsersPerInstance: Labelled;
  readonly fixedUpkeepPerMonth: Labelled;
  readonly articlesPerMonth: Labelled;
}

export interface ScrapeBreakdown extends Figure {
  readonly attempts: number;
  readonly bandwidth: number;
  readonly challenges: number;
  readonly compute: number;
  readonly upkeep: number;
}

/**
 * Cost of 1,000 successfully obtained articles via Path S.
 *
 * The challenge line prices the challenges *observed*; this model never
 * represents a challenge as satisfied, and nothing here purchases or invokes
 * a bypass service (R5).
 */
export function scrapeCostPerThousand(inputs: ScrapeInputs): ScrapeBreakdown {
  const successRate = requirePositive('successRate', inputs.successRate);
  if (successRate > 1) {
    throw new Error('cost model: successRate must be a rate in [0, 1]');
  }
  const bytesPerAttempt = requireNonNegative('bytesPerAttempt', inputs.bytesPerAttempt);
  const challengeRate = requireRate('challengeRate', inputs.challengeRate);
  const wallClockMs = requireNonNegative('wallClockMsPerAttempt', inputs.wallClockMsPerAttempt);
  const bandwidthPerGb = requireNonNegative('bandwidthCostPerGb', inputs.bandwidthCostPerGb);
  const challengePerThousand = requireNonNegative(
    'challengeCostPerThousand',
    inputs.challengeCostPerThousand
  );
  const computePerHour = requireNonNegative('computeCostPerHour', inputs.computeCostPerHour);
  const browsers = requirePositive('browsersPerInstance', inputs.browsersPerInstance);
  const upkeepPerMonth = requireNonNegative('fixedUpkeepPerMonth', inputs.fixedUpkeepPerMonth);
  const articlesPerMonth = requirePositive('articlesPerMonth', inputs.articlesPerMonth);

  const attempts = PER_THOUSAND / successRate;
  const bandwidth = ((attempts * bytesPerAttempt) / BYTES_PER_GB) * bandwidthPerGb;
  const challenges = ((attempts * challengeRate) / PER_THOUSAND) * challengePerThousand;
  const compute = ((attempts * wallClockMs) / MS_PER_HOUR / browsers) * computePerHour;
  const upkeep = (upkeepPerMonth / articlesPerMonth) * PER_THOUSAND;

  return {
    value: bandwidth + challenges + compute + upkeep,
    provenance: weakestProvenance(Object.values(inputs)),
    inputs: Object.keys(inputs),
    attempts,
    bandwidth,
    challenges,
    compute,
    upkeep,
  };
}

/** Path B — fetching the licensed Markdown through the Content API. */
export interface ApiInputs {
  readonly markdownBytes: Labelled;
  readonly deliveryCostPerGb: Labelled;
  readonly requestCostPerTenThousand: Labelled;
  /** The publisher's price per article. Not ours to set — an input (R4). */
  readonly publisherPricePerArticle: Labelled;
}

export interface ApiBreakdown extends Figure {
  readonly delivery: number;
  readonly licence: number;
}

/** Cost of 1,000 obtained articles via Path B. Dominated by the licence line. */
export function apiCostPerThousand(inputs: ApiInputs): ApiBreakdown {
  const markdownBytes = requireNonNegative('markdownBytes', inputs.markdownBytes);
  const deliveryPerGb = requireNonNegative('deliveryCostPerGb', inputs.deliveryCostPerGb);
  const requestPerTenThousand = requireNonNegative(
    'requestCostPerTenThousand',
    inputs.requestCostPerTenThousand
  );
  const price = requireNonNegative('publisherPricePerArticle', inputs.publisherPricePerArticle);

  const delivery =
    ((PER_THOUSAND * markdownBytes) / BYTES_PER_GB) * deliveryPerGb +
    (PER_THOUSAND / 10_000) * requestPerTenThousand;
  const licence = PER_THOUSAND * price;

  return {
    value: delivery + licence,
    provenance: weakestProvenance(Object.values(inputs)),
    inputs: Object.keys(inputs),
    delivery,
    licence,
  };
}

/**
 * Break-even price per article: the Path S cost of obtaining one article.
 * Above this price, buying stops being cheaper than scraping.
 */
export function breakEvenPerArticle(scrape: Figure): Figure {
  return {
    value: scrape.value / PER_THOUSAND,
    provenance: scrape.provenance,
    inputs: scrape.inputs,
  };
}

/**
 * Price per article of turnkey commercial unblocking, billed per successful
 * request. A rational buyer purchases that instead of building a scraper, so
 * this — not a hand-built scraper — is the price the API has to beat (R3).
 */
export function unblockingPerArticle(costPerThousandSuccesses: Labelled): Figure {
  const cost = requireNonNegative('costPerThousandSuccesses', costPerThousandSuccesses);
  return {
    value: cost / PER_THOUSAND,
    provenance: costPerThousandSuccesses.provenance,
    inputs: ['costPerThousandSuccesses'],
  };
}

export interface SavingVerdict {
  /** 1 - P / P*. Negative when the publisher's price exceeds break-even. */
  readonly ratio: number;
  /** True when buying through the API is the cheaper option. */
  readonly favourable: boolean;
  readonly provenance: Provenance;
}

/**
 * The saving is a function of the publisher's price, never a fixed headline
 * percentage. When the price is at or above break-even this returns the
 * unfavourable verdict rather than clamping it — hiding that branch is what
 * would make the whole comparison untrustworthy (R4).
 */
export function savingAtPrice(pricePerArticle: Labelled, breakEven: Figure): SavingVerdict {
  const price = requireNonNegative('pricePerArticle', pricePerArticle);
  if (!(breakEven.value > 0)) {
    throw new Error('cost model: break-even must be greater than zero to compare a price');
  }
  return {
    ratio: 1 - price / breakEven.value,
    favourable: price < breakEven.value,
    provenance: weakestProvenance([
      pricePerArticle,
      { value: breakEven.value, provenance: breakEven.provenance },
    ]),
  };
}

export interface TokenDeltaInputs {
  readonly htmlTokens: Labelled;
  readonly markdownTokens: Labelled;
  readonly modelCostPerMillionTokens: Labelled;
}

/**
 * Input-token cost avoided per 1,000 articles by consuming Markdown instead
 * of raw HTML. Applies to agent and real-time RAG consumption: most training
 * pipelines strip HTML before the model, so this is not a training-cost claim.
 */
export function tokenDeltaCostPerThousand(inputs: TokenDeltaInputs): Figure {
  const htmlTokens = requireNonNegative('htmlTokens', inputs.htmlTokens);
  const markdownTokens = requireNonNegative('markdownTokens', inputs.markdownTokens);
  const costPerMillion = requireNonNegative(
    'modelCostPerMillionTokens',
    inputs.modelCostPerMillionTokens
  );

  const saved = Math.max(0, htmlTokens - markdownTokens);
  return {
    value: ((saved * PER_THOUSAND) / 1_000_000) * costPerMillion,
    provenance: weakestProvenance(Object.values(inputs)),
    inputs: Object.keys(inputs),
  };
}

/**
 * Names of the inputs still resting on an assumption. The surface lists these
 * next to any conclusion drawn from them; a run of the comparison is what
 * converts them into measurements (R3).
 */
export function assumptionInputs<T extends { readonly [K in keyof T]: Labelled }>(
  inputs: T
): readonly (keyof T & string)[] {
  return (Object.keys(inputs) as (keyof T & string)[]).filter(
    (name) => inputs[name].provenance === 'assumption'
  );
}

/** Human-readable label. Rendering a figure without provenance is refused (R3). */
export function provenanceLabel(figure: { provenance?: Provenance }): string {
  if (!figure.provenance || !(figure.provenance in PROVENANCE_RANK)) {
    throw new Error('cost model: every rendered figure must carry a provenance label');
  }
  return {
    measured: 'Measured',
    sourced: 'Sourced',
    assumption: 'Assumption',
  }[figure.provenance];
}

/**
 * Cleanup-compute term (R4.2, R5.2): cost of the browser-side cleanup time
 * a scraper spends on one article, scaled to 1,000 articles. The time is
 * measured; the price per hour is an operator input, so the result inherits
 * the weaker of the two.
 */
export function cleanupComputeCostPerThousand(
  cleanupMsPerArticle: Labelled,
  computeCostPerHour: Labelled,
  browsersPerInstance: Labelled
): Figure {
  const ms = requireNonNegative('cleanupMsPerArticle', cleanupMsPerArticle);
  const perHour = requireNonNegative('computeCostPerHour', computeCostPerHour);
  const browsers = requirePositive('browsersPerInstance', browsersPerInstance);
  return {
    value: ((PER_THOUSAND * ms) / MS_PER_HOUR / browsers) * perHour,
    provenance: weakestProvenance([cleanupMsPerArticle, computeCostPerHour, browsersPerInstance]),
    inputs: ['cleanupMsPerArticle', 'computeCostPerHour', 'browsersPerInstance'],
  };
}

/**
 * Per-step cost allocation (design §4): splits a per-article total across
 * steps in proportion to a weight (normally measured wall-clock time), so each
 * step row can show a `modelled` cost. Steps with zero weight get zero; when
 * every weight is zero the total is spread evenly so nothing is dropped.
 */
export function allocateStepCosts(
  totalPerArticle: number,
  weights: Readonly<Record<string, number>>
): Record<string, number> {
  if (!Number.isFinite(totalPerArticle) || totalPerArticle < 0) {
    throw new Error('cost model: the total to allocate must be a non-negative number');
  }
  const ids = Object.keys(weights);
  const sum = ids.reduce((acc, id) => {
    const w = weights[id];
    if (!Number.isFinite(w) || w < 0) {
      throw new Error(`cost model: weight for ${id} must be a non-negative number`);
    }
    return acc + w;
  }, 0);
  const out: Record<string, number> = {};
  for (const id of ids) {
    out[id] = sum > 0 ? (totalPerArticle * weights[id]) / sum : totalPerArticle / ids.length;
  }
  return out;
}
