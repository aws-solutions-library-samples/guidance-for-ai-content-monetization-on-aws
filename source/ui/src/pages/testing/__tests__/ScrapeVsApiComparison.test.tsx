/**
 * Page tests for the publisher-URL comparison: one test per UI state of
 * design §5, plus the R12.2 copy test (no vendor names or vendor price strings).
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const signOutMock = vi.fn();
vi.mock('aws-amplify/auth', () => ({
  signOut: (...a: unknown[]) => signOutMock(...a),
  getCurrentUser: vi.fn(),
  fetchAuthSession: vi.fn(),
}));

vi.mock('../../../services/comparisonRunClient', async () => {
  const actual = await vi.importActual<typeof import('../../../services/comparisonRunClient')>(
    '../../../services/comparisonRunClient'
  );
  return { ...actual, submitRun: vi.fn(), pollUntilReady: vi.fn(), pollLimitMs: () => 180_000 };
});

vi.mock('../../../services/comparisonMeasure', async () => {
  const actual = await vi.importActual<typeof import('../../../services/comparisonMeasure')>(
    '../../../services/comparisonMeasure'
  );
  return { ...actual, countTokens: async (t: string) => (t.match(/\S+/g) ?? []).length };
});

import {
  pollUntilReady,
  RunRequestError,
  SessionExpiredError,
  submitRun,
} from '../../../services/comparisonRunClient';
import { pendingPoll, readyPoll, submitResponse } from '../../../services/__tests__/comparisonFixtures';
import { AppLayoutSlotsProvider, SlottedAppLayout } from '../../../contexts/AppLayoutSlotsContext';
import ScrapeVsApiComparison from '../ScrapeVsApiComparison';

const submitMock = vi.mocked(submitRun);
const pollMock = vi.mocked(pollUntilReady);

const FIXTURE = 'https://portal.example/fixtures/comparison/article.html';

/** The page as the portal mounts it: inside the AppLayout whose split panel and help panel it fills (R18). */
function renderPage() {
  return render(
    <AppLayoutSlotsProvider>
      <SlottedAppLayout navigationHide content={<ScrapeVsApiComparison />} />
    </AppLayoutSlotsProvider>
  );
}

async function runWith(url = 'https://publisher.example/a.html') {
  const user = userEvent.setup();
  renderPage();
  const input = screen.getByLabelText(/article url/i);
  await user.clear(input);
  await user.type(input, url);
  await user.click(screen.getByRole('button', { name: /run comparison/i }));
  return user;
}

beforeEach(() => {
  submitMock.mockReset();
  pollMock.mockReset();
  signOutMock.mockReset();
  delete (import.meta.env as Record<string, unknown>).VITE_COMPARISON_FIXTURE_URL;
});

describe('idle', () => {
  it('prefills the fixture URL, shows the allow-list note and the Run button, and no results', () => {
    (import.meta.env as Record<string, unknown>).VITE_COMPARISON_FIXTURE_URL = FIXTURE;
    renderPage();
    expect(screen.getByLabelText(/article url/i)).toHaveValue(FIXTURE);
    expect(screen.getByText('What this deployment may fetch is shown after you run.')).toBeInTheDocument();
    expect(screen.getByText(/deployer's responsibility/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /run comparison/i })).toBeEnabled();
    expect(screen.queryByText('Verdict')).not.toBeInTheDocument();
    expect(screen.queryByText('Run status')).not.toBeInTheDocument();
    expect(screen.queryByText(/demo|sample run|mock/i)).not.toBeInTheDocument();
  });

  it('disables Run until a URL is entered', () => {
    renderPage();
    expect(screen.getByRole('button', { name: /run comparison/i })).toBeDisabled();
  });

  it('opens the Cost Model over operator inputs with no run', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole('button', { name: /^cost model$/i }));
    expect(screen.getByText(/P\*, break-even price per article/)).toBeInTheDocument();
    expect(screen.getByText(/Assumption inputs/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /close the cost model/i }));
    expect(screen.queryByText(/P\*, break-even/)).not.toBeInTheDocument();
  });
});

