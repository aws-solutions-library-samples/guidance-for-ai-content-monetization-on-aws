/**
 * License Service (task 11.1 — publisher license management)
 *
 * Thin client for the **internal-plane**, Cognito-authenticated RSL license
 * API (`/licenses*`) used by the publisher portal's License workflow stage.
 * Every call carries the Cognito id-token bearer header, matching
 * `x-audience: internal` in the contract (the routes are wired in
 * `source/lambda/ui_backend/routes/licenses.py`).
 *
 * Contract source of truth: `openapi-spec.yaml`. The types below mirror the
 * `LicenseDocument` / `CreateLicenseRequest` / `UpdateLicenseRequest` /
 * `RenewLicenseRequest` / `LicenseListResponse` / `LicenseVersionHistory` /
 * `LicenseValidationRequest` / `LicenseValidationResponse` schemas — no fields
 * are invented here.
 *
 * Demo_Toggle / mock-data honesty (#27, Req 11.5): every read getter that can
 * fall back to mock data takes `allowMock = false`. With the default
 * (fail-safe) a real-source failure **rethrows** and an empty-but-successful
 * response returns an empty value (`[]` / zeroed shape) so the page renders an
 * empty state — never a mock substitution. Mock is reachable only when a
 * caller passes `allowMock={demoDataEnabled}` from `useFeatureFlags()`, and is
 * disclosed by the shared `DemoDataBanner`. Mutations (create/update/revoke/
 * renew/validate) have no mock path at all — they always hit the real API and
 * always surface failures.
 */

import { getCurrentUser, fetchAuthSession } from 'aws-amplify/auth';

/**
 * Canonical IAB CoMP v1.0 function codes.
 *
 * MIRROR, NOT A SECOND SOURCE OF TRUTH. The authority is
 * `source/lambda/services/comp_query_service.py` (`COMP_FUNCTION_CODES` /
 * `COMP_FUNCTION_NAMES`), canonicalized by task 1.1 of this spec after a
 * divergent table in `license_service.py` caused a real authorization bug.
 * This table is a verbatim TS mirror of that one — `0=all, 1=ai-all,
 * 2=ai-train, 3=ai-input, 4=ai-index, 5=search`. If the Python table ever
 * changes, change this one in the same commit; a silent divergence here is
 * exactly the class of bug task 1.1 existed to fix.
 */
export const COMP_FUNCTION_CODES: Record<number, string> = {
  0: 'all',
  1: 'ai-all',
  2: 'ai-train',
  3: 'ai-input',
  4: 'ai-index',
  5: 'search',
};

/**
 * Human-readable companions to {@link COMP_FUNCTION_CODES}. Mirror of
 * `COMP_FUNCTION_NAMES` in `services/comp_query_service.py` (same authority,
 * same mirroring rule). Shown as secondary text only — the canonical slug
 * from `COMP_FUNCTION_CODES` is what the page renders as the label (Req 11.3).
 */
export const COMP_FUNCTION_NAMES: Record<number, string> = {
  0: 'All Uses',
  1: 'All AI Uses',
  2: 'Training',
  3: 'Inference/RAG',
  4: 'Indexing',
  5: 'Search',
};

/** The canonical code values, in order, for authoring controls. */
export const COMP_FUNCTION_CODE_VALUES: number[] = [0, 1, 2, 3, 4, 5];

/**
 * Canonical label for one `allowed_uses` entry.
 *
 * Accepts `number | string` because DynamoDB numeric attributes can arrive as
 * either after JSON serialization. An unrecognized value is rendered verbatim
 * as `Unknown (<value>)` rather than guessed at — the retired
 * `license_service.py` labels (`Search/Discovery`, `Evaluation`, `Analysis`)
 * are never produced (Req 11.3).
 */
export function compFunctionLabel(code: number | string): string {
  const numeric = typeof code === 'number' ? code : Number(code);
  if (Number.isInteger(numeric) && numeric in COMP_FUNCTION_CODES) {
    return COMP_FUNCTION_CODES[numeric];
  }
  return `Unknown (${String(code)})`;
}

/**
 * Lifecycle status values the API accepts on the `status` list filter.
 *
 * All four are valid query values (`list_licenses` validates against this set),
 * but only `active` and `revoked` are REACHABLE today — see
 * {@link LICENSE_STATUSES}.
 */
