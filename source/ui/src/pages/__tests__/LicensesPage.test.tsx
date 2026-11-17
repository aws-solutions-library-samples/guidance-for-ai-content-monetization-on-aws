/**
 * LicensesPage — Vitest UI tests (task 11.5)
 *
 * Validates the publisher license-management surface for #3:
 *   - Req 11.2 — CRUD interactions against MOCKED API calls: list with a
 *     `status` filter including `revoked`, create, view, update, revoke, renew,
 *     validate, and version history.
 *   - Req 11.3 — `allowed_uses` renders with canonical CoMP labels
 *     (`all`, `ai-all`, `ai-train`, `ai-input`, `ai-index`, `search`) and never
 *     the retired `license_service.py` labels; the attribution-format authoring
 *     control is present.
 *   - Req 11.5 — Demo_Toggle: `allowMock` defaults to `false`, so with demo OFF
 *     a failure surfaces an error and an empty result renders an empty state —
 *     never mock data. The page passes `allowMock={demoDataEnabled}` through.
 *   - Req 11.6 — single concern: no partner-identity / entitlement / analytics
 *     surfaces are mixed in.
 *
 * `licenseService` and `FeatureFlagContext` are mocked — no real API is hit.
 * The service module's canonical label helpers/constants are re-exported
 * unchanged so the real label mapping is exercised.
 *
 * _Requirements: 11.2, 11.3, 11.5_
 */

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// --- Mocks -----------------------------------------------------------------

vi.mock('../../services/licenseService', async () => {
  const actual = await vi.importActual<typeof import('../../services/licenseService')>(
    '../../services/licenseService',
  );
  return {
    ...actual,
    licenseService: {
      listLicenses: vi.fn(),
      getLicense: vi.fn(),
      createLicense: vi.fn(),
      updateLicense: vi.fn(),
      revokeLicense: vi.fn(),
      renewLicense: vi.fn(),
      getLicenseVersions: vi.fn(),
      validateLicense: vi.fn(),
    },
  };
});

const mockUseFeatureFlags = vi.fn();
vi.mock('../../contexts/FeatureFlagContext', () => ({
  useFeatureFlags: () => mockUseFeatureFlags(),
}));

import LicensesPage from '../LicensesPage';
import {
  licenseService,
  LicenseDocument,
  LicenseVersionHistory,
} from '../../services/licenseService';

// --- Fixtures --------------------------------------------------------------

const activeLicense: LicenseDocument = {
  license_id: 'lic_inference_v1',
  license_url: 'https://api.example.com/licenses/lic_inference_v1',
  version: 1,
  status: 'active',
  title: 'Standard inference license',
  description: 'Permits inference/RAG.',
  // Canonical CoMP codes: 3=ai-input, 5=search.
  allowed_uses: [3, 5],
  attribution_required: true,
  attribution_format: 'Source: {title} by {author}. {url}',
};

const revokedLicense: LicenseDocument = {
  license_id: 'lic_retired_v2',
  version: 2,
  status: 'revoked',
  title: 'Retired training license',
  allowed_uses: [2],
  attribution_required: false,
};

const versionHistory: LicenseVersionHistory = {
  license_id: 'lic_inference_v1',
  current_version: '1',
  versions: [
    {
      version_number: 1,
      version: 1,
      updated_at: '2025-01-15T10:30:00Z',
      updated_by: 'publisher-admin',
      change_summary: 'Initial version',
    },
  ],
};

const primeHappyPath = (licenses: LicenseDocument[] = [activeLicense]) => {
  vi.mocked(licenseService.listLicenses).mockResolvedValue({
    licenses,
    total_count: licenses.length,
  });
  vi.mocked(licenseService.getLicenseVersions).mockResolvedValue(versionHistory);
};

const setDemo = (enabled: boolean) => {
  const flags = { enableMockData: enabled } as Record<string, boolean>;
  mockUseFeatureFlags.mockReturnValue({
    flags,
    isDemoMode: enabled,
    isFeatureEnabled: (f: string) => Boolean(flags[f]),
    demoDataEnabled: enabled,
    setDemoData: vi.fn(),
  });
};

// --- Tests -----------------------------------------------------------------

