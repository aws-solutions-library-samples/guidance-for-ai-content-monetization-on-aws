/**
 * ApiDocumentation page tests (#31 Req 4.6 / Req 8).
 *
 * - renders the reference at /docs (Internal default) fetching {API}/openapi
 * - pre-fills the Cognito bearer into Scalar's auth config when a session token is present
 * - renders (no crash) when unauthenticated
 * - the Internal|Partner toggle fetches the two variants (/openapi vs /openapi?audience=partner)
 * - shows an explicit error state when the fetch fails (no silent empty doc)
 *
 * Scalar's heavy Vue-backed component is stubbed to a marker that records its `configuration`
 * prop, so we assert on what the page hands it rather than mounting the real reference.
 */

import React from 'react';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// --- mocks (must be declared before importing the component) ---
const mockFetchAuthSession = vi.fn();
vi.mock('aws-amplify/auth', () => ({
  fetchAuthSession: () => mockFetchAuthSession(),
}));

// Capture the configuration handed to Scalar; render a marker with a data-attr for assertions.
let lastConfig: Record<string, unknown> | null = null;
vi.mock('@scalar/api-reference-react', () => ({
  ApiReferenceReact: (props: Record<string, unknown>) => {
    lastConfig = props.configuration;
    const paths = Object.keys(props.configuration?.content?.paths ?? {});
    return (
      <div data-testid="scalar" data-paths={paths.join(',')} data-hastoken={String(!!props.configuration?.authentication)}>
        scalar
      </div>
    );
  },
}));
vi.mock('@scalar/api-reference-react/style.css', () => ({}));

import ApiDocumentation from '../ApiDocumentation';

const FULL = { openapi: '3.0.3', paths: { '/query': {}, '/analytics/metrics': {}, '/content': {} } };
const PARTNER = { openapi: '3.0.3', paths: { '/query': {}, '/licenses': {} } };

function mockFetchOk() {
  return vi.fn((url: string) =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve(url.includes('audience=partner') ? PARTNER : FULL),
    } as Response)
  );
}

describe('ApiDocumentation', () => {
  beforeEach(() => {
    lastConfig = null;
    vi.restoreAllMocks();
    mockFetchAuthSession.mockResolvedValue({ tokens: { idToken: { toString: () => 'eyJ-test-token' } } });
    vi.stubGlobal('fetch', mockFetchOk());
  });

  it('renders the reference (Internal default) fetching /openapi', async () => {
    render(<ApiDocumentation />);
    await waitFor(() => expect(screen.getByTestId('scalar')).toBeTruthy());
    const calledUrl = vi.mocked(global.fetch).mock.calls[0][0] as string;
    expect(calledUrl).toContain('/openapi');
    expect(calledUrl).not.toContain('audience=partner');
    // full view shows internal paths
    expect(screen.getByTestId('scalar').getAttribute('data-paths')).toContain('/analytics/metrics');
  });

  it('pre-fills the Cognito bearer when a session token is present', async () => {
    render(<ApiDocumentation />);
    await waitFor(() => expect(screen.getByTestId('scalar')).toBeTruthy());
    expect(lastConfig.authentication.preferredSecurityScheme).toBe('CognitoAuth');
    expect(lastConfig.authentication.securitySchemes.CognitoAuth.token).toBe('eyJ-test-token');
    // and the fetch carried the Authorization header
    const opts = vi.mocked(global.fetch).mock.calls[0][1];
    expect(opts.headers.Authorization).toBe('Bearer eyJ-test-token');
  });

  it('renders when unauthenticated (no token, no auth config)', async () => {
    mockFetchAuthSession.mockResolvedValue({ tokens: undefined });
    render(<ApiDocumentation />);
    await waitFor(() => expect(screen.getByTestId('scalar')).toBeTruthy());
    expect(lastConfig.authentication).toBeUndefined();
  });

  it('toggling to Partner fetches ?audience=partner and drops internal paths', async () => {
    render(<ApiDocumentation />);
    await waitFor(() => expect(screen.getByTestId('scalar')).toBeTruthy());

    act(() => {
      // Cloudscape SegmentedControl renders option text as buttons
      fireEvent.click(screen.getByText('Partner'));
    });

    await waitFor(() => {
      const urls = vi.mocked(global.fetch).mock.calls.map((c: unknown[]) => c[0] as string);
      expect(urls.some((u: string) => u.includes('audience=partner'))).toBe(true);
    });
    await waitFor(() => {
      const paths = screen.getByTestId('scalar').getAttribute('data-paths') ?? '';
      expect(paths).toContain('/licenses');
      expect(paths).not.toContain('/analytics/metrics'); // internal dropped in partner view
    });
  });

  it('shows an explicit error state when the fetch fails (no silent empty doc)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve({ ok: false, status: 500 } as Response))
    );
    render(<ApiDocumentation />);
    await waitFor(() => expect(screen.getByText('Could not load the API contract')).toBeTruthy());
    expect(screen.queryByTestId('scalar')).toBeNull();
  });
});