export type LicenseStatus = 'active' | 'draft' | 'archived' | 'revoked';

/**
 * The `status` filter values the list surface offers.
 *
 * Deliberately NOT all of `LicenseStatus`. `draft` and `archived` have no
 * writer in the runtime: `create_license` defaults new documents to `active`
 * (never `draft`) and the UI does not author a status, while
 * `archive_license()` — despite its name — writes `status='revoked'`, so
 * nothing ever produces `archived`. Offering them yielded filter options that
 * could only ever return an empty list.
 *
 * Add a value back here when a path that writes it exists.
 */
export const LICENSE_STATUSES: LicenseStatus[] = ['active', 'revoked'];

/** Nested attribution block carried on the stored license document. */
export interface LicenseAttribution {
  required?: boolean;
  /** Media kind (`text` / `html` / `link` / `schema.org`) — see note below. */
  format?: string | null;
  text?: string | null;
  url?: string | null;
}

/**
 * An RSL license document (mirrors `LicenseDocument`).
 *
 * `allowed_uses` is typed as the canonical CoMP function codes (integers) per
 * Requirement 11.3 and `RenewLicenseRequest.allowed_uses` in the contract,
 * which declares the canonical `0–5` enum. `string` is tolerated in the type
 * only so a numerically-serialized value never crashes rendering.
 */
export interface LicenseDocument {
  license_id: string;
  license_url?: string;
  version?: number | string;
  status?: LicenseStatus | string;
  title?: string;
  description?: string;
  allowed_uses?: Array<number | string>;
  prohibited_uses?: string[];
  attribution_required?: boolean;
  /**
   * DEFERRED §7 — contract field, not honoured by the runtime. See the
   * block comment on {@link CreateLicenseRequest.attribution_format}.
   */
  attribution_format?: string;
  /** The runtime's nested attribution block (not the contract's flat field). */
  attribution?: LicenseAttribution | boolean;
  geographic_restrictions?: string[];
  content_categories?: number[];
  expiry?: LicenseExpiry;
  comp_compatible?: boolean;
  created_at?: string;
  updated_at?: string;
  current_version_number?: number;
}

/** License term expiry (mirrors `LicenseDocument.expiry`). */
export interface LicenseExpiry {
  type?: 'perpetual' | 'fixed_date' | 'rolling_days';
  expires_at?: string;
  rolling_days?: number;
}

/**
 * Create payload (mirrors `CreateLicenseRequest`).
 *
 * ──────────────────────────────────────────────────────────────────────────
 * DEFERRED §7 — `attribution_format` round-trips through the CONTRACT but is
 * NOT honoured by the runtime today.
 *
 * `openapi-spec.yaml` declares a flat `attribution_format`: a template string
 * (`'Source: {title} by {author}. {url}'`) on `LicenseDocument`,
 * `CreateLicenseRequest`, `UpdateLicenseRequest`, and
 * `LicenseValidationResponse` (added by task 3.2). The runtime
 * (`source/lambda/services/license_service.py` `create_license`) instead
 * stores a NESTED `attribution: {required, format, text, url}` where `format`
 * is an enum of media kinds (`text` / `html` / `link` / `schema.org`) and the
 * template actually lives in `attribution.text`. Those two `format` fields are
 * not the same kind of value, so a spec-conformant `attribution_format`
 * payload is currently accepted and silently dropped by `create_license`.
 *
 * Deciding which shape wins is contract-vs-runtime reconciliation, which the
 * owner DEFERRED as section 7 of this spec. This client therefore builds
 * against the contract (the documented source of truth per the workspace
 * steering) and does NOT attempt that reconciliation. The authoring control on
 * the page carries a visible warning so a publisher is not misled into
 * believing the value reaches agents yet — see `LicensesPage.tsx`.
 * ──────────────────────────────────────────────────────────────────────────
 */
export interface CreateLicenseRequest {
  title: string;
  description?: string;
  allowed_uses: number[];
  prohibited_uses?: string[];
  attribution_required: boolean;
  /** Attribution template string — see the DEFERRED §7 note above. */
  attribution_format?: string;
  geographic_restrictions?: string[];
  content_categories?: number[];
  expiry?: LicenseExpiry;
  comp_compatible?: boolean;
}

