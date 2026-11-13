/**
 * LicensesPage (task 11.2 — the "License" workflow-stage surface)
 *
 * Replaces the honest `LicenseStub` placeholder with a working CRUD surface
 * for RSL license documents, following the `ConnectionsPage` /
 * `PricingConfiguration` pattern: Cloudscape, functional components with
 * hooks, all API access through `licenseService` (task 11.1).
 *
 * Covers (Req 11.1/11.2): list with a `status` filter that includes `revoked`,
 * create, view, update, revoke (soft), renew, validate, and version history.
 *
 * SINGLE CONCERN (Req 11.6): license authoring only. Partner identity lives on
 * `/partners`, entitlement assignment and analytics live on their own
 * surfaces — none of that is mixed in here.
 *
 * Canonical CoMP labels (Req 11.3): `allowed_uses` renders via
 * `compFunctionLabel`, a mirror of the authoritative table in
 * `source/lambda/services/comp_query_service.py`. The retired
 * `license_service.py` labels are never produced.
 *
 * Demo_Toggle / mock-data honesty (#27, Req 11.5): every read getter is called
 * with `allowMock={demoDataEnabled}` from `useFeatureFlags()`. With the toggle
 * OFF (default, fail-safe) a real-source failure surfaces an error and an
 * empty-but-successful response renders an empty state — never a silent mock
 * substitution. Disclosure is the shared `DemoDataBanner`, mounted once in the
 * app chrome; this page hand-rolls NO notice of its own.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Badge,
  Box,
  Button,
  Checkbox,
  ColumnLayout,
  Container,
  ContentLayout,
  Form,
  FormField,
  Header,
  Input,
  Modal,
  Multiselect,
  Select,
  SpaceBetween,
  StatusIndicator,
  Table,
  Textarea,
} from '@cloudscape-design/components';

import { useFeatureFlags } from '../contexts/FeatureFlagContext';
import {
  COMP_FUNCTION_CODE_VALUES,
  COMP_FUNCTION_CODES,
  COMP_FUNCTION_NAMES,
  compFunctionLabel,
  CreateLicenseRequest,
  INTENDED_USE_VALUES,
  LICENSE_STATUSES,
  LicenseDocument,
  LicenseStatus,
  LicenseValidationResponse,
  LicenseVersion,
  licenseService,
} from '../services/licenseService';

/** Options for the `status` list filter (includes `revoked`, Req 11.2). */
const STATUS_OPTIONS = LICENSE_STATUSES.map((status) => ({ label: status, value: status }));

/** Options for the canonical CoMP `allowed_uses` authoring control. */
const ALLOWED_USE_OPTIONS = COMP_FUNCTION_CODE_VALUES.map((code) => ({
  label: COMP_FUNCTION_CODES[code],
  description: COMP_FUNCTION_NAMES[code],
  value: String(code),
}));

/** Options for the validate dialog's `intended_use` field. */
const INTENDED_USE_OPTIONS = INTENDED_USE_VALUES.map((use) => ({ label: use, value: use }));

/** Cloudscape status-indicator type for a license lifecycle status. */
const statusIndicatorType = (status?: string): 'success' | 'stopped' | 'pending' | 'info' => {
  if (status === 'active') {
    return 'success';
  }
  if (status === 'revoked') {
    return 'stopped';
  }
  if (status === 'draft') {
    return 'pending';
  }
  return 'info';
};

/** Authoring form state for create/edit. */
interface LicenseFormState {
  title: string;
  description: string;
  /** Canonical CoMP codes, held as strings for the Multiselect. */
  allowedUses: string[];
  attributionRequired: boolean;
  /** Attribution template string — see the DEFERRED §7 note below. */
  attributionFormat: string;
}

const emptyForm: LicenseFormState = {
  title: '',
  description: '',
  allowedUses: [],
  attributionRequired: false,
  attributionFormat: '',
};