describe('Cost Model as a split panel with its help in the help panel (R18)', () => {
  it('opens the Cost Model in the AppLayout split panel, closed by default', async () => {
    const user = userEvent.setup();
    renderPage();
    const toggle = screen.getByRole('button', { name: /^cost model$/i });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('Test outcomes')).not.toBeInTheDocument();
    await user.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(within(screen.getByTestId('cost-model-panel')).getByText('Test outcomes')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Close the Cost Model' })).toBeInTheDocument();
  });

  it('keeps the explanations out of the panel: labels, values and inputs only', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole('button', { name: /^cost model$/i }));
    const panel = within(screen.getByTestId('cost-model-panel'));
    expect(panel.getByText('P*, break-even price per article')).toBeInTheDocument();
    expect(panel.queryByText(/vendor quote/)).not.toBeInTheDocument();
    expect(panel.queryByText(/attempts = 1,000/)).not.toBeInTheDocument();
    expect(panel.queryByText(/dominates/)).not.toBeInTheDocument();
    expect(panel.getAllByRole('button', { name: /^Information about / }).length).toBeGreaterThanOrEqual(5 + 13);
  });

  it.each([
    ['Information about the Cost Model', 'cost_model', /None is a vendor quote/],
    ['Information about P*, the break-even price', 'break_even', /P\* = scraping cost per 1,000 obtained articles ÷ 1,000/],
    ['Information about saving(P)', 'saving', /saving\(P\) = 1 − P \/ P\*/],
    ['Information about the scraping cost', 'scrape_cost', /attempts = 1,000 ÷ success rate/],
    ['Information about the licensed Markdown cost', 'markdown_cost', /dominates/],
    ['Information about the unblocking comparator', 'comparator', /the price the licence has to beat/],
    ["Information about Publisher's price P", 'inputs', /Assumption inputs/],
  ])('"%s" opens the help panel on its topic', async (link, topic, text) => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole('button', { name: /^cost model$/i }));
    await user.click(screen.getByRole('button', { name: link }));
    const help = screen.getByTestId(`cost-model-help-${topic}`);
    expect(help).toHaveTextContent(text);
    expect(screen.getByRole('button', { name: 'Close help panel' })).toBeInTheDocument();
  });

  it('explains tokens once a run measured them', async () => {
    submitMock.mockResolvedValue(submitResponse());
    pollMock.mockResolvedValue({ kind: 'ready', poll: readyPoll() });
    const user = await runWith();
    await screen.findByText('Verdict');
    await user.click(screen.getByRole('button', { name: /^cost model$/i }));
    await user.click(screen.getByRole('button', { name: 'Information about tokens' }));
    expect(screen.getByTestId('cost-model-help-tokens')).toHaveTextContent(/o200k_base/);
  });
});

/** Expand the collapsed-by-default proof section (R3.0c) the way a reader would. */
async function openMeasured(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: /How these numbers were measured/ }));
}

describe('submitting', () => {
  it('shows the run status with both sides waiting while the POST is in flight', async () => {
    submitMock.mockReturnValue(new Promise(() => undefined));
    await runWith();
    expect(await screen.findByText('Run status')).toBeInTheDocument();
    expect(screen.getByText(/Scraping:\s*submitting the URL to the run route/)).toBeInTheDocument();
    expect(screen.getByText('Licensed Markdown: waiting for submit')).toBeInTheDocument();
    expect(screen.queryByText('Verdict')).not.toBeInTheDocument();
    expect(screen.queryByText(/Path [SB]\b/)).not.toBeInTheDocument();
  });
});