describe('LicensesPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setDemo(false); // demo OFF is the fail-safe default (#27)
  });

  describe('list + status filter (Req 11.2)', () => {
    it('lists licenses with the default active status filter', async () => {
      primeHappyPath();
      render(<LicensesPage />);

      expect(await screen.findByText('lic_inference_v1')).toBeInTheDocument();
      expect(screen.getByText('Standard inference license')).toBeInTheDocument();
      expect(licenseService.listLicenses).toHaveBeenCalledWith('active', false);
    });

    it('offers revoked in the status filter and re-queries with it', async () => {
      primeHappyPath();
      const user = userEvent.setup({ delay: null });
      render(<LicensesPage />);

      await screen.findByText('lic_inference_v1');

      // Open the status filter and pick `revoked`.
      await user.click(screen.getByRole('button', { name: /Filter licenses by status/i }));
      await user.click(await screen.findByText('revoked'));

      await waitFor(() => {
        expect(licenseService.listLicenses).toHaveBeenCalledWith('revoked', false);
      });
    });

    it('renders an empty state (never mock data) when the result is empty', async () => {
      vi.mocked(licenseService.listLicenses).mockResolvedValue({ licenses: [], total_count: 0 });
      render(<LicensesPage />);

      expect(await screen.findByTestId('licenses-empty')).toBeInTheDocument();
      expect(screen.getByText('No licenses')).toBeInTheDocument();
      // No demo/mock license leaked into an empty-but-successful read.
      expect(screen.queryByText(/lic_demo_/)).toBeNull();
    });
  });

  describe('canonical CoMP labels (Req 11.3)', () => {
    it('renders allowed_uses with canonical labels, not the retired ones', async () => {
      primeHappyPath([
        { ...activeLicense, allowed_uses: [0, 1, 2, 3, 4, 5] },
      ]);
      render(<LicensesPage />);

      await screen.findByText('lic_inference_v1');

      for (const label of ['all', 'ai-all', 'ai-train', 'ai-input', 'ai-index', 'search']) {
        expect(screen.getAllByText(label).length).toBeGreaterThan(0);
      }

      // The retired license_service.py labels must never appear.
      expect(screen.queryByText('Search/Discovery')).toBeNull();
      expect(screen.queryByText('Evaluation')).toBeNull();
      expect(screen.queryByText('Analysis')).toBeNull();
    });

    it('offers the canonical codes in the allowed-uses authoring control', async () => {
      primeHappyPath();
      const user = userEvent.setup({ delay: null });
      render(<LicensesPage />);

      await user.click(await screen.findByTestId('create-license'));
      await user.click(screen.getByRole('button', { name: /Allowed uses/i }));

      // Each canonical slug is selectable as a dropdown option.
      for (const label of ['all', 'ai-all', 'ai-train', 'ai-input', 'ai-index', 'search']) {
        expect(
          await screen.findByRole('option', { name: new RegExp(`^${label}\\b`) }),
        ).toBeInTheDocument();
      }
    });

    it('renders an unrecognized code honestly rather than guessing a label', async () => {
      primeHappyPath([{ ...activeLicense, allowed_uses: [9] }]);
      render(<LicensesPage />);

      expect(await screen.findByText('Unknown (9)')).toBeInTheDocument();
    });
  });

  describe('create (Req 11.2)', () => {
    it('create → createLicense with the authored contract payload', async () => {
      primeHappyPath();
      vi.mocked(licenseService.createLicense).mockResolvedValue({
        ...activeLicense,
        license_id: 'lic_new',
      });
      const user = userEvent.setup({ delay: null });
      render(<LicensesPage />);

      await user.click(await screen.findByTestId('create-license'));

      await user.type(
        screen.getByTestId('field-title').querySelector('input')!,
        'New license',
      );
      await user.click(screen.getByRole('button', { name: /Allowed uses/i }));
      await user.click(await screen.findByRole('option', { name: /^ai-input\b/ }));
      await user.click(screen.getByTestId('save-license'));

      await waitFor(() => {
        expect(licenseService.createLicense).toHaveBeenCalledTimes(1);
      });
      expect(vi.mocked(licenseService.createLicense).mock.calls[0][0]).toMatchObject({
        title: 'New license',
        allowed_uses: [3],
        attribution_required: false,
      });
    });

    it('authors the attribution format and sends it on the contract field', async () => {
      primeHappyPath();
      vi.mocked(licenseService.createLicense).mockResolvedValue(activeLicense);
      const user = userEvent.setup({ delay: null });
      render(<LicensesPage />);

      await user.click(await screen.findByTestId('create-license'));

      // The control exists. The deferred-§7 honesty notice that used to sit
      // under it was removed at the owner's request for demo presentation, so
      // it is no longer asserted here — the §7 contract-vs-runtime gap itself
      // is unchanged (see the block comment in LicensesPage.tsx).
      expect(screen.getByTestId('field-attribution-format')).toBeInTheDocument();

      await user.type(
        screen.getByTestId('field-title').querySelector('input')!,
        'Attributed license',
      );
      await user.click(screen.getByRole('button', { name: /Allowed uses/i }));
      await user.click(await screen.findByRole('option', { name: /^search\b/ }));
      // `{{` is userEvent's escape for a literal `{` — the authored template
      // is `Source: {title}`.
      await user.type(
        screen.getByTestId('field-attribution-format').querySelector('input')!,
        'Source: {{title}',
      );
      await user.click(screen.getByTestId('save-license'));

      await waitFor(() => {
        expect(licenseService.createLicense).toHaveBeenCalledTimes(1);
      });
      expect(vi.mocked(licenseService.createLicense).mock.calls[0][0]).toMatchObject({
        attribution_format: 'Source: {title}',
      });
    });

    it('validates the form before calling the API', async () => {
      primeHappyPath();
      const user = userEvent.setup({ delay: null });
      render(<LicensesPage />);

      await user.click(await screen.findByTestId('create-license'));
      await user.click(screen.getByTestId('save-license'));

      expect(await screen.findByText('Title is required.')).toBeInTheDocument();
      expect(licenseService.createLicense).not.toHaveBeenCalled();
    });
  });

  describe('view + version history (Req 11.2)', () => {
    it('view → renders detail with canonical labels and loads version history', async () => {
      primeHappyPath();
      const user = userEvent.setup({ delay: null });
      render(<LicensesPage />);

      await user.click(await screen.findByTestId('view-lic_inference_v1'));

      await waitFor(() => {
        expect(licenseService.getLicenseVersions).toHaveBeenCalledWith('lic_inference_v1', false);
      });
      expect(await screen.findByTestId('detail-allowed-uses')).toHaveTextContent('ai-input, search');
      expect(await screen.findByText('Initial version')).toBeInTheDocument();
    });

    it('version-history failure surfaces an error (demo OFF)', async () => {
      primeHappyPath();
      vi.mocked(licenseService.getLicenseVersions).mockRejectedValue(new Error('history down'));
      const user = userEvent.setup({ delay: null });
      render(<LicensesPage />);

      await user.click(await screen.findByTestId('view-lic_inference_v1'));

      expect(await screen.findByText(/Unable to load version history/)).toBeInTheDocument();
      expect(screen.getByText('history down')).toBeInTheDocument();
    });
  });

  describe('update / revoke / renew (Req 11.2)', () => {
    it('edit → updateLicense for the selected license', async () => {
      primeHappyPath();
      vi.mocked(licenseService.updateLicense).mockResolvedValue(activeLicense);
      const user = userEvent.setup({ delay: null });
      render(<LicensesPage />);

      await user.click(await screen.findByTestId('edit-lic_inference_v1'));
      await user.click(screen.getByTestId('save-license'));

      await waitFor(() => {
        expect(licenseService.updateLicense).toHaveBeenCalledTimes(1);
      });
      expect(vi.mocked(licenseService.updateLicense).mock.calls[0][0]).toBe('lic_inference_v1');
      // The edit form is pre-filled from the existing document.
      expect(vi.mocked(licenseService.updateLicense).mock.calls[0][1]).toMatchObject({
        title: 'Standard inference license',
        allowed_uses: [3, 5],
        attribution_required: true,
      });
      expect(licenseService.createLicense).not.toHaveBeenCalled();
    });

    it('revoke → revokeLicense (soft revoke) and reloads', async () => {
      primeHappyPath();
      vi.mocked(licenseService.revokeLicense).mockResolvedValue(undefined);
      const user = userEvent.setup({ delay: null });
      render(<LicensesPage />);

      await user.click(await screen.findByTestId('revoke-lic_inference_v1'));

      await waitFor(() => {
        expect(licenseService.revokeLicense).toHaveBeenCalledWith('lic_inference_v1');
      });
      expect(await screen.findByText(/Version history is preserved/)).toBeInTheDocument();
      // Reload after mutation.
      expect(vi.mocked(licenseService.listLicenses).mock.calls.length).toBeGreaterThan(1);
    });

    it('a revoked license offers Renew instead of Revoke', async () => {
      primeHappyPath([revokedLicense]);
      vi.mocked(licenseService.renewLicense).mockResolvedValue({
        ...revokedLicense,
        status: 'active',
        version: 3,
      });
      const user = userEvent.setup({ delay: null });
      render(<LicensesPage />);

      const renewBtn = await screen.findByTestId('renew-lic_retired_v2');
      expect(screen.queryByTestId('revoke-lic_retired_v2')).toBeNull();

      await user.click(renewBtn);

      await waitFor(() => {
        expect(licenseService.renewLicense).toHaveBeenCalledWith('lic_retired_v2');
      });
      expect(await screen.findByText(/renewed as a new active version/)).toBeInTheDocument();
    });

    it('renew surfaces the 409 LICENSE_ALREADY_ACTIVE conflict', async () => {
      primeHappyPath([revokedLicense]);
      vi.mocked(licenseService.renewLicense).mockRejectedValue(
        new Error('License is already active (LICENSE_ALREADY_ACTIVE)'),
      );
      const user = userEvent.setup({ delay: null });
      render(<LicensesPage />);

      await user.click(await screen.findByTestId('renew-lic_retired_v2'));

      expect(await screen.findByText(/LICENSE_ALREADY_ACTIVE/)).toBeInTheDocument();
    });
  });

  describe('validate (Req 11.2)', () => {
    it('validate → validateLicense and surfaces the permitted result', async () => {
      primeHappyPath();
      vi.mocked(licenseService.validateLicense).mockResolvedValue({
        valid: true,
        license_id: 'lic_inference_v1',
        intended_use: 'training',
      });
      const user = userEvent.setup({ delay: null });
      render(<LicensesPage />);

      await user.click(await screen.findByTestId('validate-lic_inference_v1'));
      await user.type(
        screen.getByTestId('field-agent-id').querySelector('input')!,
        'agent_gptbot_openai_001',
      );
      await user.click(screen.getByTestId('run-validation'));

      await waitFor(() => {
        expect(licenseService.validateLicense).toHaveBeenCalledWith('lic_inference_v1', {
          agent_id: 'agent_gptbot_openai_001',
          intended_use: 'training',
        });
      });
      expect(await screen.findByText(/Permitted by this license/)).toBeInTheDocument();
    });

    it('surfaces a denial with its violations (a 200 valid:false, not an error)', async () => {
      primeHappyPath();
      vi.mocked(licenseService.validateLicense).mockResolvedValue({
        valid: false,
        license_id: 'lic_inference_v1',
        violations: [
          { rule: 'training_prohibited', description: 'This license does not permit training use' },
        ],
      });
      const user = userEvent.setup({ delay: null });
      render(<LicensesPage />);

      await user.click(await screen.findByTestId('validate-lic_inference_v1'));
      await user.type(
        screen.getByTestId('field-agent-id').querySelector('input')!,
        'agent_x',
      );
      await user.click(screen.getByTestId('run-validation'));

      expect(await screen.findByText(/Not permitted/)).toBeInTheDocument();
      expect(
        await screen.findByText(/This license does not permit training use/),
      ).toBeInTheDocument();
    });
  });

  describe('Demo_Toggle / mock-data honesty (Req 11.5, #27)', () => {
    it('OFF (default): passes allowMock=false to every read getter', async () => {
      primeHappyPath();
      const user = userEvent.setup({ delay: null });
      render(<LicensesPage />);

      await waitFor(() => {
        expect(licenseService.listLicenses).toHaveBeenCalledWith('active', false);
      });

      await user.click(await screen.findByTestId('view-lic_inference_v1'));
      await waitFor(() => {
        expect(licenseService.getLicenseVersions).toHaveBeenCalledWith('lic_inference_v1', false);
      });
    });

    it('ON: passes allowMock=true to every read getter', async () => {
      setDemo(true);
      primeHappyPath();
      const user = userEvent.setup({ delay: null });
      render(<LicensesPage />);

      await waitFor(() => {
        expect(licenseService.listLicenses).toHaveBeenCalledWith('active', true);
      });

      await user.click(await screen.findByTestId('view-lic_inference_v1'));
      await waitFor(() => {
        expect(licenseService.getLicenseVersions).toHaveBeenCalledWith('lic_inference_v1', true);
      });
    });

    it('OFF: a real-source failure surfaces an error and no mock data', async () => {
      vi.mocked(licenseService.listLicenses).mockRejectedValue(new Error('backend down'));
      render(<LicensesPage />);

      expect(await screen.findByText(/Unable to load licenses/)).toBeInTheDocument();
      expect(screen.getByText('backend down')).toBeInTheDocument();
      // No mock licenses leaked in on failure.
      expect(screen.queryByText(/lic_demo_/)).toBeNull();
      expect(screen.queryByText('Standard inference license')).toBeNull();
    });
  });

  describe('single concern (Req 11.6)', () => {
    it('does not mix in partner identity, entitlements, or analytics surfaces', async () => {
      primeHappyPath();
      render(<LicensesPage />);

      await screen.findByText('lic_inference_v1');

      expect(screen.queryByText(/Entitlement/i)).toBeNull();
      expect(screen.queryByText(/Partner/i)).toBeNull();
      expect(screen.queryByText(/Analytics/i)).toBeNull();
      expect(screen.queryByText(/Revenue/i)).toBeNull();
    });

    it('hand-rolls no demo-data notice (the shared DemoDataBanner owns disclosure)', async () => {
      setDemo(true);
      primeHappyPath();
      render(<LicensesPage />);

      await screen.findByText('lic_inference_v1');
      // The shared banner text must not be re-implemented on the page.
      expect(screen.queryByText(/Demo data — not live/)).toBeNull();
    });
  });
});
