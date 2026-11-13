/**
 * Cost Model help (spec scrape-vs-api-comparison R18): the explanations of every term and formula
 * of the Cost Model, shown in the AppLayout tools drawer (`HelpPanel`) when the operator follows
 * an Info link. The Cost Model panel itself keeps labels, values and inputs only.
 *
 * Copy rule (R12.2): sources are named by category, never by vendor, and no vendor price appears.
 */

import React from 'react';
import { HelpPanel } from '@cloudscape-design/components';

import { TOKENIZER_LABEL } from '../../services/comparisonMeasure';

export type CostModelHelpTopic =
  | 'cost_model'
  | 'break_even'
  | 'saving'
  | 'scrape_cost'
  | 'markdown_cost'
  | 'comparator'
  | 'tokens'
  | 'inputs';

interface HelpEntry {
  readonly title: string;
  readonly body: React.ReactNode;
}

const LABELS = (
  <>
    <h3>Evidence labels</h3>
    <ul>
      <li>
        <b>Measured</b>: produced by this run.
      </li>
      <li>
        <b>Assumption</b>: an operator input, named as such.
      </li>
      <li>
        <b>Modelled</b>: computed from labelled inputs. A modelled figure is only as strong as its weakest
        input: one assumption makes the whole figure an assumption.
      </li>
    </ul>
  </>
);

const COST_MODEL_HELP: Readonly<Record<CostModelHelpTopic, HelpEntry>> = {
  cost_model: {
    title: 'Cost Model',
    body: (
      <>
        <p>
          The Cost Model estimates what it costs to obtain 1,000 articles each way: by scraping the page, and by
          fetching the licensed Markdown. It uses the run&apos;s measurements where there are some and your inputs
          for everything else.
        </p>
        <p>
          Prices are operator inputs. None is a vendor quote, and nothing in this repository charges for access, so
          the publisher&apos;s price <code>P</code> is modelled, not measured.
        </p>
        <p>
          Its outcomes (<code>P*</code>, <code>saving(P)</code> and the unblocking comparator) are test outcomes of
          the model. They stay out of the verdict, which shows measured figures only.
        </p>
        <p>Legal or reputational exposure is not expressed as money.</p>
        {LABELS}
      </>
    ),
  },
  break_even: {
    title: 'P*, the break-even price',
    body: (
      <>
        <p>
          <code>P* = scraping cost per 1,000 obtained articles ÷ 1,000</code>: what one article costs a scraper.
        </p>
        <p>
          At a publisher price <code>P</code> below <code>P*</code>, buying the licensed Markdown costs less than
          scraping; at or above it, it costs more, and the panel says so and lists the arguments that are not cost.
        </p>
        <p>
          &quot;Obtained&quot; means an attempt that ends with the article in hand. Failed attempts are paid for too,
          which is why the scraping cost divides by the success rate.
        </p>
      </>
    ),
  },
  saving: {
    title: 'saving(P)',
    body: (
      <>
        <p>
          <code>saving(P) = 1 − P / P*</code>: the share of the scraping cost a buyer saves by paying{' '}
          <code>P</code> per article instead.
        </p>
        <p>
          It is a function of the price, never a fixed percentage. It is negative when <code>P</code> is above{' '}
          <code>P*</code>. Then the remaining arguments are latency, clean Markdown, freshness through a conditional
          request (not measured yet) and licensed provenance.
        </p>
      </>
    ),
  },
  scrape_cost: {
    title: 'Scraping cost per 1,000 obtained articles',
    body: (
      <>
        <p>
          <code>attempts = 1,000 ÷ success rate</code>. The cost is the sum of:
        </p>
        <ul>
          <li>
            <b>Bandwidth</b>: <code>attempts × bytes per attempt ÷ 1 GB × proxy bandwidth price</code>.
          </li>
          <li>
            <b>Challenges</b>: <code>attempts × challenge rate ÷ 1,000 × challenge price per 1,000</code>. Challenges
            are priced as observed, never solved.
          </li>
          <li>
            <b>Compute</b>: <code>attempts × scrape time ÷ 1 hour ÷ browsers per instance × compute price per hour</code>.
          </li>
          <li>
            <b>Cleanup</b>: <code>1,000 × cleanup time ÷ 1 hour ÷ browsers per instance × compute price per hour</code>.
          </li>
          <li>
            <b>Upkeep</b>: <code>fixed upkeep per month ÷ articles per month × 1,000</code>.
          </li>
        </ul>
        <p>Bytes, scrape time and cleanup time come from the run when there is one; the rest are your inputs.</p>
      </>
    ),
  },
  markdown_cost: {
    title: 'Licensed Markdown cost per 1,000 articles',
    body: (
      <>
        <ul>
          <li>
            <b>Delivery</b>:{' '}
            <code>1,000 × Markdown bytes ÷ 1 GB × edge delivery price + 1,000 ÷ 10,000 × edge request price</code>.
          </li>
          <li>
            <b>Licence</b>: <code>1,000 × P</code>.
          </li>
        </ul>
        <p>
          The publisher&apos;s price <code>P</code> dominates. It is the publisher&apos;s choice, and modelled here:
          nothing in this repository charges for access.
        </p>
      </>
    ),
  },
  comparator: {
    title: 'Turnkey unblocking comparator',
    body: (
      <>
        <p>
          The price per successful request of a commercial unblocking service, divided by 1,000 to give a price per
          article.
        </p>
        <p>
          A buyer who would not build a scraper buys this instead, so it is the price the licence has to beat. It is
          an input named by its category; no service is named and none is called.
        </p>
      </>
    ),
  },
  tokens: {
    title: 'Tokens',
    body: (
      <>
        <p>
          Tokens are the units a language model reads and bills. They are counted here with {TOKENIZER_LABEL}, as
          estimates.
        </p>
        <ul>
          <li>
            <b>Tokens handed to the model</b>: the raw HTML document against the Markdown.
          </li>
          <li>
            <b>Tokens after a perfect cleaner</b>: the page&apos;s visible text, the lower bound a cleaning scraper
            reaches.
          </li>
        </ul>
        <p>
          <code>Input-token cost avoided per 1,000 = (HTML tokens − Markdown tokens) × 1,000 ÷ 1,000,000 × model input price per 1M tokens</code>
          .
        </p>
        <p>
          This applies to agent and real-time retrieval use. Training pipelines usually strip HTML first, so it is not a
          training-cost claim.
        </p>
      </>
    ),
  },
  inputs: {
    title: 'Inputs',
    body: (
      <>
        <p>
          <b>Measured inputs</b> come from this run: bytes per scrape attempt, scrape time per attempt, cleanup time
          per article and Markdown bytes. Before a run they are fallback assumptions, and say so.
        </p>
        <p>
          <b>Assumption inputs</b> are yours to edit. Each states its unit and the category of its source. The
          success rate and the challenge rate stay assumptions: a single honest client cannot measure them.
        </p>
        <p>&quot;Reset inputs&quot; restores the defaults.</p>
      </>
    ),
  },
};

export default function CostModelHelp({ topic }: { topic: CostModelHelpTopic }): React.ReactElement {
  const entry = COST_MODEL_HELP[topic];
  return (
    <HelpPanel header={<h2>{entry.title}</h2>}>
      <div data-testid={`cost-model-help-${topic}`}>{entry.body}</div>
    </HelpPanel>
  );
}
