/**
 * Cost model tests — spec `scrape-vs-api-comparison`, tasks 2.1/2.3/2.5.
 *
 * These pin each formula, the break-even comparison including the
 * unfavourable-price branch, the provenance-inheritance rule, and the
 * labelling guard that stops an unlabelled figure from ever being rendered.
 */

import { describe, it, expect } from 'vitest';

import {
  allocateStepCosts,
  apiCostPerThousand,
  assumptionInputs,
  breakEvenPerArticle,
  cleanupComputeCostPerThousand,
  provenanceLabel,
  savingAtPrice,
  scrapeCostPerThousand,
  tokenDeltaCostPerThousand,
  unblockingPerArticle,
  weakestProvenance,
  type ApiInputs,
  type Labelled,
  type ScrapeInputs,
} from '../comparisonCostModel';

const measured = (value: number): Labelled => ({ value, provenance: 'measured' });
const sourced = (value: number): Labelled => ({ value, provenance: 'sourced' });
const assumed = (value: number): Labelled => ({ value, provenance: 'assumption' });

/** One attempt per article, 1 GiB per attempt, $1/GB — keeps arithmetic checkable. */
const scrapeInputs = (overrides: Partial<ScrapeInputs> = {}): ScrapeInputs => ({
  successRate: measured(1),
  bytesPerAttempt: measured(1024 ** 3),
  challengeRate: measured(0),
  wallClockMsPerAttempt: measured(0),
  bandwidthCostPerGb: sourced(1),
  challengeCostPerThousand: sourced(0),
  computeCostPerHour: sourced(0),
  browsersPerInstance: sourced(1),
  fixedUpkeepPerMonth: sourced(0),
  articlesPerMonth: sourced(1000),
  ...overrides,
});

const apiInputs = (overrides: Partial<ApiInputs> = {}): ApiInputs => ({
  markdownBytes: measured(0),
  deliveryCostPerGb: sourced(0),
  requestCostPerTenThousand: sourced(0),
  publisherPricePerArticle: sourced(0.005),
  ...overrides,
});

describe('provenance inheritance', () => {
  it('takes the weakest input: one assumption makes the figure an assumption', () => {
    expect(weakestProvenance([measured(1), sourced(1), assumed(1)])).toBe('assumption');
  });

  it('is sourced when inputs mix measured and sourced', () => {
    expect(weakestProvenance([measured(1), sourced(1)])).toBe('sourced');
  });

  it('is measured only when every input is measured', () => {
    expect(weakestProvenance([measured(1), measured(2)])).toBe('measured');
  });

  it('refuses a figure that declares no input', () => {
    expect(() => weakestProvenance([])).toThrow(/at least one input/);
  });

  it('propagates through the scrape total', () => {
    expect(scrapeCostPerThousand(scrapeInputs()).provenance).toBe('sourced');
    expect(
      scrapeCostPerThousand(scrapeInputs({ successRate: assumed(0.65) })).provenance
    ).toBe('assumption');
  });
});

