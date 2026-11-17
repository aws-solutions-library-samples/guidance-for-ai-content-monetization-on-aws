/**
 * licenseService — Demo_Toggle gate + canonical-label tests (task 11.5)
 *
 * The page tests in `src/pages/__tests__/LicensesPage.test.tsx` cover the CRUD
 * interactions and the `allowMock={demoDataEnabled}` pass-through. This file
 * pins the half the page cannot: the SERVICE-side `allowMock = false` DEFAULT.
 * A caller that omits the argument must get real-data-only — a failure
 * rethrows and an empty-but-successful response returns an empty value, never
 * mock data (#27, Req 11.5).
 *
 * It also pins the canonical CoMP label mirror (Req 11.3) against the
 * authoritative table in `source/lambda/services/comp_query_service.py`.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// Auth headers are built via aws-amplify/auth; stub it out.
vi.mock('aws-amplify/auth', () => ({
  getCurrentUser: vi.fn().mockResolvedValue(null),
  fetchAuthSession: vi.fn().mockResolvedValue({ tokens: undefined }),
}));

import {
  COMP_FUNCTION_CODES,
  compFunctionLabel,
  LICENSE_STATUSES,
  licenseService,
} from '../licenseService';

const okJson = (body: unknown) =>
  Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) } as Response);
const noContent = () =>
  Promise.resolve({
    ok: true,
    status: 204,
    json: () => Promise.reject(new Error('no body')),
  } as unknown as Response);
const httpError = (status: number, body: unknown = {}) =>
  Promise.resolve({
    ok: false,
    status,
    statusText: `HTTP ${status}`,
    json: () => Promise.resolve(body),
  } as Response);

describe('licenseService', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('allowMock defaults to false (#27, Req 11.5)', () => {
    it('listLicenses: OMITTED argument rethrows on failure (no mock fabrication)', async () => {
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(httpError(500)));
      await expect(licenseService.listLicenses()).rejects.toThrow();
      await expect(licenseService.listLicenses('active')).rejects.toThrow();
      await expect(licenseService.listLicenses('active', false)).rejects.toThrow();
    });

    it('listLicenses: OMITTED argument returns [] on an empty-but-successful response', async () => {
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(okJson({ licenses: [] })));
      await expect(licenseService.listLicenses()).resolves.toEqual({
        licenses: [],
        total_count: undefined,
        next_token: undefined,
      });
    });

    it('listLicenses: ON falls back to mock licenses on failure', async () => {
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(httpError(500)));
      const result = await licenseService.listLicenses(undefined, true);
      expect(result.licenses.length).toBeGreaterThan(0);
    });

    it('getLicenseVersions: OMITTED argument rethrows on failure', async () => {
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(httpError(500)));
      await expect(licenseService.getLicenseVersions('lic_x')).rejects.toThrow();
    });

    it('getLicenseVersions: OMITTED argument returns an empty history on an empty response', async () => {
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(okJson({ license_id: 'lic_x' })));
      await expect(licenseService.getLicenseVersions('lic_x')).resolves.toEqual({
        license_id: 'lic_x',
        current_version: undefined,
        versions: [],
      });
    });

    it('getLicenseVersions: ON falls back to a mock history on failure', async () => {
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(httpError(500)));
      const history = await licenseService.getLicenseVersions('lic_x', true);
      expect(history.versions.length).toBeGreaterThan(0);
    });

    it('getLicense: OMITTED argument rethrows on failure but maps 404 to null', async () => {
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(httpError(500)));
      await expect(licenseService.getLicense('lic_x')).rejects.toThrow();

      vi.stubGlobal('fetch', vi.fn().mockReturnValue(httpError(404)));
      await expect(licenseService.getLicense('lic_x')).resolves.toBeNull();
    });

    it('mutations have NO mock path — they always surface failures', async () => {
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(httpError(500)));
      await expect(
        licenseService.createLicense({
          title: 't',
          allowed_uses: [3],
          attribution_required: false,
        }),
      ).rejects.toThrow();
      await expect(licenseService.updateLicense('lic_x', { title: 't' })).rejects.toThrow();
      await expect(licenseService.revokeLicense('lic_x')).rejects.toThrow();
      await expect(licenseService.renewLicense('lic_x')).rejects.toThrow();
      await expect(
        licenseService.validateLicense('lic_x', { agent_id: 'a', intended_use: 'inference' }),
      ).rejects.toThrow();
    });
  });

  describe('contract shapes', () => {
    it('revokeLicense accepts a 204 empty body without parsing it', async () => {
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(noContent()));
      await expect(licenseService.revokeLicense('lic_x')).resolves.toBeUndefined();
    });

    it('renewLicense surfaces the 409 LICENSE_ALREADY_ACTIVE code in the error', async () => {
      vi.stubGlobal(
        'fetch',
        vi.fn().mockReturnValue(
          httpError(409, {
            error_code: 'LICENSE_ALREADY_ACTIVE',
            message: 'License lic_x is already active; nothing to renew',
          }),
        ),
      );
      await expect(licenseService.renewLicense('lic_x')).rejects.toThrow(
        /LICENSE_ALREADY_ACTIVE/,
      );
    });

    it('listLicenses passes the status filter through as a query parameter', async () => {
      const fetchMock = vi.fn().mockReturnValue(okJson({ licenses: [] }));
      vi.stubGlobal('fetch', fetchMock);
      await licenseService.listLicenses('revoked');
      expect(String(fetchMock.mock.calls[0][0])).toContain('/licenses?status=revoked');
    });

    it('the status filter offers revoked (Req 11.2)', () => {
      expect(LICENSE_STATUSES).toContain('revoked');
    });
  });

  describe('canonical CoMP labels (Req 11.3)', () => {
    it('mirrors the authoritative comp_query_service table exactly', () => {
      expect(COMP_FUNCTION_CODES).toEqual({
        0: 'all',
        1: 'ai-all',
        2: 'ai-train',
        3: 'ai-input',
        4: 'ai-index',
        5: 'search',
      });
    });

    it('labels every canonical code and never the retired license_service labels', () => {
      expect([0, 1, 2, 3, 4, 5].map((c) => compFunctionLabel(c))).toEqual([
        'all',
        'ai-all',
        'ai-train',
        'ai-input',
        'ai-index',
        'search',
      ]);
      const retired = ['Search/Discovery', 'Evaluation', 'Analysis'];
      for (const code of [0, 1, 2, 3, 4, 5]) {
        expect(retired).not.toContain(compFunctionLabel(code));
      }
    });

    it('renders a stringified code (DynamoDB numeric serialization) canonically', () => {
      expect(compFunctionLabel('5')).toBe('search');
    });

    it('renders an unrecognized code honestly rather than guessing', () => {
      expect(compFunctionLabel(9)).toBe('Unknown (9)');
      expect(compFunctionLabel('nope')).toBe('Unknown (nope)');
    });
  });
});
