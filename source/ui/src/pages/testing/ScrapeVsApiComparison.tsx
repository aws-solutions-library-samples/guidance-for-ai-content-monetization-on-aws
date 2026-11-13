/**
 * Scrape vs licensed Markdown: the publisher-URL comparison (spec
 * `scrape-vs-api-comparison`). Route `/test/comparison`.
 *
 * The page submits a URL to the Cognito-authenticated run route, runs the
 * scraper-side cleanup in this browser (DOMParser only), polls for the
 * published Markdown and renders the result verdict first (R3.0): the
 * measured gains with their actual values, one at-a-glance table, then the
 * step tables as collapsed proof, then the fine print. The Cost Model is the
 * portal AppLayout's split panel and its explanations are the help panel (R18).
 * There is no demo toggle, no sample run and no mock fallback (R9.3).
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Checkbox,
  ColumnLayout,
  Container,
  ExpandableSection,
  FormField,
  Grid,
  Header,
  Input,
  ProgressBar,
  Spinner,
  SpaceBetween,
  SplitPanel,
  StatusIndicator,
  Table,
  Tabs,
  type BoxProps,
  type SplitPanelProps,
} from '@cloudscape-design/components';
import { signOut } from 'aws-amplify/auth';

import { useAppLayoutSlots } from '../../contexts/AppLayoutSlotsContext';

import {
  countTokens,
  measureMarkdown,
  runCleanup,
  TOKENIZER_LABEL,
  type CleanupResult,
  type MarkdownMeasurement,
} from '../../services/comparisonMeasure';
import {
  pollLimitMs,
  pollUntilReady,
  RunRequestError,
  SessionExpiredError,
  submitRun,
  SUBMIT_ERROR_HINTS,
  toRunRecord,
  type PollResponse,
  allowListSentence,
  markdownLocation,
  originLabel,
  publishedRecordUrl,
  type SubmitErrorCode,
  type SubmitResponse,
} from '../../services/comparisonRunClient';
import type { RunRecord } from '../../services/comparisonRunService';
import { buildFidelityReport } from '../../services/comparisonFidelity';
import CostModelHelp, { type CostModelHelpTopic } from './CostModelHelp';
import CostModelPanel from './CostModelPanel';
import FidelityView from './FidelityView';
import {
  API_STEP_LABELS,
  buildAtAGlance,
  computeCostModel,
  computeHeadline,
  coveragePercent,
  coverageTone,
  DEFAULT_COST_INPUTS,
  FIGURE_LABELS,
  formatCount,
  formatMs,
  gainActuals,
  gainPhrase,
  gainTone,
  SCRAPE_STEP_LABELS,
  verdictSentence,
  type AtAGlanceRow,
  type CostInputs,
  type RowTone,
} from './comparisonView';
import StepTable from './StepTable';

type Phase = 'idle' | 'submitting' | 'refused' | 'ingesting' | 'ready' | 'timeout' | 'expired' | 'error';

const count = (text: string) => countTokens(text);

/** The Cost Model's split panel strings (R18). */
const COST_PANEL_STRINGS: SplitPanelProps.I18nStrings = {
  closeButtonAriaLabel: 'Close the Cost Model',
  openButtonAriaLabel: 'Open the Cost Model',
  preferencesTitle: 'Cost Model position',
  preferencesPositionLabel: 'Position',
  preferencesPositionDescription: 'Show the Cost Model beside the page or below it.',
  preferencesPositionSide: 'Side',
  preferencesPositionBottom: 'Bottom',
  preferencesConfirm: 'Confirm',
  preferencesCancel: 'Cancel',
  preferencesCloseAriaLabel: 'Close the position preferences',
  resizeHandleAriaLabel: 'Resize the Cost Model',
};

const TONE_COLOR: Readonly<Record<RowTone, BoxProps.Color>> = {
  favourable: 'text-status-success',
  unfavourable: 'text-status-error',
  neutral: 'text-status-info',
  none: 'text-status-inactive',
};