describe('submit refused', () => {
  it('names the cause and the fix and renders nothing else', async () => {
    submitMock.mockRejectedValue(new RunRequestError('host is not allowed', 400, 'url_not_allowed'));
    await runWith('https://evil.example/x');
    expect(await screen.findByText(/The URL was refused: url_not_allowed/)).toBeInTheDocument();
    expect(screen.getByText('host is not allowed')).toBeInTheDocument();
    expect(screen.getByText(/COMPARISON_ALLOWED_HOSTS/)).toBeInTheDocument();
    expect(screen.queryByText('Run status')).not.toBeInTheDocument();
    expect(screen.queryByText(/How these numbers were measured/)).not.toBeInTheDocument();
    expect(pollMock).not.toHaveBeenCalled();
  });

  it('never offers the withdrawn already_published_elsewhere refusal (R15)', async () => {
    submitMock.mockResolvedValue(submitResponse());
    pollMock.mockImplementation(async () => new Promise(() => undefined));
    await runWith();
    expect(screen.queryByText(/already_published_elsewhere|Use the existing record/)).not.toBeInTheDocument();
  });
});

describe('ingesting', () => {
  it('shows the ingest progress in the run status and the scraping steps behind the proof section', async () => {
    submitMock.mockResolvedValue(submitResponse());
    pollMock.mockImplementation(async (_id, opts) => {
      opts?.onPending?.(pendingPoll(12_000));
      return new Promise(() => undefined);
    });
    const user = await runWith();
    expect(await screen.findByText('Run status')).toBeInTheDocument();
    expect(await screen.findByText(/Scraping:\s*measured, \d+ cleanup steps done in this browser/)).toBeInTheDocument();
    expect(screen.getByText('Licensed Markdown: ingesting')).toBeInTheDocument();
    expect(screen.getByText(/Waiting for the pipeline to publish\. Elapsed 12\.00 s of 180 s allowed/)).toBeInTheDocument();
    expect(screen.getByTestId('run-information')).toHaveTextContent(/^This deployment fetches only the hosts on its allow-list\./);
    expect(screen.queryByText('Verdict')).not.toBeInTheDocument();
    expect(screen.queryByText('At a glance')).not.toBeInTheDocument();

    // The proof is one click away from the status block (R3.0c).
    await user.click(screen.getByRole('button', { name: /Show how these numbers are measured/ }));
    expect(screen.getByRole('button', { name: /How these numbers were measured/ })).toHaveAttribute('aria-expanded', 'true');
    const columns = screen.getByTestId('path-columns');
    expect(within(columns).getByText('Fetch document')).toBeInTheDocument();
    expect(within(columns).getByText('Strip scripts, styles, templates')).toBeInTheDocument();
    expect(within(columns).getByText('Ingest (one-time preparation)')).toBeInTheDocument();
    expect(within(columns).getByText(/Ingesting\. Elapsed 12\.00 s of 180 s allowed/)).toBeInTheDocument();
  });
});

