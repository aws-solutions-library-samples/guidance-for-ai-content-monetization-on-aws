/**
 * API Reference + Test Console page (#31).
 *
 * Renders the OpenAPI contract via Scalar (@scalar/api-reference-react) as a categorized,
 * interactive reference. The contract is fetched AT RUNTIME from the API's `/openapi` route
 * (single source of truth — no bundled copy / no drift):
 *   - Internal view:  GET {API}/openapi
 *   - Partner view:   GET {API}/openapi?audience=partner   (server-side derive_partner_spec)
 *
 * The logged-in Cognito idToken is pre-filled as the CognitoAuth bearer (http/bearer scheme) so
 * UI-authed endpoints are testable in one click, and is also sent on the /openapi fetch itself
 * (the route sits behind the Cognito-authed proxy). If the fetch fails, an explicit error state
 * is shown — never a silent stale/empty document.
 */

import React, { useCallback, useEffect, useState, Suspense, lazy } from 'react';
import { Box, SegmentedControl, SpaceBetween, Spinner, Alert, Button } from '@cloudscape-design/components';
import { fetchAuthSession } from 'aws-amplify/auth';

// Scalar is a large (Vue-backed) bundle. Lazy-load it so it becomes its own async chunk —
// keeps it out of the main app bundle (only fetched when /docs opens) and avoids the Rollup
// build OOM that a synchronous import of the whole reference caused.
const ApiReferenceReact = lazy(async () => {
  const mod = await import('@scalar/api-reference-react');
  await import('@scalar/api-reference-react/style.css');
  return { default: mod.ApiReferenceReact };
});

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');

type Audience = 'internal' | 'partner';

async function getIdToken(): Promise<string | null> {
  try {
    const session = await fetchAuthSession();
    return session.tokens?.idToken?.toString() ?? null;
  } catch {
    return null;
  }
}

export default function ApiDocumentation() {
  const [token, setToken] = useState<string | null>(null);
  const [audience, setAudience] = useState<Audience>('internal');
  const [spec, setSpec] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadSpec = useCallback(async (which: Audience) => {
    setLoading(true);
    setError(null);
    try {
      const idToken = await getIdToken();
      setToken(idToken);
      const url = `${API_BASE}/openapi${which === 'partner' ? '?audience=partner' : ''}`;
      const resp = await fetch(url, {
        headers: idToken ? { Authorization: `Bearer ${idToken}` } : {},
      });
      if (!resp.ok) {
        throw new Error(`Failed to load API contract (HTTP ${resp.status})`);
      }
      setSpec(await resp.json());
    } catch (e) {
      // Explicit error state — never render a silent stale/empty doc (Req 4.3).
      setSpec(null);
      setError(e instanceof Error ? e.message : 'Failed to load the API contract');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSpec(audience);
  }, [audience, loadSpec]);

  return (
    <div className="cm-api-docs" style={{ height: 'calc(100vh - 56px)', overflow: 'auto' }}>
      {/* Hide residual Scalar-branded/external bits not covered by config flags. */}
      <style>{`
        .references-footer,
        .start-item,
        a[href*="scalar.com"],
        a[href*="github.com/scalar"],
        a[href*="apidocumentation.com"] { display: none !important; }
      `}</style>

      <Box padding="s">
        <SegmentedControl
          selectedId={audience}
          onChange={({ detail }) => setAudience(detail.selectedId as Audience)}
          label="API audience"
          options={[
            { id: 'internal', text: 'Internal' },
            { id: 'partner', text: 'Partner' },
          ]}
        />
      </Box>

      {loading && (
        <Box textAlign="center" padding="xxl">
          <SpaceBetween size="s" alignItems="center">
            <Spinner size="large" />
            <Box variant="p">Loading API reference…</Box>
          </SpaceBetween>
        </Box>
      )}

      {error && !loading && (
        <Box padding="l">
          <Alert
            type="error"
            header="Could not load the API contract"
            action={<Button onClick={() => loadSpec(audience)}>Retry</Button>}
          >
            {error}
          </Alert>
        </Box>
      )}

      {spec && !loading && !error && (
        <Suspense
          fallback={
            <Box textAlign="center" padding="xxl">
              <Spinner size="large" />
            </Box>
          }
        >
          <ApiReferenceReact
            configuration={{
              // Runtime-fetched content object (not a bundled url) — single source of truth.
              content: spec,
              // Live "test" calls target the deployed API.
              servers: API_BASE ? [{ url: API_BASE }] : undefined,
              // Pre-fill the Cognito bearer (http/bearer scheme → Scalar `token`, adds Bearer prefix).
              authentication: token
                ? {
                    preferredSecurityScheme: 'CognitoAuth',
                    securitySchemes: { CognitoAuth: { token } },
                  }
                : undefined,
              // Suppress vendor-branded/external elements.
              agentEnabled: false,
              hideClientButton: true,
              showToolbar: 'never',
            }}
          />
        </Suspense>
      )}
    </div>
  );
}