/** One verdict figure (R3.0): the relative gain, the actual values beneath it, its evidence label. */
function VerdictFigure({
  label,
  phrase,
  actuals,
  tone,
  note,
  action,
}: {
  label: string;
  phrase: string | null;
  actuals: string | null;
  tone: RowTone;
  note: string;
  action?: React.ReactNode;
}): React.ReactElement {
  return (
    <div>
      <Box variant="awsui-key-label">{label}</Box>
      <Box variant="h1" color={TONE_COLOR[tone]}>
        {phrase ?? 'Not available'}
      </Box>
      {actuals ? <Box variant="h3">{actuals}</Box> : null}
      <Box variant="small" color="text-body-secondary">
        {FIGURE_LABELS.measured}. {note}
      </Box>
      {action}
    </div>
  );
}

export default function ScrapeVsApiComparison(): React.ReactElement {
  const [url, setUrl] = useState<string>(() => import.meta.env.VITE_COMPARISON_FIXTURE_URL ?? '');
  const [reingest, setReingest] = useState(false);
  // R1.7: the URL a run showed to have a published record (owned + reused); the re-ingest offer exists only for it.
  const [publishedUrl, setPublishedUrl] = useState<string | null>(null);
  const reingestOffered = publishedUrl !== null && url.trim() === publishedUrl;
  const [phase, setPhase] = useState<Phase>('idle');
  const [errorText, setErrorText] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<{ code: string | null; message: string } | null>(null);
  const [submit, setSubmit] = useState<SubmitResponse | null>(null);
  const [cleanup, setCleanup] = useState<CleanupResult | null>(null);
  const [poll, setPoll] = useState<PollResponse | null>(null);
  const [markdown, setMarkdown] = useState<MarkdownMeasurement | null>(null);
  const [timedOutAfterMs, setTimedOutAfterMs] = useState<number | undefined>(undefined);
  const [costOpen, setCostOpen] = useState(false);
  const [costInputs, setCostInputs] = useState<CostInputs>(DEFAULT_COST_INPUTS);
  const [activeTab, setActiveTab] = useState<string>('comparison');
  const [helpTopic, setHelpTopic] = useState<CostModelHelpTopic>('cost_model');
  const slots = useAppLayoutSlots();
  // R3.0c: the step tables are proof, collapsed by default.
  const [measuredOpen, setMeasuredOpen] = useState(false);

  // R14.5: sentence coverage is the page's content-quality figure (word share stays secondary).
  const fidelityCoverage = useMemo(() => {
    if (!submit?.html || poll?.status !== 'ready' || !poll.api?.markdown) return null;
    return buildFidelityReport(submit.html, poll.api.markdown).coverage;
  }, [submit, poll]);
  const token = useRef(0);

  const handleFailure = useCallback((err: unknown) => {
    if (err instanceof SessionExpiredError) {
      setPhase('expired');
    } else if (err instanceof RunRequestError && err.status === 400) {
      setRefusal({ code: err.errorCode, message: err.message });
      setPhase('refused');
    } else {
      setErrorText(err instanceof Error ? err.message : 'The comparison failed.');
      setPhase('error');
    }
  }, []);

  const pollRoute = useCallback(
    async (sub: SubmitResponse, mine: number) => {
      setPhase('ingesting');
      setTimedOutAfterMs(undefined);
      try {
        const outcome = await pollUntilReady(sub.run_id, {
          onPending: (p) => {
            if (token.current === mine) setPoll(p);
          },
        });
        if (token.current !== mine) return;
        if (outcome.kind === 'ready' && outcome.poll.api) {
          const md = await measureMarkdown(outcome.poll.api.markdown, count);
          if (token.current !== mine) return;
          setMarkdown(md);
          setPoll(outcome.poll);
          setPhase('ready');
        } else if (outcome.kind === 'timeout') {
          if (outcome.poll) setPoll(outcome.poll);
          setTimedOutAfterMs(outcome.limitMs);
          setPhase('timeout');
        }
      } catch (err) {
        if (token.current === mine) handleFailure(err);
      }
    },
    [handleFailure]
  );

  const run = useCallback(async () => {
    const mine = ++token.current;
    setErrorText(null);
    setRefusal(null);
    setSubmit(null);
    setCleanup(null);
    setPoll(null);
    setMarkdown(null);
    setTimedOutAfterMs(undefined);
    setPhase('submitting');
    const target = url.trim();
    const fresh = reingestOffered && reingest;
    let sub: SubmitResponse;
    try {
      sub = await submitRun(target, fresh);
    } catch (err) {
      if (token.current === mine) handleFailure(err);
      return;
    }
    if (token.current !== mine) return;
    setPublishedUrl(publishedRecordUrl(target, sub, fresh));
    setSubmit(sub);
    runCleanup(sub.html, count)
      .then((result) => {
        if (token.current === mine) setCleanup(result);
      })
      .catch((err: unknown) => {
        if (token.current === mine) handleFailure(err);
      });
    await pollRoute(sub, mine);
  }, [url, reingest, reingestOffered, handleFailure, pollRoute]);

  const checkAgain = useCallback(() => {
    if (submit) void pollRoute(submit, token.current);
  }, [submit, pollRoute]);

  // The schema-2 record, validated by the strict parser before anything renders from it.
  const built = useMemo<{ record: RunRecord | null; error: string | null }>(() => {
    if (!submit || !cleanup) return { record: null, error: null };
    try {
      const pending: PollResponse = poll ?? { run_id: submit.run_id, status: 'pending', elapsed_ms: 0 };
      const usable = pending.status === 'ready' && markdown ? pending : { ...pending, status: 'pending' as const };
      return {
        record: toRunRecord(submit, usable, cleanup, TOKENIZER_LABEL, { markdown: markdown ?? undefined, timedOutAfterMs }),
        error: null,
      };
    } catch (err) {
      return { record: null, error: err instanceof Error ? err.message : 'The run record is invalid.' };
    }
  }, [submit, cleanup, poll, markdown, timedOutAfterMs]);
  const record = built.record;

  const cost = useMemo(() => {
    try {
      return { result: computeCostModel(record, costInputs), error: null as string | null };
    } catch (err) {
      return { result: null, error: err instanceof Error ? err.message : 'The cost model failed.' };
    }
  }, [record, costInputs]);

  // ---- R18: the Cost Model is the AppLayout split panel; its explanations are the help panel ----
  const showHelp = useCallback(
    (topic: CostModelHelpTopic) => {
      setHelpTopic(topic);
      slots.setToolsOpen(true);
    },
    [slots]
  );
  const resetCostInputs = useCallback(() => setCostInputs(DEFAULT_COST_INPUTS), []);
  const costPanel = useMemo(
    () => (
      <SplitPanel header="Cost Model" closeBehavior="hide" i18nStrings={COST_PANEL_STRINGS}>
        <CostModelPanel
          inputs={costInputs}
          onChange={setCostInputs}
          onReset={resetCostInputs}
          onInfo={showHelp}
          result={cost.result}
          error={cost.error}
        />
      </SplitPanel>
    ),
    [costInputs, resetCostInputs, showHelp, cost.result, cost.error]
  );
  useEffect(() => {
    slots.setTools(<CostModelHelp topic={helpTopic} />);
  }, [slots, helpTopic]);
  useEffect(() => {
    slots.setSplitPanel({ panel: costPanel, open: costOpen, onToggle: setCostOpen });
  }, [slots, costPanel, costOpen]);
  useEffect(
    () => () => {
      slots.setTools(null);
      slots.setToolsOpen(false);
      slots.setSplitPanel(null);
    },
    [slots]
  );

  const headline = useMemo(() => (record ? computeHeadline(record) : null), [record]);
  const rows = useMemo<AtAGlanceRow[]>(
    () =>
      record && headline
        ? buildAtAGlance({
            record,
            headline,
            coverage: fidelityCoverage,
            cost: cost.result ? { scrapeTotal: cost.result.scrapeTotal, api: cost.result.api } : null,
          })
        : [],
    [record, headline, fidelityCoverage, cost.result]
  );
  const busy = phase === 'submitting' || phase === 'ingesting';
  const limitMs = timedOutAfterMs ?? pollLimitMs();
  const limitSeconds = Math.round(limitMs / 1000);
  const elapsedMs = poll?.elapsed_ms ?? 0;
  const coveragePct = coveragePercent(fidelityCoverage);

  // ---- R3.0: the verdict ----------------------------------------------------
  const verdict =
    headline && record ? (
      <Container
        header={
          <Header
            variant="h2"
            description="Measured on this run: the licensed Markdown against scraping the same page. Unfavourable figures are shown as they are."
          >
            Verdict
          </Header>
        }
      >
        <SpaceBetween size="m">
          <Box variant="h3" data-testid="verdict-sentence">
            {verdictSentence(headline, fidelityCoverage)}
          </Box>
          <div data-testid="verdict-figures">
            <ColumnLayout columns={4} variant="text-grid">
              <VerdictFigure
                label="Tokens handed to the model"
                phrase={gainPhrase(headline.tokens, 'tokens')}
                actuals={gainActuals(headline.tokens, 'count')}
                tone={gainTone(headline.tokens)}
                note="Raw HTML document vs Markdown."
              />
              <VerdictFigure
                label="Bytes transferred"
                phrase={gainPhrase(headline.bytes, 'bytes')}
                actuals={gainActuals(headline.bytes, 'bytes')}
                tone={gainTone(headline.bytes)}
                note="Page weight vs Markdown."
              />
              <VerdictFigure
                label="Time to obtain the article"
                phrase={gainPhrase(headline.time, 'time')}
                actuals={gainActuals(headline.time, 'ms')}
                tone={gainTone(headline.time)}
                note="Scrape total vs resolve + fetch; the one-time ingest is excluded."
              />
              <VerdictFigure
                label="Article content kept"
                phrase={coveragePct === null ? null : `${coveragePct}% kept`}
                actuals={
                  fidelityCoverage && coveragePct !== null
                    ? `${formatCount(fidelityCoverage.articleMatched)} of ${formatCount(fidelityCoverage.articleSentences)} sentences`
                    : null
                }
                tone={coverageTone(coveragePct)}
                note="Sentence-level, against the article text of the page."
                action={
                  <Button variant="inline-link" onClick={() => setActiveTab('fidelity')}>
                    See what was kept and lost
                  </Button>
                }
              />
            </ColumnLayout>
          </div>
        </SpaceBetween>
      </Container>
    ) : null;

  // ---- R3.0b: the at-a-glance table -----------------------------------------
  const atAGlance =
    rows.length > 0 ? (
      <Container
        header={
          <Header
            variant="h2"
            description="One row per metric. Measured figures come from this run. The cost row is modelled from the Cost Model inputs and is not a measurement."
          >
            At a glance
          </Header>
        }
      >
        <div data-testid="at-a-glance">
          <Table
            variant="embedded"
            wrapLines
            items={rows}
            trackBy={(row) => row.id}
            ariaLabels={{ tableLabel: 'At a glance' }}
            columnDefinitions={[
              {
                id: 'metric',
                header: 'Metric',
                isRowHeader: true,
                cell: (row) => (
                  <SpaceBetween size="xxxs">
                    <Box variant="strong">{row.metric}</Box>
                    {row.note ? (
                      <Box variant="small" color="text-body-secondary">
                        {row.note}
                      </Box>
                    ) : null}
                  </SpaceBetween>
                ),
              },
              { id: 'scrape', header: 'Scraping', cell: (row) => row.scrape },
              { id: 'markdown', header: 'Licensed Markdown', cell: (row) => row.markdown },
              {
                id: 'difference',
                header: 'Difference',
                cell: (row) => (
                  <Box color={TONE_COLOR[row.tone]} fontWeight="bold">
                    {row.difference}
                  </Box>
                ),
              },
              {
                id: 'evidence',
                header: 'Evidence',
                cell: (row) =>
                  row.provenance === 'modelled' ? (
                    <SpaceBetween size="xxxs">
                      <Box>{FIGURE_LABELS.modelled}</Box>
                      <Button variant="inline-link" onClick={() => setCostOpen(true)}>
                        Cost Model inputs
                      </Button>
                    </SpaceBetween>
                  ) : (
                    FIGURE_LABELS[row.provenance]
                  ),
              },
            ]}
          />
        </div>
      </Container>
    ) : null;

  // ---- Run status while there is no verdict yet --------------------------------
  const status = headline ? null : (
    <Container header={<Header variant="h2">Run status</Header>}>
      <SpaceBetween size="s">
        <StatusIndicator type={cleanup ? 'success' : 'in-progress'}>
          Scraping:{' '}
          {phase === 'submitting'
            ? 'submitting the URL to the run route'
            : cleanup
              ? `measured, ${cleanup.steps.length} cleanup steps done in this browser`
              : 'running the cleanup steps in this browser'}
        </StatusIndicator>
        {phase === 'submitting' ? <StatusIndicator type="pending">Licensed Markdown: waiting for submit</StatusIndicator> : null}
        {phase === 'ingesting' ? (
          <ProgressBar
            status="in-progress"
            value={Math.min(100, Math.round((elapsedMs / limitMs) * 100))}
            label="Licensed Markdown: ingesting"
            description={`Waiting for the pipeline to publish. Elapsed ${formatMs(elapsedMs)} of ${limitSeconds} s allowed.`}
          />
        ) : null}
        {phase === 'timeout' ? (
          <StatusIndicator type="info">
            Licensed Markdown: still processing after {limitSeconds} s, so there is no verdict yet. The scraping side is ready.
          </StatusIndicator>
        ) : null}
        {phase === 'ready' ? <StatusIndicator type="warning">Licensed Markdown: obtained, but no verdict could be computed.</StatusIndicator> : null}
        <Button variant="inline-link" onClick={() => setMeasuredOpen(true)}>
          Show how these numbers are measured
        </Button>
      </SpaceBetween>
    </Container>
  );

  // ---- R3.0c: the step tables, collapsed proof -----------------------------------
  const scrapeSteps = record ? (
    <StepTable
      ariaLabel="Scraping steps"
      stepLabels={SCRAPE_STEP_LABELS}
      steps={record.paths.scrape.steps}
      costsUsd={cost.result?.stepCostsUsd.scrape ?? {}}
    />
  ) : phase === 'submitting' ? (
    <Spinner size="big" />
  ) : submit ? (
    <Box>Running the cleanup steps in this browser</Box>
  ) : (
    <Box color="text-status-inactive">Run a URL to see the scraping steps.</Box>
  );

  const markdownSteps = record ? (
    <SpaceBetween size="s">
      <StepTable
        ariaLabel="Licensed Markdown steps"
        stepLabels={API_STEP_LABELS}
        steps={record.paths.api.steps}
        costsUsd={cost.result?.stepCostsUsd.api ?? {}}
      />
      {phase === 'ingesting' ? <Box>Ingesting. Elapsed {formatMs(elapsedMs)} of {limitSeconds} s allowed.</Box> : null}
    </SpaceBetween>
  ) : phase === 'submitting' ? (
    <Box>Waiting for submit</Box>
  ) : phase === 'ingesting' ? (
    <Box>Waiting for the pipeline. Elapsed {formatMs(elapsedMs)}.</Box>
  ) : (
    <Box color="text-status-inactive">Run a URL to see the Markdown steps.</Box>
  );

  const measured = (
    <ExpandableSection
      variant="container"
      headerText="How these numbers were measured"
      headerDescription="Step-by-step evidence for both sides, each figure with its evidence label. Fetch, ingest and resolve times are measured in the run route; cleanup and tokenize times in this browser."
      expanded={measuredOpen}
      onChange={({ detail }) => setMeasuredOpen(detail.expanded)}
    >
      <div data-testid="path-columns">
        <Grid gridDefinition={[{ colspan: { default: 12, s: 6 } }, { colspan: { default: 12, s: 6 } }]}>
          <SpaceBetween size="s">
            <Header variant="h3" description="What a scraper does with the publisher's own page">
              Scraping
            </Header>
            {scrapeSteps}
          </SpaceBetween>
          <SpaceBetween size="s">
            <Header variant="h3" description="The licensed Markdown from the ingest pipeline">
              Licensed Markdown
            </Header>
            {markdownSteps}
          </SpaceBetween>
        </Grid>
      </div>
    </ExpandableSection>
  );

  // ---- R3.0d: the fine print, last ----------------------------------------------
  const finePrint = record ? (
    <Box variant="small" color="text-body-secondary" data-testid="fine-print">
      Token counts are estimates with {TOKENIZER_LABEL}; tokens of scripts, stylesheets and images are not counted, and
      page weight is its own figure. Sizes are decoded, not compressed transfer size. Training pipelines usually strip
      HTML before the model, so the token difference applies to agent and real-time retrieval use. A 200 object read
      records nothing billable in V1, so this run adds no usage or revenue event.
    </Box>
  ) : null;

  return (
    <SpaceBetween size="l">
      <Header
        variant="h1"
        description="Scrape a publisher page and fetch its licensed Markdown, then compare tokens, bytes, time, content kept and modelled cost."
        actions={
          <Button iconName="settings" onClick={() => setCostOpen((open) => !open)} ariaExpanded={costOpen}>
            Cost Model
          </Button>
        }
      >
        Publisher URL comparison
      </Header>

      <Container header={<Header variant="h2">Article URL</Header>}>
        <SpaceBetween size="m">
          <FormField
            label="Article URL"
            description="https only. Third-party pages are fetched only where the deployer has allowed them, and that choice is the deployer's responsibility."
          >
            <Input
              value={url}
              onChange={({ detail }) => setUrl(detail.value)}
              placeholder="https://publisher.example/article.html"
              type="url"
              disabled={busy}
            />
          </FormField>
          {reingestOffered ? (
            <Checkbox
              checked={reingest}
              onChange={({ detail }) => setReingest(detail.checked)}
              disabled={busy}
              description="Runs the pipeline on this fetch into the comparison partition. Your published record is not changed."
            >
              Compare fresh pipeline output instead of the published version
            </Checkbox>
          ) : null}
          {submit ? (
            <SpaceBetween size="xxs">
              {/* R7.3, R15.7 (amended 2026-11-12): what the deployment may fetch, then where the Markdown comes from and who sees it. */}
              <Box color="text-body-secondary" data-testid="run-information">
                {allowListSentence(submit.allow_list_mode)} {originLabel(submit)}
                {submit.article.canonical_overridden
                  ? ' The page named a canonical URL on another host; its own URL was used instead.'
                  : ''}
              </Box>
              <ExpandableSection headerText="Run details" variant="footer" data-testid="run-details">
                <SpaceBetween size="xxs">
                  <Box color="text-body-secondary">Allow-list mode: {submit.allow_list_mode}</Box>
                  <Box color="text-body-secondary" data-testid="markdown-location">
                    Markdown object: <Box variant="code" display="inline">{markdownLocation(submit).key}</Box> (
                    {markdownLocation(submit).note})
                  </Box>
                </SpaceBetween>
              </ExpandableSection>
            </SpaceBetween>
          ) : (
            <Box color="text-body-secondary">What this deployment may fetch is shown after you run.</Box>
          )}
          <Button variant="primary" onClick={() => void run()} loading={busy} disabled={url.trim() === ''}>
            Run comparison
          </Button>
        </SpaceBetween>
      </Container>

      {phase === 'refused' && refusal ? (
        <Alert type="error" header={`The URL was refused${refusal.code ? `: ${refusal.code}` : ''}`}>
          <SpaceBetween size="xs">
            <Box>{refusal.message}</Box>
            {refusal.code && refusal.code in SUBMIT_ERROR_HINTS ? (
              <Box>{SUBMIT_ERROR_HINTS[refusal.code as SubmitErrorCode]}</Box>
            ) : null}
          </SpaceBetween>
        </Alert>
      ) : null}

      {phase === 'expired' ? (
        <Alert
          type="warning"
          header="Session expired"
          action={<Button onClick={() => void signOut()}>Sign in again</Button>}
        >
          Your session has expired. Sign in again, then run the comparison again.
        </Alert>
      ) : null}

      {phase === 'error' && errorText ? (
        <Alert type="error" header="The comparison failed">
          {errorText}
        </Alert>
      ) : null}

      {built.error ? (
        <Alert type="error" header="The run record was rejected">
          {built.error}
        </Alert>
      ) : null}

      {phase === 'timeout' ? (
        <Alert
          type="info"
          header="The licensed Markdown is still processing"
          action={<Button onClick={checkAgain}>Check again</Button>}
        >
          The pipeline is still publishing this page; it has taken longer than the {limitSeconds} s we waited. The scraping
          side is ready — check again to pick up the Markdown once it publishes.
        </Alert>
      ) : null}

      {phase !== 'idle' && phase !== 'refused' && phase !== 'expired' && phase !== 'error' ? (
        <Tabs
          activeTabId={activeTab}
          onChange={({ detail }) => setActiveTab(detail.activeTabId)}
          tabs={[
            {
              id: 'comparison',
              label: 'Comparison',
              content: (
                <SpaceBetween size="l">
                  {status}
                  {verdict}
                  {atAGlance}
                  {measured}
                  {finePrint}
                </SpaceBetween>
              ),
            },
            {
              id: 'fidelity',
              label: 'Content fidelity',
              content: (
                <FidelityView
                  html={submit?.html ?? null}
                  markdown={poll?.status === 'ready' ? (poll.api?.markdown ?? null) : null}
                  markdownState={phase === 'ready' ? 'ready' : phase === 'timeout' ? 'failed' : 'pending'}
                  reused={submit?.ingest === 'reused'}
                />
              ),
            },
          ]}
        />
      ) : null}
    </SpaceBetween>
  );
}