describe('ready', () => {
  beforeEach(() => {
    submitMock.mockResolvedValue(submitResponse());
    pollMock.mockResolvedValue({ kind: 'ready', poll: readyPoll() });
  });

  it('opens with the verdict: each measured gain with its actual values, coverage, and a one-line summary (R3.0)', async () => {
    await runWith();
    expect(await screen.findByText('Verdict')).toBeInTheDocument();
    expect(screen.getByTestId('verdict-sentence')).toHaveTextContent(
      /^Same article, \d+% fewer tokens, >99% fewer bytes, in 95% less time; 100% of the article's sentences kept\.$/
    );
    const figures = within(screen.getByTestId('verdict-figures'));
    expect(figures.getByText(/^\d+% fewer tokens$/)).toBeInTheDocument();
    expect(figures.getByText(/^\d[\d,]* → \d[\d,]*$/)).toBeInTheDocument(); // token actuals
    expect(figures.getByText('>99% fewer bytes')).toBeInTheDocument(); // 99.5% rounds to 100, but 2.0 KB is not nothing
    expect(figures.getByText('390.6 KB → 2.0 KB')).toBeInTheDocument();
    expect(figures.getByText('95% less time')).toBeInTheDocument();
    expect(figures.getByText('1.50 s → 80 ms')).toBeInTheDocument();
    expect(figures.getByText(/one-time ingest is excluded/)).toBeInTheDocument();
    expect(figures.getByText('100% kept')).toBeInTheDocument();
    expect(figures.getByText('1 of 1 sentences')).toBeInTheDocument();
    expect(figures.getAllByText(/^Measured\./).length).toBe(4);
    // No money in the verdict (R3.3); no design-document vocabulary anywhere (R3).
    expect(figures.queryByText(/USD/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Path [SB]\b/)).not.toBeInTheDocument();
    // Reading order: verdict, table, proof, fine print.
    const text = document.body.textContent ?? '';
    expect(text.indexOf('Verdict')).toBeLessThan(text.indexOf('At a glance'));
    expect(text.indexOf('At a glance')).toBeLessThan(text.indexOf('How these numbers were measured'));
    expect(text.indexOf('How these numbers were measured')).toBeLessThan(text.indexOf('Training pipelines usually strip HTML'));
  });

  it('shows one at-a-glance table, Scraping | Licensed Markdown | Difference, with the modelled cost row labelled and linked (R3.0b)', async () => {
    const user = await runWith();
    await screen.findByText('At a glance');
    const table = within(screen.getByTestId('at-a-glance'));
    expect(table.getByRole('columnheader', { name: /Scraping/ })).toBeInTheDocument();
    expect(table.getByRole('columnheader', { name: /Licensed Markdown/ })).toBeInTheDocument();
    expect(table.getByRole('columnheader', { name: /Difference/ })).toBeInTheDocument();
    expect(table.getByText('Tokens handed to the model')).toBeInTheDocument();
    expect(table.getByText('Tokens after a perfect cleaner')).toBeInTheDocument();
    expect(table.getByText(/lower bound a cleaning scraper/)).toBeInTheDocument();
    expect(table.getByText('Bytes transferred')).toBeInTheDocument();
    expect(table.getByText('HTTP requests')).toBeInTheDocument();
    expect(table.getByText('11 fewer requests')).toBeInTheDocument(); // 12 vs 1
    expect(table.getByText('Time to obtain the article')).toBeInTheDocument();
    expect(table.getByText('Cost per 1,000 retrievals')).toBeInTheDocument();
    expect(table.getByText(/USD (saved|more) per 1,000$/)).toBeInTheDocument();
    expect(table.getByText('Article content kept')).toBeInTheDocument();
    expect(table.getByText('Article text share of the page')).toBeInTheDocument();
    expect(table.getByText('Licence obtained')).toBeInTheDocument();
    expect(table.getByText('a scrape obtains no licence')).toBeInTheDocument();
    expect(table.getByText('Provenance delivered')).toBeInTheDocument();
    expect(table.getByText(/Author: A\. Writer/)).toBeInTheDocument();
    // Evidence: measured rows say so; the one modelled row is labelled and links to its inputs.
    expect(table.getAllByText('Measured').length).toBe(9);
    expect(table.getAllByText('Modelled').length).toBe(1);
    await user.click(table.getByRole('button', { name: /Cost Model inputs/ }));
    expect(screen.getByText(/P\*, break-even price per article/)).toBeInTheDocument();
  });

  it('keeps the step tables as proof, collapsed by default, named for publishers, with every evidence label (R3.0c, R3.1)', async () => {
    const user = await runWith();
    await screen.findByText('Verdict');
    const toggle = screen.getByRole('button', { name: /How these numbers were measured/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await openMeasured(user);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    const columns = within(screen.getByTestId('path-columns'));
    expect(columns.getByRole('heading', { name: 'Scraping' })).toBeInTheDocument();
    expect(columns.getByRole('heading', { name: 'Licensed Markdown' })).toBeInTheDocument();
    expect(columns.getByText('Fetch document')).toBeInTheDocument();
    expect(columns.getByText('Fetch Markdown (published object)')).toBeInTheDocument();
    expect(columns.getAllByText('Measured').length).toBeGreaterThan(10);
    expect(columns.getAllByText('Modelled').length).toBeGreaterThan(0);
    // The conditional re-fetch is not measured yet, and says why.
    expect(columns.getByText(/not through the content API, so there is no conditional request/)).toBeInTheDocument();
    expect(columns.getAllByText('Not measured').length).toBeGreaterThan(0);
    // Fine print last (R3.0d).
    const fine = screen.getByTestId('fine-print');
    expect(fine).toHaveTextContent(/Training pipelines usually strip HTML/);
    expect(fine).toHaveTextContent(/decoded, not compressed/);
    expect(fine).toHaveTextContent(/records nothing billable in V1/);
  });

  it('keeps P* out of the verdict and inside the collapsed-by-default pull-out', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.type(screen.getByLabelText(/article url/i), 'https://publisher.example/a.html');
    await user.click(screen.getByRole('button', { name: /run comparison/i }));
    await screen.findByText('Verdict');
    // R3.3: no P* in the verdict or the at-a-glance table (the closed help drawer explains it, R18).
    for (const id of ['verdict-sentence', 'verdict-figures', 'at-a-glance']) {
      expect(within(screen.getByTestId(id)).queryByText(/P\*/)).not.toBeInTheDocument();
    }
    await user.click(screen.getByRole('button', { name: /^cost model$/i }));
    expect(screen.getByText(/P\*, break-even price per article/)).toBeInTheDocument();
    expect(screen.getByText('saving(P) = 1 − P / P*')).toBeInTheDocument();
    expect(screen.getByText(/Turnkey unblocking comparator per article/)).toBeInTheDocument();
  });

  it('shows the P >= P* branch with the non-cost arguments', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.type(screen.getByLabelText(/article url/i), 'https://publisher.example/a.html');
    await user.click(screen.getByRole('button', { name: /run comparison/i }));
    await screen.findByText('Verdict');
    await user.click(screen.getByRole('button', { name: /^cost model$/i }));
    const price = screen.getByRole('spinbutton', { name: /Publisher's price P/ });
    await user.clear(price);
    await user.type(price, '5');
    expect(await screen.findByText(/P is at or above P\*/)).toBeInTheDocument();
    expect(screen.getByText(/latency, clean Markdown, freshness/)).toBeInTheDocument();
  });

  it('shows an unfavourable result as it is', async () => {
    pollMock.mockResolvedValue({
      kind: 'ready',
      poll: readyPoll({
        api: { ...readyPoll().api!, markdown: Array.from({ length: 5000 }, (_, i) => `w${i}`).join(' ') },
      }),
    });
    await runWith();
    await screen.findByText('Verdict');
    const figures = within(screen.getByTestId('verdict-figures'));
    expect(figures.getByText(/^\d+% more tokens$/)).toBeInTheDocument();
    expect(screen.getByTestId('verdict-sentence')).toHaveTextContent(/more tokens/);
    expect(within(screen.getByTestId('at-a-glance')).getAllByText(/% more tokens$/).length).toBeGreaterThan(0);
  });
});

describe('where the Markdown landed (R15.7, amended 2026-11-12)', () => {
  it('says in plain sentences what the deployment may fetch and who sees the Markdown; the key is in Run details', async () => {
    submitMock.mockResolvedValue(
      submitResponse({
        ingest: 'submitted',
        origin: 'external',
        allow_list_mode: 'open',
        article: { ...submitResponse().article, markdown_key: 'comparison/abc.md' },
      })
    );
    pollMock.mockImplementation(async () => new Promise(() => undefined));
    const user = await runWith();
    expect(await screen.findByTestId('run-information')).toHaveTextContent(
      'This deployment may fetch any public https page. This page is external: its Markdown is kept for this comparison only, outside your catalogue, so partners and search never see it.'
    );
    await user.click(screen.getByRole('button', { name: 'Run details' }));
    expect(screen.getByText('Allow-list mode: open')).toBeInTheDocument();
    expect(screen.getByTestId('markdown-location')).toHaveTextContent(
      'Markdown object: comparison/abc.md (comparison partition, outside your catalogue)'
    );
  });

  it('names the catalogue key for a reused run', async () => {
    submitMock.mockResolvedValue(submitResponse({ ingest: 'reused' }));
    pollMock.mockResolvedValue({ kind: 'ready', poll: readyPoll() });
    const user = await runWith();
    expect(await screen.findByTestId('run-information')).toHaveTextContent(/the one partners and search use\./);
    await user.click(screen.getByRole('button', { name: 'Run details' }));
    const location = screen.getByTestId('markdown-location');
    expect(location).toHaveTextContent('Markdown object: content/publisher.example/a.md (your published catalogue object)');
    expect(location).not.toHaveTextContent(/outside your catalogue/);
  });
});

describe('re-ingest offer (R1.7, amended 2026-11-10)', () => {
  const LABEL = 'Compare fresh pipeline output instead of the published version';

  it('is hidden before any run and after a run that did not reuse a published record', async () => {
    submitMock.mockResolvedValue(submitResponse({ origin: 'external', ingest: 'submitted' }));
    pollMock.mockResolvedValue({ kind: 'ready', poll: readyPoll() });
    renderPage();
    expect(screen.queryByText(LABEL)).not.toBeInTheDocument();
    cleanup();
    await runWith();
    await screen.findByText('Verdict');
    expect(screen.queryByText(LABEL)).not.toBeInTheDocument();
    expect(submitMock).toHaveBeenCalledWith('https://publisher.example/a.html', false);
  });

  it('appears once a run reused the published record, sends reingest when ticked, and keeps the offer', async () => {
    submitMock.mockResolvedValue(submitResponse({ origin: 'owned', ingest: 'reused' }));
    pollMock.mockResolvedValue({ kind: 'ready', poll: readyPoll() });
    const user = await runWith();
    await screen.findByText('Verdict');
    const box = screen.getByRole('checkbox', { name: LABEL });
    expect(box).not.toBeChecked();
    expect(screen.getByText(/Your published record is not changed/)).toBeInTheDocument();

    submitMock.mockResolvedValue(submitResponse({ origin: 'owned', ingest: 'submitted' }));
    await user.click(box);
    await user.click(screen.getByRole('button', { name: /run comparison/i }));
    await waitFor(() => expect(submitMock).toHaveBeenLastCalledWith('https://publisher.example/a.html', true));
    await screen.findByText('Verdict');
    expect(screen.getByRole('checkbox', { name: LABEL })).toBeInTheDocument();
  });

  it('is hidden again when the URL changes, and a hidden option is never sent', async () => {
    submitMock.mockResolvedValue(submitResponse({ origin: 'owned', ingest: 'reused' }));
    pollMock.mockResolvedValue({ kind: 'ready', poll: readyPoll() });
    const user = await runWith();
    await screen.findByText('Verdict');
    await user.click(screen.getByRole('checkbox', { name: LABEL }));
    const input = screen.getByLabelText(/article url/i);
    await user.clear(input);
    // One paste, not 32 keystrokes: each keystroke re-renders the whole result view, which ran
    // this test past the 15 s timeout on the CI runner (pipeline 4038438). Same URL change.
    await user.click(input);
    await user.paste('https://publisher.example/b.html');
    expect(screen.queryByText(LABEL)).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /run comparison/i }));
    await waitFor(() => expect(submitMock).toHaveBeenLastCalledWith('https://publisher.example/b.html', false));
  });
});

