/**
 * FeatureFlagContext — runtime Demo_Toggle tests (#27)
 *
 * Covers the runtime demo-data toggle contract:
 *  - default OFF from a clean session (no override, no stored value)
 *  - setDemoData(true) flips consumers live (no reload) and persists
 *  - a sessionStorage seed is respected on mount
 *  - the readInitial precedence: sessionStorage -> env default -> false
 */

import React from 'react';
import { render, screen, act } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { FeatureFlagProvider, useFeatureFlags } from '../FeatureFlagContext';
import { readInitial, DEMO_DATA_STORAGE_KEY } from '../../config/featureFlags';

/** Minimal consumer that surfaces the toggle state and a setter button. */
const Consumer: React.FC = () => {
  const { demoDataEnabled, setDemoData, isFeatureEnabled } = useFeatureFlags();
  return (
    <div>
      <span data-testid="demo">{String(demoDataEnabled)}</span>
      <span data-testid="mockflag">{String(isFeatureEnabled('enableMockData'))}</span>
      <button onClick={() => setDemoData(true)}>on</button>
      <button onClick={() => setDemoData(false)}>off</button>
    </div>
  );
};

describe('readInitial (precedence)', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it('returns the env default when nothing is stored', () => {
    expect(readInitial('cm.test.key', false)).toBe(false);
    expect(readInitial('cm.test.key', true)).toBe(true);
  });

  it('prefers a stored value over the env default', () => {
    sessionStorage.setItem('cm.test.key', 'true');
    expect(readInitial('cm.test.key', false)).toBe(true);
    sessionStorage.setItem('cm.test.key', 'false');
    expect(readInitial('cm.test.key', true)).toBe(false);
  });

  it('falls back to the env default when storage throws', () => {
    const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('storage unavailable');
    });
    expect(readInitial('cm.test.key', true)).toBe(true);
    expect(readInitial('cm.test.key', false)).toBe(false);
    spy.mockRestore();
  });
});

describe('FeatureFlagProvider — runtime demo-data toggle', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it('is OFF by default from a clean session (override wins as production-safe seed)', () => {
    render(
      <FeatureFlagProvider overrides={{ enableMockData: false }}>
        <Consumer />
      </FeatureFlagProvider>
    );
    expect(screen.getByTestId('demo').textContent).toBe('false');
    expect(screen.getByTestId('mockflag').textContent).toBe('false');
  });

  it('setDemoData(true) flips consumers live and persists to sessionStorage', () => {
    render(
      <FeatureFlagProvider overrides={{ enableMockData: false }}>
        <Consumer />
      </FeatureFlagProvider>
    );
    expect(screen.getByTestId('demo').textContent).toBe('false');

    act(() => {
      screen.getByText('on').click();
    });

    expect(screen.getByTestId('demo').textContent).toBe('true');
    // enableMockData in the exposed flags reflects the live toggle, not the const
    expect(screen.getByTestId('mockflag').textContent).toBe('true');
    expect(sessionStorage.getItem(DEMO_DATA_STORAGE_KEY)).toBe('true');

    act(() => {
      screen.getByText('off').click();
    });
    expect(screen.getByTestId('demo').textContent).toBe('false');
    expect(sessionStorage.getItem(DEMO_DATA_STORAGE_KEY)).toBe('false');
  });

  it('respects a sessionStorage seed on mount (no override)', () => {
    sessionStorage.setItem(DEMO_DATA_STORAGE_KEY, 'true');
    render(
      <FeatureFlagProvider>
        <Consumer />
      </FeatureFlagProvider>
    );
    expect(screen.getByTestId('demo').textContent).toBe('true');
    expect(screen.getByTestId('mockflag').textContent).toBe('true');
  });

  it('an explicit override beats the stored seed', () => {
    sessionStorage.setItem(DEMO_DATA_STORAGE_KEY, 'true');
    render(
      <FeatureFlagProvider overrides={{ enableMockData: false }}>
        <Consumer />
      </FeatureFlagProvider>
    );
    expect(screen.getByTestId('demo').textContent).toBe('false');
  });
});
