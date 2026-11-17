/**
 * Test page: scenario card grid. Card 1 opens the publisher-URL
 * comparison; card 2 wraps the existing agent simulator, labelled mock.
 */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import TestConsole from '../TestConsole';

function Where() {
  return <div data-testid="where">{useLocation().pathname}</div>;
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/test']}>
      <Routes>
        <Route path="/test" element={<TestConsole />} />
        <Route path="/test/comparison" element={<Where />} />
      </Routes>
    </MemoryRouter>
  );
}

describe('TestConsole scenario cards', () => {
  it('shows the comparison card and navigates to /test/comparison', async () => {
    renderPage();
    expect(screen.getByText('Publisher URL comparison')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /open comparison/i }));
    expect(screen.getByTestId('where')).toHaveTextContent('/test/comparison');
  });

  it('wraps the unchanged agent simulator in a card labelled mock, with no toggle', () => {
    renderPage();
    expect(screen.getByText('Mock data')).toBeInTheDocument();
    expect(screen.getByText(/mock, hardcoded data/i)).toBeInTheDocument();
    expect(screen.getByText('Agent Behavior Simulation')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /search content/i })).toBeDisabled();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  });
});
