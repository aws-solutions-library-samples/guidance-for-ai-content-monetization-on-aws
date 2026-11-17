/**
 * Cost Model panel content (R4, R18): every Assumption input, `P*`, `saving(P)`, the unblocking
 * comparator and the `P >= P*` branch. Never part of the headline.
 *
 * Rendered inside the page's Cloudscape `SplitPanel`. Labels, values and inputs stay here; the
 * explanation of each term and formula is one Info link away, in the help panel (CostModelHelp).
 */

import React from 'react';
import {
  Alert,
  Box,
  Button,
  ExpandableSection,
  FormField,
  Header,
  Input,
  Link,
  SpaceBetween,
} from '@cloudscape-design/components';

import type { CostModelHelpTopic } from './CostModelHelp';
import {
  COST_INPUT_DEFINITIONS,
  FIGURE_LABELS,
  formatUsd,
  type CostInputs,
  type CostModelResult,
} from './comparisonView';

export interface CostModelPanelProps {
  readonly inputs: CostInputs;
  readonly onChange: (next: CostInputs) => void;
  readonly onReset: () => void;
  /** Open the help panel on a topic (R18). */
  readonly onInfo: (topic: CostModelHelpTopic) => void;
  readonly result: CostModelResult | null;
  readonly error: string | null;
}

/** A Cloudscape Info link that opens the help panel on one topic. */
export function InfoLink({
  topic,
  about,
  onInfo,
}: {
  topic: CostModelHelpTopic;
  about: string;
  onInfo: (topic: CostModelHelpTopic) => void;
}): React.ReactElement {
  return (
    <Link variant="info" ariaLabel={`Information about ${about}`} onFollow={() => onInfo(topic)}>
      Info
    </Link>
  );
}

function Line({
  label,
  value,
  evidence,
  info,
}: {
  label: string;
  value: string;
  evidence: string;
  info?: React.ReactNode;
}): React.ReactElement {
  return (
    <Box>
      <SpaceBetween direction="horizontal" size="xs" alignItems="center">
        <Box variant="awsui-key-label">{label}</Box>
        {info}
      </SpaceBetween>
      <div>
        {value} <Box variant="small" color="text-body-secondary" display="inline">({evidence})</Box>
      </div>
    </Box>
  );
}

export default function CostModelPanel({
  inputs,
  onChange,
  onReset,
  onInfo,
  result,
  error,
}: CostModelPanelProps): React.ReactElement {
  const info = (topic: CostModelHelpTopic, about: string) => <InfoLink topic={topic} about={about} onInfo={onInfo} />;
  return (
    <SpaceBetween size="m" data-testid="cost-model-panel">
      <Header variant="h3" info={info('cost_model', 'the Cost Model')}>
        Test outcomes
      </Header>

      {error ? <Alert type="warning" header="Check the inputs">{error}</Alert> : null}

      {result ? (
        <SpaceBetween size="s">
          <Line
            label="P*, break-even price per article"
            value={formatUsd(result.breakEven.value)}
            evidence={FIGURE_LABELS[result.breakEven.provenance]}
            info={info('break_even', 'P*, the break-even price')}
          />
          <Line
            label="Scraping cost per 1,000 obtained articles"
            value={formatUsd(result.scrapeTotal.value)}
            evidence={FIGURE_LABELS[result.scrapeTotal.provenance]}
            info={info('scrape_cost', 'the scraping cost')}
          />
          <Line
            label="Licensed Markdown cost per 1,000 articles"
            value={formatUsd(result.api.value)}
            evidence={FIGURE_LABELS[result.api.provenance]}
            info={info('markdown_cost', 'the licensed Markdown cost')}
          />
          <Line
            label="saving(P) = 1 − P / P*"
            value={`${(result.saving.ratio * 100).toFixed(1)}%`}
            evidence={FIGURE_LABELS[result.saving.provenance]}
            info={info('saving', 'saving(P)')}
          />
          <Line
            label="Turnkey unblocking comparator per article"
            value={formatUsd(result.unblocking.value)}
            evidence={FIGURE_LABELS[result.unblocking.provenance]}
            info={info('comparator', 'the unblocking comparator')}
          />
          <Box>
            {result.beatsUnblocking
              ? 'At this P the licensed API is cheaper than the unblocking comparator.'
              : 'At this P the licensed API is not cheaper than the unblocking comparator.'}
          </Box>
          {result.tokenDelta ? (
            <Line
              label="Input-token cost avoided per 1,000 articles"
              value={formatUsd(result.tokenDelta.value)}
              evidence={FIGURE_LABELS[result.tokenDelta.provenance]}
              info={info('tokens', 'tokens')}
            />
          ) : null}
          {!result.saving.favourable ? (
            <Alert type="warning" header="P is at or above P*">
              At this price buying is not cheaper than scraping. The arguments that remain are not cost:
              latency, clean Markdown, freshness through a conditional request (not measured in V1), and licensed
              provenance. Legal and reputational exposure is not expressed as money.
            </Alert>
          ) : null}
        </SpaceBetween>
      ) : null}

      <ExpandableSection headerText="Measured inputs" defaultExpanded>
        <SpaceBetween size="xs">
          {(result?.measuredInputs ?? []).map((m) => (
            <Line
              key={m.label}
              label={m.label}
              value={m.value}
              evidence={m.provenance === 'measured' ? 'Measured' : 'Assumption, until a run measures it'}
              info={info('inputs', m.label)}
            />
          ))}
        </SpaceBetween>
      </ExpandableSection>

      <ExpandableSection
        headerText="Assumption inputs (editable)"
        defaultExpanded
        headerActions={<Button onClick={onReset}>Reset inputs</Button>}
      >
        <SpaceBetween size="s">
          {COST_INPUT_DEFINITIONS.map((def) => (
            <FormField
              key={def.key}
              label={`${def.label} (${def.unit})`}
              description={`Assumption. Source category: ${def.category}.`}
              info={info('inputs', def.label)}
            >
              <Input
                type="number"
                inputMode="decimal"
                value={inputs[def.key]}
                onChange={({ detail }) => onChange({ ...inputs, [def.key]: detail.value })}
              />
            </FormField>
          ))}
        </SpaceBetween>
      </ExpandableSection>
    </SpaceBetween>
  );
}