describe('licensed Markdown still processing past the poll limit', () => {
  it('shows the still-processing state, keeps the scraping side and re-polls the same run on "Check again"', async () => {
    const sub = submitResponse();
    submitMock.mockResolvedValue(sub);
    pollMock.mockResolvedValueOnce({ kind: 'timeout', poll: pendingPoll(181_000), limitMs: 180_000 });
    const user = await runWith();
    expect(await screen.findByText(/licensed Markdown is still processing/)).toBeInTheDocument();
    expect(screen.getByText(/longer than the 180 s/)).toBeInTheDocument();
    expect(screen.getByText(/still processing after 180 s, so there is no verdict yet/)).toBeInTheDocument();
    expect(screen.queryByText('Verdict')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Show how these numbers are measured/ }));
    const columns = within(screen.getByTestId('path-columns'));
    expect(columns.getByText('Fetch document')).toBeInTheDocument();
    expect(columns.getByText(/had not published after 180000 ms/)).toBeInTheDocument();

    pollMock.mockResolvedValueOnce({ kind: 'ready', poll: readyPoll() });
    await user.click(screen.getByRole('button', { name: /check again/i }));
    expect(await screen.findByText('Verdict')).toBeInTheDocument();
    expect(pollMock).toHaveBeenCalledTimes(2);
    expect(pollMock.mock.calls[1][0]).toBe(sub.run_id);
    expect(submitMock).toHaveBeenCalledTimes(1);
  });
});

