/**
 * AppLayout slots: lets the page on screen fill the portal AppLayout's tools drawer (a
 * `HelpPanel`) and split panel, the Cloudscape places for help content and side panels
 * (spec scrape-vs-api-comparison R18, design §4a).
 *
 * The AppLayout lives in `AuthenticatedApp`, above the routes, so a page cannot pass these props
 * itself. A page registers content with `useAppLayoutSlots()` and clears it on unmount; with
 * nothing registered the tools drawer is hidden and there is no split panel, so every other page
 * renders as before.
 */

import React, { createContext, useContext, useMemo, useState } from 'react';
import { AppLayout, type AppLayoutProps } from '@cloudscape-design/components';

export interface SplitPanelSlot {
  /** A Cloudscape `<SplitPanel>`. */
  readonly panel: React.ReactNode;
  readonly open: boolean;
  readonly onToggle: (open: boolean) => void;
}

export interface AppLayoutSlots {
  /** Content of the tools drawer (a `HelpPanel`); `null` hides the drawer. */
  readonly setTools: (tools: React.ReactNode | null) => void;
  readonly setToolsOpen: (open: boolean) => void;
  readonly setSplitPanel: (slot: SplitPanelSlot | null) => void;
}

interface SlotState {
  readonly tools: React.ReactNode | null;
  readonly toolsOpen: boolean;
  readonly splitPanel: SplitPanelSlot | null;
}

const SlotsApiContext = createContext<AppLayoutSlots | null>(null);
const SlotsStateContext = createContext<SlotState | null>(null);

export function AppLayoutSlotsProvider({ children }: { children: React.ReactNode }): React.ReactElement {
  const [tools, setTools] = useState<React.ReactNode | null>(null);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [splitPanel, setSplitPanel] = useState<SplitPanelSlot | null>(null);
  // Setters are stable, so pages can list the API as an effect dependency without re-registering.
  const api = useMemo<AppLayoutSlots>(() => ({ setTools, setToolsOpen, setSplitPanel }), []);
  const state = useMemo<SlotState>(() => ({ tools, toolsOpen, splitPanel }), [tools, toolsOpen, splitPanel]);
  return (
    <SlotsApiContext.Provider value={api}>
      <SlotsStateContext.Provider value={state}>{children}</SlotsStateContext.Provider>
    </SlotsApiContext.Provider>
  );
}

/** The slots of the enclosing `SlottedAppLayout`. */
// eslint-disable-next-line react-refresh/only-export-components -- hook is intentionally co-located with its provider
export function useAppLayoutSlots(): AppLayoutSlots {
  const api = useContext(SlotsApiContext);
  if (!api) throw new Error('useAppLayoutSlots must be used inside an AppLayoutSlotsProvider');
  return api;
}

/** R18: a split panel opens beside the content where the screen is wide enough; Cloudscape falls back to the bottom otherwise. */
// eslint-disable-next-line react-refresh/only-export-components -- the default is part of the provider's contract and its tests
export const DEFAULT_SPLIT_PANEL_PREFERENCES: AppLayoutProps.SplitPanelPreferences = { position: 'side' };

type SlotProps =
  | 'tools'
  | 'toolsHide'
  | 'toolsOpen'
  | 'onToolsChange'
  | 'splitPanel'
  | 'splitPanelOpen'
  | 'onSplitPanelToggle'
  | 'splitPanelPreferences'
  | 'onSplitPanelPreferencesChange';

const DEFAULT_ARIA_LABELS: AppLayoutProps.Labels = {
  tools: 'Help panel',
  toolsClose: 'Close help panel',
  toolsToggle: 'Open help panel',
};

/** The portal AppLayout with its tools drawer and split panel driven by the registered slots. */
export function SlottedAppLayout(props: Omit<AppLayoutProps, SlotProps>): React.ReactElement {
  const api = useAppLayoutSlots();
  const state = useContext(SlotsStateContext);
  const [preferences, setPreferences] = useState<AppLayoutProps.SplitPanelPreferences>(DEFAULT_SPLIT_PANEL_PREFERENCES);
  const tools = state?.tools ?? null;
  const splitPanel = state?.splitPanel ?? null;
  return (
    <AppLayout
      {...props}
      ariaLabels={{ ...DEFAULT_ARIA_LABELS, ...props.ariaLabels }}
      tools={tools ?? undefined}
      toolsHide={tools === null}
      toolsOpen={tools !== null && (state?.toolsOpen ?? false)}
      onToolsChange={({ detail }) => api.setToolsOpen(detail.open)}
      splitPanel={splitPanel?.panel}
      splitPanelOpen={splitPanel?.open ?? false}
      onSplitPanelToggle={({ detail }) => splitPanel?.onToggle(detail.open)}
      splitPanelPreferences={preferences}
      onSplitPanelPreferencesChange={({ detail }) => setPreferences(detail)}
    />
  );
}
