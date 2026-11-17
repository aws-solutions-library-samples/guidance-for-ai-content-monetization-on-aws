/**
 * DemoDataBanner tests (#27, Task 3.4)
 *
 * The banner renders iff the runtime demo-data toggle is ON, and disappears
 * live when the toggle flips OFF.
 */

import React from 'react';
import { render, screen, act } from '@testing-library/react';
import { describe, it, expect, beforeEach } from 'vitest';
import DemoDataBanner, { DEMO_BANNER_TEXT } from '../DemoDataBanner';
import { FeatureFlagProvider, useFeatureFlags } from '../../contexts/FeatureFlagContext';

/** Test-only control to flip the toggle from within the provider. */
const Toggle: React.FC = () => {
  const { setDemoData } = useFeatureFlags();
  return (
    <>
      <button onClick={() => setDemoData(true)}>enable</button>
      <button onClick={() => setDemoData(false)}>disable</button>
    </>
  );
};

describe('DemoDataBanner', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it('is absent when the demo toggle is OFF', () => {
    render(
      <FeatureFlagProvider overrides={{ enableMockData: false }}>
        <DemoDataBanner />
      </FeatureFlagProvider>
    );
    expect(screen.queryByText(DEMO_BANNER_TEXT)).toBeNull();
  });

  it('renders when the demo toggle is ON', () => {
    render(
      <FeatureFlagProvider overrides={{ enableMockData: true }}>
        <DemoDataBanner />
      </FeatureFlagProvider>
    );
    expect(screen.getByText(DEMO_BANNER_TEXT)).toBeTruthy();
  });

  it('appears and disappears live as the toggle flips', () => {
    render(
      <FeatureFlagProvider overrides={{ enableMockData: false }}>
        <Toggle />
        <DemoDataBanner />
      </FeatureFlagProvider>
    );
    expect(screen.queryByText(DEMO_BANNER_TEXT)).toBeNull();

    act(() => {
      screen.getByText('enable').click();
    });
    expect(screen.getByText(DEMO_BANNER_TEXT)).toBeTruthy();

    act(() => {
      screen.getByText('disable').click();
    });
    expect(screen.queryByText(DEMO_BANNER_TEXT)).toBeNull();
  });
});