/**
 * Update payload (mirrors `UpdateLicenseRequest` — `allOf` over
 * `CreateLicenseRequest`, every field optional on the wire). An update appends
 * a new version server-side.
 */
export type UpdateLicenseRequest = Partial<CreateLicenseRequest>;

/** Renewal payload (mirrors `RenewLicenseRequest`; an empty body is valid). */
export interface RenewLicenseRequest {
  expiration_date?: string;
  /** Canonical CoMP function codes `0–5`. */
  allowed_uses?: number[];
  metadata?: Record<string, unknown>;
}

/** List response (mirrors `LicenseListResponse`). */
export interface LicenseListResult {
  licenses: LicenseDocument[];
  total_count?: number;
  next_token?: string;
}

/** One entry of the version history (mirrors `LicenseVersionHistory.versions`). */
export interface LicenseVersion {
  version_number?: number;
  version?: number | string;
  updated_at?: string;
  updated_by?: string;
  change_summary?: string;
}

/** Version history (mirrors `LicenseVersionHistory`). */
export interface LicenseVersionHistory {
  license_id: string;
  current_version?: string;
  versions: LicenseVersion[];
}

/** Validate payload (mirrors `LicenseValidationRequest`). */
export interface LicenseValidationRequest {
  agent_id: string;
  /** Friendly alias for a canonical CoMP function code. */
  intended_use: string;
  content_ids?: string[];
}

/** The `intended_use` values the contract enumerates. */
export const INTENDED_USE_VALUES: string[] = [
  'training',
  'fine-tuning',
  'finetune',
  'inference',
  'rag',
  'indexing',
  'index',
  'search',
  'discovery',
];

/** One validation violation (mirrors `LicenseValidationResponse.violations`). */
export interface LicenseViolation {
  rule?: string;
  description?: string;
}

/** Validate response (mirrors `LicenseValidationResponse`). */
export interface LicenseValidationResponse {
  valid?: boolean;
  license_id?: string;
  intended_use?: string;
  /** See the DEFERRED §7 note on {@link CreateLicenseRequest}. */
  attribution_format?: string;
  violations?: LicenseViolation[];
  validated_at?: string;
}

/** Error code the renew endpoint returns when the license is already active. */
export const LICENSE_ALREADY_ACTIVE = 'LICENSE_ALREADY_ACTIVE';

class LicenseService {
  private apiBaseUrl: string;

  constructor() {
    this.apiBaseUrl = import.meta.env.VITE_API_BASE_URL || 'https://api.example.com';
  }

  /**
   * Cognito auth headers for the internal license API. The internal plane is
   * `x-audience: internal` (Cognito only), so a bearer id-token is required.
   */
  private async getAuthHeaders(): Promise<Record<string, string>> {
    try {
      const user = await getCurrentUser();
      if (user) {
        const session = await fetchAuthSession();
        const token = session.tokens?.idToken?.toString();
        if (token) {
          return {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
          };
        }
      }
    } catch (error) {
      console.warn('No authentication available:', error);
    }

    return { 'Content-Type': 'application/json' };
  }

  /** Build a readable error from a non-ok response, preferring the API's code. */
  private async describeFailure(response: Response, fallback: string): Promise<string> {
    try {
      const body = await response.json();
      const code = body?.error_code ?? body?.code;
      const message = body?.message ?? body?.error;
      if (code && message) {
        return `${message} (${code})`;
      }
      if (message) {
        return String(message);
      }
    } catch {
      // Non-JSON or empty body — fall through to the status text.
    }
    return `${fallback}: ${response.statusText || response.status}`;
  }

  /**
   * List license documents (mirrors `GET /licenses`).
   *
   * @param status optional lifecycle filter; the API defaults to `active`, so
   *   pass `revoked` to inspect soft-revoked licenses.
   * @param allowMock when `true` (demo mode) a failed real request falls back
   *   to mock licenses. Defaults to `false` (fail-safe): failures rethrow and
   *   an empty-but-successful response returns an empty list (#27).
   */
  async listLicenses(status?: LicenseStatus, allowMock = false): Promise<LicenseListResult> {
    try {
      const headers = await this.getAuthHeaders();
      const query = status ? `?status=${encodeURIComponent(status)}` : '';
      const response = await fetch(`${this.apiBaseUrl}/licenses${query}`, {
        method: 'GET',
        headers,
      });

      if (!response.ok) {
        throw new Error(await this.describeFailure(response, 'Failed to fetch licenses'));
      }

      const data = await response.json();
      return {
        licenses: (data.licenses || []) as LicenseDocument[],
        total_count: data.total_count,
        next_token: data.next_token,
      };
    } catch (error) {
      console.error('Error fetching licenses:', error);
      if (allowMock) {
        // Demo mode only: mock licenses, surfaced behind the shared banner.
        return { licenses: this.getMockLicenses(status), total_count: undefined };
      }
      throw error;
    }
  }

