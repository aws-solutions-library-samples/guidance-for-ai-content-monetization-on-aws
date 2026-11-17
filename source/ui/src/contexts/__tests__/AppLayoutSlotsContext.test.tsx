/**
 * AppLayout slots (spec scrape-vs-api-comparison R18, design §4a): a page fills the tools drawer
 * and the split panel of the portal AppLayout; with nothing registered both are absent, so every
 * other page renders as before. AppLayout is stubbed to observe the props it receives.
 */

import React, { useEffect, useState } from 'react';
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { AppLayoutProps } from '@cloudscape-design/components';

const seen: AppLayoutProps[] = [];
vi.mock('@cloudscape-design/components', async () => {
  const actual = await vi.importActual<typeof import('@cloudscape-design/components')>('@cloudscape-design/components');
  return {
    ...actual,
    AppLayout: (props: AppLayoutProps) => {
      seen.push(props);
      return (
        <div>
          {props.content}
          {props.toolsOpen ? <div data-testid="tools">{props.tools}</div> : null}
          {props.splitPanelOpen ? <div data-testid="split">{props.splitPanel}</div> : null}
        </div>
      );
    },
  };
});

import {
  AppLayoutSlotsProvider,
  DEFAULT_SPLIT_PANEL_PREFERENCES,
  SlottedAppLayout,
  useAppLayoutSlots,
  type AppLayoutSlots,
} from '../AppLayoutSlotsContext';

const last = (): AppLayoutProps => seen[seen.length - 1];

let api: AppLayoutSlots | null = null;

/** A page that registers a help panel and a split panel, and clears both on unmount. */
function HelpfulPage(): React.ReactElement {
  const slots = useAppLayoutSlots();
  const [open, setOpen] = useState(false);
  api = slots;
  useEffect(() => {
    slots.setTools(<p>Help text</p>);
    return () => slots.setTools(null);
  }, [slots]);
  useEffect(() => {
    slots.setSplitPanel({ panel: <p>Side panel</p>, open, onToggle: setOpen });
  }, [slots, open]);
  useEffect(() => () => slots.setSplitPanel(null), [slots]);
  return <button onClick={() => setOpen(true)}>Open side panel</button>;
}

function Shell({ showPage }: { showPage: boolean }): React.ReactElement {
  return (
    <AppLayoutSlotsProvider>
      <SlottedAppLayout navigationHide content={showPage ? <HelpfulPage /> : <p>Plain page</p>} />
    </AppLayoutSlotsProvider>
  );
}

beforeEach(() => {
  seen.length = 0;
  api = null;
});

describe('AppLayout slots', () => {
  it('hides the tools drawer and has no split panel when no page registers one', () => {
    render(<Shell showPage={false} />);
    expect(last().toolsHide).toBe(true);
    expect(last().tools).toBeUndefined();
    expect(last().splitPanel).toBeUndefined();
    expect(last().splitPanelOpen).toBe(false);
  });

  it('prefers the side position for a split panel', () => {
    render(<Shell showPage />);
    expect(DEFAULT_SPLIT_PANEL_PREFERENCES).toEqual({ position: 'side' });
    expect(last().splitPanelPreferences).toEqual({ position: 'side' });
    act(() => last().onSplitPanelPreferencesChange?.(new CustomEvent('x', { detail: { position: 'bottom' } })));
    expect(last().splitPanelPreferences).toEqual({ position: 'bottom' });
  });

  it('shows what a page registers and follows the drawer and panel toggles', async () => {
    const user = userEvent.setup();
    render(<Shell showPage />);
    expect(last().toolsHide).toBe(false);
    expect(last().toolsOpen).toBe(false);
    act(() => api?.setToolsOpen(true));
    expect(screen.getByTestId('tools')).toHaveTextContent('Help text');
    act(() => last().onToolsChange?.(new CustomEvent('x', { detail: { open: false } })));
    expect(screen.queryByTestId('tools')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Open side panel' }));
    expect(screen.getByTestId('split')).toHaveTextContent('Side panel');
    act(() => last().onSplitPanelToggle?.(new CustomEvent('x', { detail: { open: false } })));
    expect(screen.queryByTestId('split')).not.toBeInTheDocument();
  });

  it('clears both slots when the page unmounts', () => {
    const { rerender } = render(<Shell showPage />);
    expect(last().toolsHide).toBe(false);
    rerender(<Shell showPage={false} />);
    expect(last().toolsHide).toBe(true);
    expect(last().splitPanel).toBeUndefined();
  });

  it('refuses to be used outside the provider', () => {
    const Orphan = () => {
      useAppLayoutSlots();
      return null;
    };
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    expect(() => render(<Orphan />)).toThrow(/inside an AppLayoutSlotsProvider/);
    spy.mockRestore();
  });
});