describe('session expired', () => {
  it('shows the expired-session state and signs in again on request', async () => {
    submitMock.mockRejectedValue(new SessionExpiredError());
    const user = await runWith();
    expect(await screen.findByText('Session expired')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /sign in again/i }));
    expect(signOutMock).toHaveBeenCalled();
  });

  it('also handles a 401 on a later poll', async () => {
    submitMock.mockResolvedValue(submitResponse());
    pollMock.mockRejectedValue(new SessionExpiredError());
    await runWith();
    expect(await screen.findByText('Session expired')).toBeInTheDocument();
  });
});

describe('unexpected failure', () => {
  it('shows a generic error', async () => {
    submitMock.mockRejectedValue(new Error('network down'));
    await runWith();
    expect(await screen.findByText('network down')).toBeInTheDocument();
  });
});

describe('narrow viewport', () => {
  it('lays the two paths out in a responsive grid so they stack below the s breakpoint', async () => {
    submitMock.mockResolvedValue(submitResponse());
    pollMock.mockResolvedValue({ kind: 'ready', poll: readyPoll() });
    const user = await runWith();
    await screen.findByText('Verdict');
    await openMeasured(user);
    const columns = screen.getByTestId('path-columns');
    const cells = Array.from(columns.querySelectorAll('[class*="awsui_grid-column"]'));
    expect(cells.length).toBe(2);
    // jsdom has no layout width, so Cloudscape resolves its narrowest breakpoint: each path takes
    // the full row, which is the stacked layout of R9.4.
    for (const cell of cells) expect(cell.className).toMatch(/colspan-12/);
  });
});