  /**
   * Read one license document (mirrors `GET /licenses/{license_id}`).
   *
   * Returns `null` when the license is not served (HTTP 404 — which is also
   * how a revoked license reads) so the caller can render an empty state
   * rather than an error. Other failures rethrow unless `allowMock`.
   */
  async getLicense(licenseId: string, allowMock = false): Promise<LicenseDocument | null> {
    try {
      const headers = await this.getAuthHeaders();
      const response = await fetch(
        `${this.apiBaseUrl}/licenses/${encodeURIComponent(licenseId)}`,
        { method: 'GET', headers },
      );

      if (response.status === 404) {
        return null;
      }

      if (!response.ok) {
        throw new Error(await this.describeFailure(response, 'Failed to fetch license'));
      }

      return (await response.json()) as LicenseDocument;
    } catch (error) {
      console.error('Error fetching license:', error);
      if (allowMock) {
        return this.getMockLicenses().find((l) => l.license_id === licenseId) ?? null;
      }
      throw error;
    }
  }

  /** Create a license document (mirrors `POST /licenses`, 201). */
  async createLicense(request: CreateLicenseRequest): Promise<LicenseDocument> {
    const headers = await this.getAuthHeaders();
    const response = await fetch(`${this.apiBaseUrl}/licenses`, {
      method: 'POST',
      headers,
      body: JSON.stringify(request),
    });

    if (!response.ok) {
      throw new Error(await this.describeFailure(response, 'Failed to create license'));
    }

    return (await response.json()) as LicenseDocument;
  }

  /**
   * Update a license document (mirrors `PUT /licenses/{license_id}`). The
   * server appends a new version; previous versions stay in the history.
   */
  async updateLicense(
    licenseId: string,
    request: UpdateLicenseRequest,
  ): Promise<LicenseDocument> {
    const headers = await this.getAuthHeaders();
    const response = await fetch(
      `${this.apiBaseUrl}/licenses/${encodeURIComponent(licenseId)}`,
      { method: 'PUT', headers, body: JSON.stringify(request) },
    );

    if (!response.ok) {
      throw new Error(await this.describeFailure(response, 'Failed to update license'));
    }

    return (await response.json()) as LicenseDocument;
  }

  /**
   * Revoke a license document (mirrors `DELETE /licenses/{license_id}`).
   *
   * This is a SOFT revoke: the server appends a `revoked` version and keeps
   * the full history. The response is `204` with an empty body, so nothing is
   * parsed here. Revoking an already-revoked license is idempotent (also 204).
   */
  async revokeLicense(licenseId: string): Promise<void> {
    const headers = await this.getAuthHeaders();
    const response = await fetch(
      `${this.apiBaseUrl}/licenses/${encodeURIComponent(licenseId)}`,
      { method: 'DELETE', headers },
    );

    if (!response.ok) {
      throw new Error(await this.describeFailure(response, 'Failed to revoke license'));
    }
  }

  /**
   * Renew a revoked license (mirrors `POST /licenses/{license_id}/renew`).
   *
   * Renewal is defined as a `revoked -> active` transition, so renewing an
   * already-active license is a `409` carrying
   * {@link LICENSE_ALREADY_ACTIVE} — surfaced here as an error whose message
   * includes that code so the page can explain it rather than look broken.
   */
  async renewLicense(
    licenseId: string,
    request: RenewLicenseRequest = {},
  ): Promise<LicenseDocument> {
    const headers = await this.getAuthHeaders();
    const response = await fetch(
      `${this.apiBaseUrl}/licenses/${encodeURIComponent(licenseId)}/renew`,
      { method: 'POST', headers, body: JSON.stringify(request) },
    );

    if (!response.ok) {
      throw new Error(await this.describeFailure(response, 'Failed to renew license'));
    }

    return (await response.json()) as LicenseDocument;
  }