describe('scrapeCostPerThousand', () => {
  it('prices bandwidth over the attempts the success rate implies', () => {
    // 1,000 attempts x 1 GiB x $1/GB
    expect(scrapeCostPerThousand(scrapeInputs()).value).toBeCloseTo(1000, 6);
  });

  it('scales attempts inversely with the success rate', () => {
    const half = scrapeCostPerThousand(scrapeInputs({ successRate: measured(0.5) }));
    expect(half.attempts).toBeCloseTo(2000, 6);
    expect(half.value).toBeCloseTo(2000, 6);
  });

  it('prices observed challenges per thousand attempts', () => {
    const breakdown = scrapeCostPerThousand(
      scrapeInputs({
        bytesPerAttempt: measured(0),
        bandwidthCostPerGb: sourced(0),
        challengeRate: measured(0.4),
        challengeCostPerThousand: sourced(2),
      })
    );
    // 1,000 attempts x 40% = 400 challenges, at $2 per 1,000
    expect(breakdown.challenges).toBeCloseTo(0.8, 6);
  });

  it('divides headless compute across browsers per instance', () => {
    const breakdown = scrapeCostPerThousand(
      scrapeInputs({
        bytesPerAttempt: measured(0),
        bandwidthCostPerGb: sourced(0),
        wallClockMsPerAttempt: measured(3_600_000),
        computeCostPerHour: sourced(1),
        browsersPerInstance: sourced(4),
      })
    );
    // 1,000 attempt-hours / 4 browsers x $1
    expect(breakdown.compute).toBeCloseTo(250, 6);
  });

  it('amortises fixed upkeep over monthly volume', () => {
    const breakdown = scrapeCostPerThousand(
      scrapeInputs({
        bytesPerAttempt: measured(0),
        bandwidthCostPerGb: sourced(0),
        fixedUpkeepPerMonth: assumed(20_000),
        articlesPerMonth: assumed(1_000_000),
      })
    );
    // $20,000 / 1M articles x 1,000
    expect(breakdown.upkeep).toBeCloseTo(20, 6);
  });

  it('rejects a success rate outside (0, 1]', () => {
    expect(() => scrapeCostPerThousand(scrapeInputs({ successRate: measured(0) }))).toThrow();
    expect(() => scrapeCostPerThousand(scrapeInputs({ successRate: measured(1.5) }))).toThrow();
  });

  it('rejects a challenge rate outside [0, 1]', () => {
    expect(() =>
      scrapeCostPerThousand(scrapeInputs({ challengeRate: measured(1.2) }))
    ).toThrow(/rate in \[0, 1\]/);
  });

  it('rejects non-finite and negative inputs', () => {
    expect(() =>
      scrapeCostPerThousand(scrapeInputs({ bytesPerAttempt: measured(Number.NaN) }))
    ).toThrow(/finite/);
    expect(() =>
      scrapeCostPerThousand(scrapeInputs({ bandwidthCostPerGb: sourced(-1) }))
    ).toThrow(/negative/);
  });
});

describe('apiCostPerThousand', () => {
  it('is dominated by the publisher licence line', () => {
    const breakdown = apiCostPerThousand(apiInputs());
    expect(breakdown.licence).toBeCloseTo(5, 6);
    expect(breakdown.delivery).toBeCloseTo(0, 6);
    expect(breakdown.value).toBeCloseTo(5, 6);
  });

  it('prices edge delivery of the Markdown object', () => {
    const breakdown = apiCostPerThousand(
      apiInputs({
        markdownBytes: measured(1024 ** 3),
        deliveryCostPerGb: sourced(1),
        requestCostPerTenThousand: sourced(1),
        publisherPricePerArticle: sourced(0),
      })
    );
    // 1,000 GiB x $1 + (1,000/10,000) x $1
    expect(breakdown.delivery).toBeCloseTo(1000.1, 6);
  });

  it('accepts a zero price without treating it as missing', () => {
    expect(apiCostPerThousand(apiInputs({ publisherPricePerArticle: sourced(0) })).value).toBe(0);
  });
});

describe('break-even and saving', () => {
  it('break-even is the scrape cost of a single article', () => {
    const breakEven = breakEvenPerArticle(scrapeCostPerThousand(scrapeInputs()));
    expect(breakEven.value).toBeCloseTo(1, 6);
  });

  it('reports the saving as a function of the price', () => {
    const breakEven = breakEvenPerArticle(scrapeCostPerThousand(scrapeInputs()));
    const verdict = savingAtPrice(sourced(0.25), breakEven);
    expect(verdict.ratio).toBeCloseTo(0.75, 6);
    expect(verdict.favourable).toBe(true);
  });

  it('returns an unfavourable verdict when the price exceeds break-even', () => {
    const breakEven = breakEvenPerArticle(scrapeCostPerThousand(scrapeInputs()));
    const verdict = savingAtPrice(sourced(1.5), breakEven);
    expect(verdict.favourable).toBe(false);
    expect(verdict.ratio).toBeLessThan(0);
  });

  it('does not clamp an unfavourable saving to zero', () => {
    const breakEven = breakEvenPerArticle(scrapeCostPerThousand(scrapeInputs()));
    expect(savingAtPrice(sourced(2), breakEven).ratio).toBeCloseTo(-1, 6);
  });

  it('treats a price exactly at break-even as not favourable', () => {
    const breakEven = breakEvenPerArticle(scrapeCostPerThousand(scrapeInputs()));
    const verdict = savingAtPrice(sourced(1), breakEven);
    expect(verdict.ratio).toBeCloseTo(0, 6);
    expect(verdict.favourable).toBe(false);
  });

  it('refuses to compare against a non-positive break-even', () => {
    expect(() =>
      savingAtPrice(sourced(0.01), { value: 0, provenance: 'measured', inputs: ['x'] })
    ).toThrow(/greater than zero/);
  });

  it('inherits the weaker of the price and the break-even provenance', () => {
    const breakEven = breakEvenPerArticle(
      scrapeCostPerThousand(scrapeInputs({ successRate: assumed(0.65) }))
    );
    expect(savingAtPrice(sourced(0.01), breakEven).provenance).toBe('assumption');
  });
});

