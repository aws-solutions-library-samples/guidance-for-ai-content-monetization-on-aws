/**
 * Run record parser tests (schema 2), spec `scrape-vs-api-comparison` design §2:
 * steps mandatory, provenance on every figure, schema 1 rejected by name,
 * credential-like keys and Bearer-like values rejected, no satisfied challenge.
 */

import { describe, it, expect } from 'vitest';

import {
  latencySummary,
  parseRunRecord,
  parseRunRecordText,
  RunRecordError,
  RUN_SCHEMA_VERSION,
  totalBytes,
  type Attempt,
} from '../comparisonRunService';
import { rawRecord } from './comparisonFixtures';

type Mutable = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

describe('parseRunRecord (schema 2)', () => {
  it('accepts a valid record', () => {
    const rec = parseRunRecord(rawRecord());
    expect(rec.schema_version).toBe(RUN_SCHEMA_VERSION);
    expect(rec.paths.scrape.steps.length).toBeGreaterThan(0);
    expect(rec.paths.api.steps.find((s) => s.id === 'conditional_refetch')?.status).toBe('not_measured');
  });

  it('accepts each outcome of each path', () => {
    for (const outcome of ['obtained', 'refused', 'failed']) {
      const raw = rawRecord() as Mutable;
      raw.paths.scrape.outcome = outcome;
      expect(parseRunRecord(raw).paths.scrape.outcome).toBe(outcome);
    }
    for (const outcome of ['obtained', 'pending', 'precondition_failed']) {
      const raw = rawRecord() as Mutable;
      raw.paths.api.outcome = outcome;
      expect(parseRunRecord(raw).paths.api.outcome).toBe(outcome);
    }
  });

  it('rejects a schema-1 record and names the version', () => {
    const raw = rawRecord() as Mutable;
    raw.schema_version = 1;
    expect(() => parseRunRecord(raw)).toThrow(/schema_version 1 is not supported/);
  });

  it('rejects a record whose steps are missing or empty on either path', () => {
    for (const path of ['scrape', 'api']) {
      const missing = rawRecord() as Mutable;
      delete missing.paths[path].steps;
      expect(() => parseRunRecord(missing)).toThrow(/steps/);
      const empty = rawRecord() as Mutable;
      empty.paths[path].steps = [];
      expect(() => parseRunRecord(empty)).toThrow(/steps/);
    }
  });

  it('rejects a figure without a provenance', () => {
    const raw = rawRecord() as Mutable;
    raw.paths.scrape.steps[0].wall_clock_ms = { value: 5 };
    expect(() => parseRunRecord(raw)).toThrow(/provenance/);
  });

  it('rejects an unknown provenance value', () => {
    const raw = rawRecord() as Mutable;
    raw.paths.scrape.steps[0].bytes = { value: 5, provenance: 'guess' };
    expect(() => parseRunRecord(raw)).toThrow(/provenance/);
  });

  it('accepts all five provenance values', () => {
    for (const provenance of ['measured', 'sourced', 'assumption', 'modelled']) {
      const raw = rawRecord() as Mutable;
      raw.paths.scrape.steps[0].cost_usd = { value: 0.1, provenance };
      expect(parseRunRecord(raw).paths.scrape.steps[0].cost_usd?.provenance).toBe(provenance);
    }
    const raw = rawRecord() as Mutable;
    raw.paths.scrape.steps[0].cost_usd = { value: null, provenance: 'not_measured' };
    expect(parseRunRecord(raw).paths.scrape.steps[0].cost_usd?.value).toBeNull();
  });

  it('rejects a null value on a figure that claims to be measured', () => {
    const raw = rawRecord() as Mutable;
    raw.paths.scrape.steps[0].bytes = { value: null, provenance: 'measured' };
    expect(() => parseRunRecord(raw)).toThrow();
  });

  it('rejects an unknown step id and status', () => {
    const badId = rawRecord() as Mutable;
    badId.paths.scrape.steps[0].id = 'render_with_proxy';
    expect(() => parseRunRecord(badId)).toThrow(/id/);
    const badStatus = rawRecord() as Mutable;
    badStatus.paths.api.steps[0].status = 'bypassed';
    expect(() => parseRunRecord(badStatus)).toThrow(/status/);
  });

  it('rejects a record that claims a satisfied challenge', () => {
    const raw = rawRecord() as Mutable;
    raw.paths.scrape.attempts[0].challenge = 'bot_management_solved';
    expect(() => parseRunRecord(raw)).toThrow(/never a satisfied challenge/);
  });

  it('accepts an observed, unsatisfied challenge', () => {
    const raw = rawRecord() as Mutable;
    raw.paths.scrape.attempts[0].challenge = 'bot_management_observed_not_satisfied';
    expect(parseRunRecord(raw).paths.scrape.attempts[0].challenge).toBe('bot_management_observed_not_satisfied');
  });

  it.each(['authorization', 'cookie', 'x-api-key', 'token', 'secret', 'password'])(
    'rejects a credential-like key: %s',
    (key) => {
      const raw = rawRecord() as Mutable;
      raw.paths.api[key] = 'x';
      expect(() => parseRunRecord(raw)).toThrow(/credential or session value/);
    }
  );

  it('does not mistake the tokens figure or the tokenizer for a credential', () => {
    expect(() => parseRunRecord(rawRecord())).not.toThrow();
  });

  it('rejects a Bearer-like value anywhere', () => {
    const raw = rawRecord() as Mutable;
    raw.paths.api.steps[0].reason = 'sent Bearer eyJhbGciOiJIUzI1NiJ9.abc.def to the gateway';
    expect(() => parseRunRecord(raw)).toThrow(/Bearer/);
  });

  it('rejects bad producer, non-http URL and negative numbers', () => {
    const producer = rawRecord() as Mutable;
    producer.producer = 'browser';
    expect(() => parseRunRecord(producer)).toThrow(/producer/);
    const url = rawRecord() as Mutable;
    url.article.website_url = 'ftp://x';
    expect(() => parseRunRecord(url)).toThrow(/http/);
    const neg = rawRecord() as Mutable;
    neg.paths.scrape.attempts[0].bytes = -1;
    expect(() => parseRunRecord(neg)).toThrow(/non-negative/);
  });

  it('requires cognito_user auth on Path B', () => {
    const raw = rawRecord() as Mutable;
    raw.paths.api.auth = 'partner_key';
    expect(() => parseRunRecord(raw)).toThrow(/auth/);
  });

  it('rejects non-object input', () => {
    expect(() => parseRunRecord(null)).toThrow(RunRecordError);
    expect(() => parseRunRecord([])).toThrow(RunRecordError);
  });
});

describe('parseRunRecordText', () => {
  it('parses JSON text and rejects invalid JSON', () => {
    expect(parseRunRecordText(JSON.stringify(rawRecord())).run_id).toMatch(/-1700000000$/);
    expect(() => parseRunRecordText('{not json')).toThrow(/not valid JSON/);
  });
});

describe('attempt helpers', () => {
  const attempt = (ms: number, bytes = 10): Attempt => ({ n: 1, status: 200, bytes, wall_clock_ms: ms, challenge: null });

  it('computes median and worst, and marks a single attempt', () => {
    expect(latencySummary([])).toBeNull();
    expect(latencySummary([attempt(100)])?.single).toBe(true);
    const s = latencySummary([attempt(300), attempt(100), attempt(200), attempt(400)]);
    expect(s).toMatchObject({ attempts: 4, medianMs: 250, worstMs: 400, single: false });
  });

  it('sums bytes', () => {
    expect(totalBytes([attempt(1, 5), attempt(1, 7)])).toBe(12);
  });
});