  /**
   * Read a license's full version history (mirrors
   * `GET /licenses/{license_id}/versions`).
   *
   * @param allowMock when `true` (demo mode) a failed real request falls back
   *   to a mock history. Defaults to `false` (fail-safe): failures rethrow and
   *   an empty-but-successful response returns an empty `versions` list (#27).
   */
  async getLicenseVersions(
    licenseId: string,
    allowMock = false,
  ): Promise<LicenseVersionHistory> {
    try {
      const headers = await this.getAuthHeaders();
      const response = await fetch(
        `${this.apiBaseUrl}/licenses/${encodeURIComponent(licenseId)}/versions`,
        { method: 'GET', headers },
      );

      if (!response.ok) {
        throw new Error(await this.describeFailure(response, 'Failed to fetch version history'));
      }

      const data = await response.json();
      return {
        license_id: data.license_id ?? licenseId,
        current_version: data.current_version,
        versions: (data.versions || []) as LicenseVersion[],
      };
    } catch (error) {
      console.error('Error fetching license versions:', error);
      if (allowMock) {
        return this.getMockVersionHistory(licenseId);
      }
      throw error;
    }
  }

  /**
   * Validate a declared intended use against a license (mirrors
   * `POST /licenses/{license_id}/validate`).
   *
   * A compliant-but-denied check is a `200` with `valid: false`, so a denial
   * is returned rather than thrown. No mock path: a validation result is an
   * authorization answer and must never be fabricated.
   */
  async validateLicense(
    licenseId: string,
    request: LicenseValidationRequest,
  ): Promise<LicenseValidationResponse> {
    const headers = await this.getAuthHeaders();
    const response = await fetch(
      `${this.apiBaseUrl}/licenses/${encodeURIComponent(licenseId)}/validate`,
      { method: 'POST', headers, body: JSON.stringify(request) },
    );

    if (!response.ok) {
      throw new Error(await this.describeFailure(response, 'Failed to validate license'));
    }

    return (await response.json()) as LicenseValidationResponse;
  }

  /**
   * Mock license documents for demo mode only. `allowed_uses` carries the
   * canonical CoMP codes so demo rendering exercises the same label path as
   * real data. Never returned unless `allowMock` is `true`.
   */
  private getMockLicenses(status?: LicenseStatus): LicenseDocument[] {
    const licenses: LicenseDocument[] = [
      {
        license_id: 'lic_demo_inference_v1',
        license_url: 'https://api.example.com/licenses/lic_demo_inference_v1',
        version: 1,
        status: 'active',
        title: 'Demo inference license',
        description: 'Permits inference/RAG with required attribution.',
        allowed_uses: [3],
        attribution_required: true,
        attribution_format: 'Source: {title} by {author}. {url}',
        comp_compatible: true,
        created_at: '2025-01-15T10:30:00Z',
        updated_at: '2025-01-15T10:30:00Z',
      },
      {
        license_id: 'lic_demo_revoked_v1',
        license_url: 'https://api.example.com/licenses/lic_demo_revoked_v1',
        version: 2,
        status: 'revoked',
        title: 'Demo retired search license',
        description: 'Soft-revoked; retained for audit.',
        allowed_uses: [5],
        attribution_required: false,
        comp_compatible: true,
        created_at: '2025-01-02T09:00:00Z',
        updated_at: '2025-02-01T09:00:00Z',
      },
    ];
    return status ? licenses.filter((l) => l.status === status) : licenses;
  }

  /** Mock version history for demo mode only. */
  private getMockVersionHistory(licenseId: string): LicenseVersionHistory {
    return {
      license_id: licenseId,
      current_version: '2',
      versions: [
        {
          version_number: 2,
          version: 2,
          updated_at: '2025-02-01T09:00:00Z',
          updated_by: 'demo-publisher',
          change_summary: 'Added search to allowed uses',
        },
        {
          version_number: 1,
          version: 1,
          updated_at: '2025-01-02T09:00:00Z',
          updated_by: 'demo-publisher',
          change_summary: 'Initial version',
        },
      ],
    };
  }
}

export const licenseService = new LicenseService();