describe('unblockingPerArticle', () => {
  it('divides the price per 1,000 successes down to one article', () => {
    const figure = unblockingPerArticle(sourced(1.5));
    expect(figure.value).toBeCloseTo(0.0015, 9);
    expect(figure.provenance).toBe('sourced');
  });

  it('rejects a negative price', () => {
    expect(() => unblockingPerArticle(sourced(-1))).toThrow(/negative/);
  });
});

describe('tokenDeltaCostPerThousand', () => {
  it('prices the avoided input tokens per 1,000 articles', () => {
    const figure = tokenDeltaCostPerThousand({
      htmlTokens: sourced(16_180),
      markdownTokens: sourced(3_150),
      modelCostPerMillionTokens: assumed(1),
    });
    // 13,030 tokens x 1,000 articles = 13.03M tokens at $1/M
    expect(figure.value).toBeCloseTo(13.03, 6);
    expect(figure.provenance).toBe('assumption');
  });

  it('never returns a negative delta when Markdown is the larger payload', () => {
    const figure = tokenDeltaCostPerThousand({
      htmlTokens: measured(100),
      markdownTokens: measured(500),
      modelCostPerMillionTokens: sourced(1),
    });
    expect(figure.value).toBe(0);
  });
});

describe('assumption reporting', () => {
  it('names only the inputs still resting on an assumption', () => {
    const names = assumptionInputs(
      scrapeInputs({ successRate: assumed(0.65), challengeRate: assumed(0.4) })
    );
    expect(names).toEqual(['successRate', 'challengeRate']);
  });

  it('returns nothing once every input is measured or sourced', () => {
    expect(assumptionInputs(scrapeInputs())).toEqual([]);
  });
});

describe('labelling guard', () => {
  it('labels each provenance for display', () => {
    expect(provenanceLabel({ provenance: 'measured' })).toBe('Measured');
    expect(provenanceLabel({ provenance: 'sourced' })).toBe('Sourced');
    expect(provenanceLabel({ provenance: 'assumption' })).toBe('Assumption');
  });

  it('refuses to label a figure that carries no provenance', () => {
    expect(() => provenanceLabel({})).toThrow(/must carry a provenance label/);
    expect(() =>
      provenanceLabel({ provenance: 'guessed' as unknown as 'measured' })
    ).toThrow(/must carry a provenance label/);
  });
});

describe('cleanupComputeCostPerThousand', () => {
  it('scales measured cleanup time to 1,000 articles over browsers per instance', () => {
    // 3.6 s per article, 1,000 articles = 1 hour; 2 browsers; $10/h -> $5.
    const fig = cleanupComputeCostPerThousand(measured(3600), sourced(10), sourced(2));
    expect(fig.value).toBeCloseTo(5, 10);
    expect(fig.provenance).toBe('sourced');
  });

  it('rejects a non-positive browser count', () => {
    expect(() => cleanupComputeCostPerThousand(measured(1), sourced(1), sourced(0))).toThrow();
  });
});

describe('allocateStepCosts', () => {
  it('splits in proportion to the weights and conserves the total', () => {
    const out = allocateStepCosts(10, { a: 1, b: 3, c: 0 });
    expect(out.a).toBeCloseTo(2.5, 10);
    expect(out.b).toBeCloseTo(7.5, 10);
    expect(out.c).toBe(0);
  });

  it('spreads evenly when every weight is zero', () => {
    expect(allocateStepCosts(9, { a: 0, b: 0, c: 0 })).toEqual({ a: 3, b: 3, c: 3 });
  });

  it('rejects negative totals and weights', () => {
    expect(() => allocateStepCosts(-1, { a: 1 })).toThrow();
    expect(() => allocateStepCosts(1, { a: -1 })).toThrow();
  });
});