const LicensesPage: React.FC = () => {
  const { demoDataEnabled } = useFeatureFlags();

  const [statusFilter, setStatusFilter] = useState<LicenseStatus>('active');
  const [licenses, setLicenses] = useState<LicenseDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Create/edit modal.
  const [formVisible, setFormVisible] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<LicenseFormState>(emptyForm);
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Detail (view) modal + its version history.
  const [detail, setDetail] = useState<LicenseDocument | null>(null);
  const [versions, setVersions] = useState<LicenseVersion[]>([]);
  const [versionsError, setVersionsError] = useState<string | null>(null);
  const [versionsLoading, setVersionsLoading] = useState(false);

  // Validate modal.
  const [validateTarget, setValidateTarget] = useState<LicenseDocument | null>(null);
  const [agentId, setAgentId] = useState('');
  const [intendedUse, setIntendedUse] = useState<string>(INTENDED_USE_VALUES[0]);
  const [validating, setValidating] = useState(false);
  const [validationResult, setValidationResult] = useState<LicenseValidationResponse | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);

  // Per-row action progress.
  const [busyId, setBusyId] = useState<string | null>(null);

  /**
   * Load the license list for the selected `status`, passing
   * `allowMock={demoDataEnabled}` (fail-safe default off).
   */
  const loadLicenses = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await licenseService.listLicenses(statusFilter, demoDataEnabled);
      setLicenses(result.licenses);
    } catch (err) {
      // Real-source failure surfaces an error — never a silent mock fallback.
      setError(err instanceof Error ? err.message : 'Failed to load licenses.');
      setLicenses([]);
    } finally {
      setLoading(false);
    }
  }, [statusFilter, demoDataEnabled]);

  useEffect(() => {
    void loadLicenses();
  }, [loadLicenses]);

  const openCreate = () => {
    setEditingId(null);
    setForm(emptyForm);
    setFormError(null);
    setFormVisible(true);
  };

  const openEdit = (license: LicenseDocument) => {
    setEditingId(license.license_id);
    setForm({
      title: license.title ?? '',
      description: license.description ?? '',
      allowedUses: (license.allowed_uses ?? []).map((code) => String(code)),
      attributionRequired: Boolean(license.attribution_required),
      attributionFormat: license.attribution_format ?? '',
    });
    setFormError(null);
    setFormVisible(true);
  };

  const closeForm = () => {
    setFormVisible(false);
    setEditingId(null);
    setForm(emptyForm);
    setFormError(null);
  };

  const handleSave = async () => {
    setFormError(null);

    if (!form.title.trim()) {
      setFormError('Title is required.');
      return;
    }
    if (form.allowedUses.length === 0) {
      setFormError('Select at least one allowed use.');
      return;
    }

    const payload: CreateLicenseRequest = {
      title: form.title.trim(),
      description: form.description.trim() || undefined,
      allowed_uses: form.allowedUses.map((value) => Number(value)),
      attribution_required: form.attributionRequired,
      // Contract field (see the DEFERRED §7 note on the control below): sent
      // only when authored, so an unauthored license carries no empty template.
      attribution_format: form.attributionFormat.trim() || undefined,
    };

    setSaving(true);
    try {
      if (editingId) {
        await licenseService.updateLicense(editingId, payload);
        setSuccess(`License ${editingId} updated as a new version.`);
      } else {
        const created = await licenseService.createLicense(payload);
        setSuccess(`License ${created.license_id} created.`);
      }
      closeForm();
      await loadLicenses();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Failed to save license.');
    } finally {
      setSaving(false);
    }
  };

  const handleRevoke = async (license: LicenseDocument) => {
    setBusyId(license.license_id);
    setError(null);
    setSuccess(null);
    try {
      await licenseService.revokeLicense(license.license_id);
      setSuccess(
        `License ${license.license_id} revoked. Version history is preserved; filter by "revoked" to inspect it.`,
      );
      await loadLicenses();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to revoke license.');
    } finally {
      setBusyId(null);
    }
  };

  const handleRenew = async (license: LicenseDocument) => {
    setBusyId(license.license_id);
    setError(null);
    setSuccess(null);
    try {
      await licenseService.renewLicense(license.license_id);
      setSuccess(`License ${license.license_id} renewed as a new active version.`);
      await loadLicenses();
    } catch (err) {
      // A 409 LICENSE_ALREADY_ACTIVE arrives here with its code in the
      // message — renewal is a revoked -> active transition only.
      setError(err instanceof Error ? err.message : 'Failed to renew license.');
    } finally {
      setBusyId(null);
    }
  };

  /** Open the detail view and load its version history. */
  const openDetail = async (license: LicenseDocument) => {
    setDetail(license);
    setVersions([]);
    setVersionsError(null);
    setVersionsLoading(true);
    try {
      const history = await licenseService.getLicenseVersions(
        license.license_id,
        demoDataEnabled,
      );
      setVersions(history.versions);
    } catch (err) {
      setVersionsError(err instanceof Error ? err.message : 'Failed to load version history.');
      setVersions([]);
    } finally {
      setVersionsLoading(false);
    }
  };

  const openValidate = (license: LicenseDocument) => {
    setValidateTarget(license);
    setAgentId('');
    setIntendedUse(INTENDED_USE_VALUES[0]);
    setValidationResult(null);
    setValidationError(null);
  };

  const handleValidate = async () => {
    if (!validateTarget) {
      return;
    }
    setValidationError(null);
    if (!agentId.trim()) {
      setValidationError('Agent ID is required.');
      return;
    }
    setValidating(true);
    try {
      const result = await licenseService.validateLicense(validateTarget.license_id, {
        agent_id: agentId.trim(),
        intended_use: intendedUse,
      });
      setValidationResult(result);
    } catch (err) {
      setValidationError(err instanceof Error ? err.message : 'Failed to validate license.');
      setValidationResult(null);
    } finally {
      setValidating(false);
    }
  };

  const selectedAllowedUseOptions = useMemo(
    () => ALLOWED_USE_OPTIONS.filter((option) => form.allowedUses.includes(option.value)),
    [form.allowedUses],
  );

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description="Author and manage RSL license terms governing how AI systems may use your content."
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={loadLicenses} loading={loading}>
                Refresh
              </Button>
              <Button variant="primary" onClick={openCreate} data-testid="create-license">
                Create license
              </Button>
            </SpaceBetween>
          }
        >
          License
        </Header>
      }
    >
      <SpaceBetween size="l">
        {error && (
          <Alert
            type="error"
            statusIconAriaLabel="Error"
            header="Unable to load licenses"
            dismissible
            onDismiss={() => setError(null)}
          >
            {error}
          </Alert>
        )}

        {success && (
          <Alert
            type="success"
            statusIconAriaLabel="Success"
            dismissible
            onDismiss={() => setSuccess(null)}
          >
            {success}
          </Alert>
        )}

        <Container
          header={
            <Header
              variant="h2"
              counter={`(${licenses.length})`}
              actions={
                <FormField label="Status">
                  <Select
                    selectedOption={{ label: statusFilter, value: statusFilter }}
                    options={STATUS_OPTIONS}
                    onChange={({ detail: changed }) =>
                      setStatusFilter((changed.selectedOption.value as LicenseStatus) ?? 'active')
                    }
                    ariaLabel="Filter licenses by status"
                    data-testid="status-filter"
                  />
                </FormField>
              }
            >
              License documents
            </Header>
          }
        >
          <Table<LicenseDocument>
            items={licenses}
            loading={loading}
            loadingText="Loading licenses..."
            trackBy={(item) => item.license_id}
            variant="embedded"
            ariaLabels={{
              tableLabel: 'License documents',
            }}
            columnDefinitions={[
              {
                id: 'license_id',
                header: 'License ID',
                cell: (item) => item.license_id,
              },
              {
                id: 'title',
                header: 'Title',
                cell: (item) => item.title ?? '—',
              },
              {
                id: 'status',
                header: 'Status',
                cell: (item) => (
                  <StatusIndicator type={statusIndicatorType(item.status)}>
                    {item.status ?? 'unknown'}
                  </StatusIndicator>
                ),
              },
              {
                id: 'allowed_uses',
                header: 'Allowed uses',
                cell: (item) =>
                  (item.allowed_uses ?? []).length > 0 ? (
                    <SpaceBetween direction="horizontal" size="xxs">
                      {(item.allowed_uses ?? []).map((code) => (
                        <Badge key={`${item.license_id}-${String(code)}`} color="blue">
                          {compFunctionLabel(code)}
                        </Badge>
                      ))}
                    </SpaceBetween>
                  ) : (
                    '—'
                  ),
              },
              {
                id: 'version',
                header: 'Version',
                cell: (item) => String(item.version ?? '—'),
              },
              {
                id: 'actions',
                header: 'Actions',
                cell: (item) => (
                  <SpaceBetween direction="horizontal" size="xxs">
                    <Button
                      variant="inline-link"
                      onClick={() => void openDetail(item)}
                      data-testid={`view-${item.license_id}`}
                    >
                      View
                    </Button>
                    <Button
                      variant="inline-link"
                      onClick={() => openEdit(item)}
                      data-testid={`edit-${item.license_id}`}
                    >
                      Edit
                    </Button>
                    <Button
                      variant="inline-link"
                      onClick={() => void openValidate(item)}
                      data-testid={`validate-${item.license_id}`}
                    >
                      Validate
                    </Button>
                    {item.status === 'revoked' ? (
                      <Button
                        variant="inline-link"
                        loading={busyId === item.license_id}
                        onClick={() => void handleRenew(item)}
                        data-testid={`renew-${item.license_id}`}
                      >
                        Renew
                      </Button>
                    ) : (
                      <Button
                        variant="inline-link"
                        loading={busyId === item.license_id}
                        onClick={() => void handleRevoke(item)}
                        data-testid={`revoke-${item.license_id}`}
                      >
                        Revoke
                      </Button>
                    )}
                  </SpaceBetween>
                ),
              },
            ]}
            empty={
              <Box textAlign="center" color="inherit" data-testid="licenses-empty">
                <b>No licenses</b>
                <Box variant="p" color="inherit">
                  No license documents with status &quot;{statusFilter}&quot;.
                </Box>
                <Button onClick={openCreate}>Create license</Button>
              </Box>
            }
          />
        </Container>
      </SpaceBetween>

      {/* ── Create / edit modal ─────────────────────────────────────────── */}
      <Modal
        visible={formVisible}
        onDismiss={closeForm}
        header={editingId ? `Edit license ${editingId}` : 'Create license'}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={closeForm}>
                Cancel
              </Button>
              <Button
                variant="primary"
                onClick={() => void handleSave()}
                loading={saving}
                data-testid="save-license"
              >
                {editingId ? 'Save changes' : 'Create'}
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <Form>
          <SpaceBetween size="m">
            {formError && (
              <Alert type="error" statusIconAriaLabel="Error">
                {formError}
              </Alert>
            )}

            <FormField label="Title">
              <Input
                value={form.title}
                onChange={({ detail: changed }) =>
                  setForm((f) => ({ ...f, title: changed.value }))
                }
                data-testid="field-title"
              />
            </FormField>

            <FormField label="Description">
              <Textarea
                value={form.description}
                onChange={({ detail: changed }) =>
                  setForm((f) => ({ ...f, description: changed.value }))
                }
                data-testid="field-description"
              />
            </FormField>

            <FormField
              label="Allowed uses"
              description="Canonical IAB CoMP v1.0 function codes (0=all, 1=ai-all, 2=ai-train, 3=ai-input, 4=ai-index, 5=search)."
            >
              <Multiselect
                selectedOptions={selectedAllowedUseOptions}
                options={ALLOWED_USE_OPTIONS}
                onChange={({ detail: changed }) =>
                  setForm((f) => ({
                    ...f,
                    allowedUses: changed.selectedOptions
                      .map((option) => option.value)
                      .filter((value): value is string => Boolean(value)),
                  }))
                }
                placeholder="Choose allowed uses"
                ariaLabel="Allowed uses"
                data-testid="field-allowed-uses"
              />
            </FormField>

            <FormField label="Attribution">
              <Checkbox
                checked={form.attributionRequired}
                onChange={({ detail: changed }) =>
                  setForm((f) => ({ ...f, attributionRequired: changed.checked }))
                }
                data-testid="field-attribution-required"
              >
                Attribution required
              </Checkbox>
            </FormField>

            {/*
              Attribution-format authoring control (task 11.3, Req 11.4).

              DEFERRED §7 — built against the CONTRACT, not yet honoured by the
              RUNTIME. `openapi-spec.yaml` declares a flat `attribution_format`
              template string on LicenseDocument / CreateLicenseRequest /
              UpdateLicenseRequest / LicenseValidationResponse, and the contract
              is the single source of truth per the workspace steering — so this
              control authors that field and the value round-trips through the
              contract.

              `services/license_service.py` however stores a NESTED
              `attribution: {required, format, text, url}` where `format` is a
              media-kind enum (text/html/link/schema.org) and the template lives
              in `attribution.text`. The two `format` fields are not the same
              kind of value, so a spec-conformant `attribution_format` payload
              is accepted and silently dropped by `create_license` today.

              Deciding which shape wins is contract-vs-runtime reconciliation,
              which the owner DEFERRED as §7 of this spec — it is deliberately
              NOT resolved here.

              An inline warning used to sit under this control saying the value
              is authored but not yet delivered to agents. It was removed at the
              owner's request for demo presentation. The underlying gap is
              UNCHANGED: what a publisher types here is still silently dropped
              by create_license, so the control looks live and is not. Restore a
              notice, or resolve §7 so the field actually persists, before this
              surface is used for anything but a demo.
            */}
            <FormField
              label="Attribution format"
              description="Template string. Variables in {braces} are substituted from content metadata; agents must use this format when citation=1 or resdis=1."
            >
              <Input
                value={form.attributionFormat}
                onChange={({ detail: changed }) =>
                  setForm((f) => ({ ...f, attributionFormat: changed.value }))
                }
                placeholder="Source: {title} by {author}. {url}"
                data-testid="field-attribution-format"
              />
            </FormField>
          </SpaceBetween>
        </Form>
      </Modal>

      {/* ── Detail (view) + version history modal ──────────────────────── */}
      <Modal
        visible={Boolean(detail)}
        onDismiss={() => setDetail(null)}
        header={detail ? `License ${detail.license_id}` : 'License'}
        footer={
          <Box float="right">
            <Button variant="link" onClick={() => setDetail(null)}>
              Close
            </Button>
          </Box>
        }
      >
        {detail && (
          <SpaceBetween size="l">
            <ColumnLayout columns={2} variant="text-grid">
              <div>
                <Box variant="awsui-key-label">Title</Box>
                <Box>{detail.title ?? '—'}</Box>
              </div>
              <div>
                <Box variant="awsui-key-label">Status</Box>
                <StatusIndicator type={statusIndicatorType(detail.status)}>
                  {detail.status ?? 'unknown'}
                </StatusIndicator>
              </div>
              <div>
                <Box variant="awsui-key-label">Allowed uses</Box>
                <Box data-testid="detail-allowed-uses">
                  {(detail.allowed_uses ?? []).length > 0
                    ? (detail.allowed_uses ?? []).map((code) => compFunctionLabel(code)).join(', ')
                    : '—'}
                </Box>
              </div>
              <div>
                <Box variant="awsui-key-label">Version</Box>
                <Box>{String(detail.version ?? '—')}</Box>
              </div>
              <div>
                <Box variant="awsui-key-label">Attribution required</Box>
                <Box>{detail.attribution_required ? 'Yes' : 'No'}</Box>
              </div>
              <div>
                <Box variant="awsui-key-label">Attribution format</Box>
                <Box>{detail.attribution_format ?? '—'}</Box>
              </div>
              <div>
                <Box variant="awsui-key-label">License URL</Box>
                <Box>{detail.license_url ?? '—'}</Box>
              </div>
              <div>
                <Box variant="awsui-key-label">Updated</Box>
                <Box>{detail.updated_at ?? '—'}</Box>
              </div>
            </ColumnLayout>

            <Container header={<Header variant="h3">Version history</Header>}>
              {versionsError ? (
                <Alert type="error" statusIconAriaLabel="Error" header="Unable to load version history">
                  {versionsError}
                </Alert>
              ) : (
                <Table<LicenseVersion>
                  items={versions}
                  loading={versionsLoading}
                  loadingText="Loading version history..."
                  variant="embedded"
                  trackBy={(item) => String(item.version_number ?? item.version ?? item.updated_at)}
                  ariaLabels={{ tableLabel: 'License version history' }}
                  columnDefinitions={[
                    {
                      id: 'version',
                      header: 'Version',
                      cell: (item) => String(item.version_number ?? item.version ?? '—'),
                    },
                    {
                      id: 'updated_at',
                      header: 'Updated',
                      cell: (item) => item.updated_at ?? '—',
                    },
                    {
                      id: 'updated_by',
                      header: 'Updated by',
                      cell: (item) => item.updated_by ?? '—',
                    },
                    {
                      id: 'change_summary',
                      header: 'Change summary',
                      cell: (item) => item.change_summary ?? '—',
                    },
                  ]}
                  empty={
                    <Box textAlign="center" color="inherit" data-testid="versions-empty">
                      No version history available.
                    </Box>
                  }
                />
              )}
            </Container>
          </SpaceBetween>
        )}
      </Modal>

      {/* ── Validate modal ─────────────────────────────────────────────── */}
      <Modal
        visible={Boolean(validateTarget)}
        onDismiss={() => setValidateTarget(null)}
        header={validateTarget ? `Validate ${validateTarget.license_id}` : 'Validate license'}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setValidateTarget(null)}>
                Close
              </Button>
              <Button
                variant="primary"
                onClick={() => void handleValidate()}
                loading={validating}
                data-testid="run-validation"
              >
                Validate
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <Form>
          <SpaceBetween size="m">
            {validationError && (
              <Alert type="error" statusIconAriaLabel="Error">
                {validationError}
              </Alert>
            )}

            <FormField label="Agent ID">
              <Input
                value={agentId}
                onChange={({ detail: changed }) => setAgentId(changed.value)}
                placeholder="agent_gptbot_openai_001"
                data-testid="field-agent-id"
              />
            </FormField>

            <FormField
              label="Intended use"
              description="Friendly alias for a canonical CoMP function code."
            >
              <Select
                selectedOption={{ label: intendedUse, value: intendedUse }}
                options={INTENDED_USE_OPTIONS}
                onChange={({ detail: changed }) =>
                  setIntendedUse(changed.selectedOption.value ?? INTENDED_USE_VALUES[0])
                }
                ariaLabel="Intended use"
                data-testid="field-intended-use"
              />
            </FormField>

            {validationResult && (
              <Box data-testid="validation-result">
                <StatusIndicator type={validationResult.valid ? 'success' : 'error'}>
                  {validationResult.valid ? 'Permitted by this license' : 'Not permitted'}
                </StatusIndicator>
                {(validationResult.violations ?? []).length > 0 && (
                  <Box variant="p" color="text-body-secondary">
                    {(validationResult.violations ?? [])
                      .map((violation) => violation.description ?? violation.rule)
                      .filter(Boolean)
                      .join('; ')}
                  </Box>
                )}
              </Box>
            )}
          </SpaceBetween>
        </Form>
      </Modal>
    </ContentLayout>
  );
};

export default LicensesPage;
