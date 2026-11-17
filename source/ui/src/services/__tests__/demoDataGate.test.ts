/**
 * Demo-data allowMock gate — service inversion tests (#27, Task 2.5)
 *
 * The safety-critical contract: with the demo toggle OFF (`allowMock=false`,
 * the default), a failed or empty real response must surface honestly
 * (rethrow / empty list) — never fabricate mock data. With the toggle ON
 * (`allowMock=true`), the pre-#21 mock fallback is allowed (behind the banner).
 */

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';

// All three services build auth headers via aws-amplify/auth; stub it out.
vi.mock('aws-amplify/auth', () => ({
  getCurrentUser: vi.fn().mockResolvedValue(null),
  fetchAuthSession: vi.fn().mockResolvedValue({ tokens: undefined }),
}));

import { contentService } from '../contentService';
import { partnerService } from '../partnerService';
import { analyticsService } from '../analyticsService';

const okJson = (body: unknown) =>
  Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) } as Response);
const httpError = (status: number) =>
  Promise.resolve({
    ok: false,
    status,
    statusText: `HTTP ${status}`,
    text: () => Promise.resolve(''),
    json: () => Promise.resolve({}),
  } as Response);

describe('allowMock gate (#27)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('contentService.getContent', () => {
    it('OFF: rethrows on failure (no mock fabrication)', async () => {
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(httpError(500)));
      await expect(contentService.getContent()).rejects.toThrow();
      await expect(contentService.getContent(false)).rejects.toThrow();
    });

    it('OFF: returns [] on an empty-but-successful response', async () => {
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(okJson({ items: [] })));
      await expect(contentService.getContent(false)).resolves.toEqual([]);
    });

    it('ON: falls back to mock data on failure', async () => {
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(httpError(500)));
      const items = await contentService.getContent(true);
      expect(items.length).toBeGreaterThan(0);
    });
  });

  describe('contentService.getProcessingStatus', () => {
    it('OFF: rethrows on failure', async () => {
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(httpError(503)));
      await expect(contentService.getProcessingStatus(false)).rejects.toThrow();
    });

    it('ON: falls back to mock jobs on failure', async () => {
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(httpError(503)));
      const jobs = await contentService.getProcessingStatus(true);
      expect(jobs.length).toBeGreaterThan(0);
    });
  });

  describe('partnerService.getRegisteredPartners', () => {
    it('OFF: rethrows on failure', async () => {
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(httpError(500)));
      await expect(partnerService.getRegisteredPartners(undefined, false)).rejects.toThrow();
    });

    it('ON: falls back to mock partners on failure', async () => {
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(httpError(500)));
      const partners = await partnerService.getRegisteredPartners(undefined, true);
      expect(partners.length).toBeGreaterThan(0);
    });
  });

  describe('partnerService.getAccessLogs', () => {
    it('OFF: rethrows on non-ok response', async () => {
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(httpError(500)));
      await expect(partnerService.getAccessLogs({}, false)).rejects.toThrow();
    });

    it('OFF: returns [] when there is no activity data', async () => {
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(okJson({ activity: { datapoints: [] } })));
      await expect(partnerService.getAccessLogs({}, false)).resolves.toEqual([]);
    });

    it('ON: falls back to mock logs on non-ok response', async () => {
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(httpError(500)));
      const logs = await partnerService.getAccessLogs({}, true);
      expect(logs.length).toBeGreaterThan(0);
    });
  });

  describe('analyticsService.getUsageMetrics', () => {
    it('OFF: surfaces the auth error on 401 (no mock)', async () => {
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(httpError(401)));
      await expect(analyticsService.getUsageMetrics(false)).rejects.toThrow();
    });

    it('OFF: rethrows on a generic failure', async () => {
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(httpError(500)));
      await expect(analyticsService.getUsageMetrics(false)).rejects.toThrow();
    });

    it('ON: falls back to mock metrics on 401', async () => {
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(httpError(401)));
      const metrics = await analyticsService.getUsageMetrics(true);
      expect(metrics.total_requests).toBeGreaterThan(0);
    });
  });

  describe('analyticsService.getBotControlRules', () => {
    it('OFF: rethrows on failure', async () => {
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(httpError(500)));
      await expect(analyticsService.getBotControlRules(false)).rejects.toThrow();
    });

    it('ON: falls back to mock rules on failure', async () => {
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(httpError(500)));
      const rules = await analyticsService.getBotControlRules(true);
      expect(rules.length).toBeGreaterThan(0);
    });
  });
});
