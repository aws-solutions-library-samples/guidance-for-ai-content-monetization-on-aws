/**
 * One path's step rows (R3.1): colour-coded status, time, bytes, tokens and
 * cost, each figure with exactly one evidence label.
 */

import React from 'react';
import { Box, SpaceBetween, StatusIndicator, Table, type StatusIndicatorProps } from '@cloudscape-design/components';

import type { FigureProvenance, RecordFigure, StepRecord } from '../../services/comparisonRunService';
import { FIGURE_LABELS, formatBytes, formatCount, formatMs, formatUsd } from './comparisonView';

const STATUS_VIEW: Readonly<Record<string, { type: StatusIndicatorProps.Type; text: string }>> = {
  ok: { type: 'success', text: 'Done' },
  failed: { type: 'error', text: 'Failed' },
  pending: { type: 'in-progress', text: 'Pending' },
  skipped: { type: 'stopped', text: 'Skipped' },
  not_measured: { type: 'warning', text: 'Not measured' },
};

function FigureCell({
  figure,
  format,
}: {
  figure: RecordFigure | undefined;
  format: (n: number) => string;
}): React.ReactElement {
  if (!figure) return <Box color="text-status-inactive">—</Box>;
  return (
    <div>
      <div>{figure.value === null ? '—' : format(figure.value)}</div>
      <Box variant="small" color="text-body-secondary">
        {FIGURE_LABELS[figure.provenance]}
      </Box>
    </div>
  );
}

interface Row {
  readonly step: StepRecord;
  readonly cost: RecordFigure | undefined;
}

export interface StepTableProps {
  readonly stepLabels: Readonly<Record<string, string>>;
  readonly steps: readonly StepRecord[];
  /** Modelled cost per article, by step id. */
  readonly costsUsd: Readonly<Record<string, number>>;
  readonly ariaLabel: string;
}

export default function StepTable({ stepLabels, steps, costsUsd, ariaLabel }: StepTableProps): React.ReactElement {
  const items: Row[] = steps.map((step) => {
    const modelled = costsUsd[step.id];
    const cost: RecordFigure | undefined =
      step.cost_usd ??
      (modelled !== undefined ? { value: modelled, provenance: 'modelled' as FigureProvenance } : undefined);
    return { step, cost };
  });
  return (
    <Table
      variant="embedded"
      wrapLines
      items={items}
      trackBy={(row) => row.step.id}
      ariaLabels={{ tableLabel: ariaLabel }}
      columnDefinitions={[
        {
          id: 'step',
          header: 'Step',
          isRowHeader: true,
          cell: ({ step }) => (
            <SpaceBetween size="xxxs">
              <Box variant="strong">{stepLabels[step.id] ?? step.id}</Box>
              {step.reason ? (
                <Box variant="small" color="text-body-secondary">
                  {step.reason}
                </Box>
              ) : null}
            </SpaceBetween>
          ),
        },
        {
          id: 'status',
          header: 'Status',
          cell: ({ step }) => {
            const view = STATUS_VIEW[step.status] ?? STATUS_VIEW.pending;
            return <StatusIndicator type={view.type}>{view.text}</StatusIndicator>;
          },
        },
        { id: 'time', header: 'Time', cell: ({ step }) => <FigureCell figure={step.wall_clock_ms} format={formatMs} /> },
        { id: 'bytes', header: 'Bytes', cell: ({ step }) => <FigureCell figure={step.bytes} format={formatBytes} /> },
        { id: 'tokens', header: 'Tokens', cell: ({ step }) => <FigureCell figure={step.tokens} format={formatCount} /> },
        { id: 'cost', header: 'Cost per article', cell: ({ cost }) => <FigureCell figure={cost} format={formatUsd} /> },
      ]}
    />
  );
}