describe('copy (R12.2)', () => {
  const VENDORS = [
    'cloudflare', 'akamai', 'datadome', 'imperva', 'perimeterx', 'human security', 'brightdata', 'bright data',
    'oxylabs', 'smartproxy', 'zyte', 'scraperapi', 'scrapingbee', 'apify', '2captcha', 'capsolver', 'anticaptcha',
    'openai', 'anthropic', 'google', 'amazon web services', 'cloudfront', 'fastly',
  ];
  const PRICE = /(\$|€|£)\s?\d|\d\s?(\$|€|£)|\bUSD\s?\d/;

  it('contains no vendor names or vendor price strings in the rendered page', async () => {
    const user = userEvent.setup();
    submitMock.mockResolvedValue(submitResponse());
    pollMock.mockResolvedValue({ kind: 'ready', poll: readyPoll() });
    const { container } = renderPage();
    await user.type(screen.getByLabelText(/article url/i), 'https://publisher.example/a.html');
    await user.click(screen.getByRole('button', { name: /run comparison/i }));
    await screen.findByText('Verdict');
    await openMeasured(user);
    await user.click(screen.getByRole('button', { name: /^cost model$/i }));
    await waitFor(() => expect(screen.getByText(/Assumption inputs/)).toBeInTheDocument());
    const text = (container.ownerDocument.body.textContent ?? '').toLowerCase();
    for (const vendor of VENDORS) expect(text).not.toContain(vendor);
    expect(text).not.toMatch(PRICE);
  });

  it.each(['ScrapeVsApiComparison.tsx', 'CostModelPanel.tsx', 'CostModelHelp.tsx', 'StepTable.tsx', 'comparisonView.ts'])(
    'contains no vendor names or price strings in the source of %s',
    (file) => {
      const source = readFileSync(join(__dirname, '..', file), 'utf8').toLowerCase();
      for (const vendor of VENDORS) expect(source).not.toContain(vendor);
      expect(source).not.toMatch(/(\$|€|£)\s?\d/);
    }
  );
});
