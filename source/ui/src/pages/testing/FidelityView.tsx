/**
 * Content fidelity tab (spec scrape-vs-api-comparison R14, design §3c).
 *
 * Answers the publisher's question behind the gains headline: "did the Markdown keep my
 * article?" The verdict comes first, in the buyer's terms (R14.12): a quality score, how many
 * sentences the agent receives are not the article, how many article sentences are missing, and
 * whether the title is the headline. Beneath it: one "Not the article" list with each item's
 * origin, the article text beside the rendered Markdown, and a structure comparison. Everything
 * is computed in the browser from data the run already returned; raw HTML is never rendered as a
 * page (R14.1), and react-markdown's default (no raw HTML) keeps publisher content inert.
 */
import React, { useMemo, useState } from 'react';
import {
  Alert,
  Badge,
  Box,
  Button,
  ColumnLayout,
  Container,
  ExpandableSection,
  Header,
  SpaceBetween,
  StatusIndicator,
  Table,
  TextContent,
  type BadgeProps,
} from '@cloudscape-design/components';
import ReactMarkdown, { type Components } from 'react-markdown';
import {
  buildFidelityReport,
  coverageStatus,
  leakageStatus,
  schemaTitleLine,
  scoreStatus,
  type AddedByDesign,
  type FidelityReport,
  type FidelityStatus,
  type NotArticleOrigin,
  type StructureFlag,
  type TitleNotHeadline,
} from '../../services/comparisonFidelity';

export const UNMATCHED_LIST_CAP = 25;

interface FidelityViewProps {
  readonly html: string | null;
  readonly markdown: string | null;
  /** 'pending' while Path B is still ingesting; 'failed' on precondition failure. */
  readonly markdownState: 'ready' | 'pending' | 'failed';
  /** The run reused the catalogue record: the Markdown comes from an earlier ingest (R14.11). */
  readonly reused?: boolean;
}

function describeAddition(item: AddedByDesign): string {
  switch (item.kind) {
    case 'document_title':
      return `Document title, from the page's ${item.source} (the pipeline always opens with a title heading)`;
    case 'image_alt':
      return 'Image alt text (the pipeline keeps an image as its alt text)';
    case 'metadata':
      return 'Metadata block (the pipeline lists the author, publisher, dates and source under the title)';
    case 'generated_summary':
      return 'Generated summary, labelled as generated in the Markdown';
    case 'generated_faq':
      return 'Generated FAQ, labelled as generated and not part of the original article';
    case 'attribution':
      return 'Attribution line (the pipeline closes with the source credit)';
  }
}

/** Origin labels of the "Not the article" list (R14.12). */
const ORIGIN_BADGES: Readonly<Record<NotArticleOrigin, { readonly text: string; readonly color: BadgeProps['color']; readonly note: string }>> = {
  boilerplate: {
    text: 'boilerplate',
    color: 'blue',
    note: 'page boilerplate the cleanup removes; the pipeline kept it',
  },
  unknown: {
    text: 'unknown',
    color: 'grey',
    note: 'found neither in the article nor in the boilerplate around it',
  },
};

function pct(value: number | null): string {
  return value === null ? 'n/a' : `${(value * 100).toFixed(1)}%`;
}

/** R14.15: ", the byline and the date line carried by the metadata block", or "" when there are none. */
function carriedByMetadata(bylines: number, dates: number): string {
  const parts = [bylines > 0 ? 'the byline' : '', dates > 0 ? 'the date line' : ''].filter(Boolean);
  return parts.length > 0 ? `, ${parts.join(' and ')} carried by the metadata block` : '';
}

function statusColor(status: FidelityStatus | null): 'text-status-success' | 'text-status-warning' | 'text-status-error' | 'text-status-inactive' {
  if (status === 'good') return 'text-status-success';
  if (status === 'warning') return 'text-status-warning';
  if (status === 'poor') return 'text-status-error';
  return 'text-status-inactive';
}

function describeFlag(flag: StructureFlag): string {
  switch (flag.kind) {
    case 'duplicate_heading':
      return `Heading repeated in the Markdown: "${flag.text}" (the pipeline often emits the title as both H1 and H2).`;
    case 'single_cell_table':
      return `${flag.text} has a single cell: likely a page widget that survived conversion, not article content.`;
    case 'missing_structure':
      return `The HTML article has ${flag.what} but the Markdown has none.`;
    case 'escaped_punctuation_in_heading':
      return `Escaped punctuation in a heading: "${flag.text}".`;
    default:
      return 'Unknown flag';
  }
}

function describeTitleFinding(finding: TitleNotHeadline): string {
  return `The Markdown title is "${finding.title}"${finding.source ? ` (from the page's ${finding.source})` : ''}, not the article headline "${finding.headline}".`;
}

interface ListItem {
  readonly text: string;
  /** Shown after the text, e.g. why it is listed. */
  readonly note?: string;
  /** Short origin label shown before the text. */
  readonly badge?: { readonly text: string; readonly color: BadgeProps['color'] };
}

function UnmatchedList({
  title,
  items,
  emptyText,
  intro,
}: {
  title: string;
  items: readonly (string | ListItem)[];
  emptyText: string;
  intro?: string;
}): React.ReactElement {
  const shown = items.slice(0, UNMATCHED_LIST_CAP).map((item) => (typeof item === 'string' ? { text: item } : item));
  const rest = items.length - shown.length;
  return (
    <ExpandableSection headerText={`${title} (${items.length})`} variant="footer">
      {items.length === 0 ? (
        <Box color="text-status-success">{emptyText}</Box>
      ) : (
        <SpaceBetween size="xxs">
          {intro ? (
            <Box variant="small" color="text-body-secondary">
              {intro}
            </Box>
          ) : null}
          {shown.map((item, i) => (
            <Box key={i} variant="p">
              {item.badge ? (
                <>
                  <Badge color={item.badge.color}>{item.badge.text}</Badge>{' '}
                </>
              ) : null}
              {item.text}
              {item.note ? (
                <Box variant="small" color="text-body-secondary" display="inline">
                  {` — ${item.note}`}
                </Box>
              ) : null}
            </Box>
          ))}
          {rest > 0 ? <Box color="text-body-secondary">+{rest} more</Box> : null}
        </SpaceBetween>
      )}
    </ExpandableSection>
  );
}

const HEADING_LABELS = ['H1', 'H2', 'H3', 'H4', 'H5', 'H6'];

interface StructureRow {
  readonly what: string;
  readonly html: number;
  readonly markdown: number;
}

function StructureTable({ report }: { report: FidelityReport }): React.ReactElement {
  const { html, markdown } = report.structure;
  const rows: StructureRow[] = [
    ...HEADING_LABELS.map((label, i) => ({ what: `Headings ${label}`, html: html.headings[i], markdown: markdown.headings[i] })).filter(
      (r) => r.html > 0 || r.markdown > 0,
    ),
    { what: 'Links', html: html.links, markdown: markdown.links },
    { what: 'Images', html: html.images, markdown: markdown.images },
    { what: 'Tables', html: html.tables, markdown: markdown.tables },
    { what: 'Emphasis runs', html: html.emphasis, markdown: markdown.emphasis },
    { what: 'Code', html: html.code, markdown: markdown.code },
  ];
  return (
    <Table
      variant="embedded"
      items={rows}
      trackBy={(row) => row.what}
      ariaLabels={{ tableLabel: 'Structure comparison' }}
      columnDefinitions={[
        { id: 'what', header: 'Element', isRowHeader: true, cell: (r) => r.what },
        { id: 'html', header: 'HTML article', cell: (r) => String(r.html) },
        { id: 'markdown', header: 'Markdown', cell: (r) => String(r.markdown) },
        {
          id: 'delta',
          header: 'Difference',
          cell: (r) => {
            const d = r.markdown - r.html;
            if (d === 0) return <StatusIndicator type="success">same</StatusIndicator>;
            return (
              <StatusIndicator type={d < 0 && r.html > 0 && r.markdown === 0 ? 'error' : 'warning'}>
                {d > 0 ? `+${d}` : String(d)}
              </StatusIndicator>
            );
          },
        },
      ]}
    />
  );
}

/** One verdict figure: the headline value, what it counts, and how it was derived. */
function VerdictFigure({
  label,
  value,
  status,
  detail,
  testId,
}: {
  label: string;
  value: string;
  status: FidelityStatus | null;
  detail: React.ReactNode;
  testId: string;
}): React.ReactElement {
  return (
    <div>
      <Box variant="awsui-key-label">{label}</Box>
      <Box variant="h1" color={statusColor(status)} data-testid={testId}>
        {value}
      </Box>
      <Box variant="small" color="text-body-secondary">
        {detail}
      </Box>
    </div>
  );
}

/** The flag shown next to the rendered title H1 (R14.13): where the title came from, and whether it is the headline. */
function TitleFlags({ report }: { report: FidelityReport }): React.ReactElement {
  return (
    <span data-testid="markdown-title-source">
      <SpaceBetween direction="horizontal" size="xs" alignItems="center">
        <Badge color={report.titleSource ? 'blue' : 'grey'}>
          {report.titleSource ? `Title from the page's ${report.titleSource}` : 'Title not found on the page'}
        </Badge>
        {report.titleFinding ? (
          <StatusIndicator type="warning">Not the article headline</StatusIndicator>
        ) : report.titleIsHeadline ? (
          <StatusIndicator type="success">The article headline</StatusIndicator>
        ) : null}
      </SpaceBetween>
    </span>
  );
}

/** react-markdown components: the schema title's H1 (found by source line) carries the title flags. */
function markdownComponents(report: FidelityReport, titleLine: number | null): Components {
  return {
    h1: ({ node, children, ...rest }) => {
      const heading = <h1 {...rest}>{children}</h1>;
      if (titleLine === null || node?.position?.start.line !== titleLine) return heading;
      return (
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', columnGap: '8px' }}>
          {heading}
          <TitleFlags report={report} />
        </div>
      );
    },
  };
}

export default function FidelityView({ html, markdown, markdownState, reused = false }: FidelityViewProps): React.ReactElement {
  const [showSource, setShowSource] = useState(false);
  const report = useMemo(() => (html && markdown && markdownState === 'ready' ? buildFidelityReport(html, markdown) : null), [html, markdown, markdownState]);
  const components = useMemo(() => (report && markdown ? markdownComponents(report, schemaTitleLine(markdown)) : undefined), [report, markdown]);

  if (!html) {
    return <Box color="text-status-inactive">Run a URL to compare the article text with the licensed Markdown.</Box>;
  }

  if (!report) {
    // No comparison is fabricated (R14.6): show the article side alone with the Markdown state.
    const articleOnly = buildFidelityReport(html, '');
    return (
      <SpaceBetween size="l">
        <Alert type={markdownState === 'failed' ? 'warning' : 'info'}>
          {markdownState === 'failed'
            ? 'No Markdown to compare yet: the pipeline is still publishing this page (the licensed Markdown side is still processing).'
            : 'Markdown pending: the pipeline has not published this page yet. The article text is shown; the comparison follows once the Markdown is ready.'}
        </Alert>
        <Container header={<Header variant="h3">Article text (from the HTML)</Header>}>
          <ArticleParagraphs text={articleOnly.articleSide} />
        </Container>
      </SpaceBetween>
    );
  }

  const cov = report.coverage;
  const missing = cov.articleSentences - cov.articleMatched;
  const missingShare = cov.coverage === null ? null : 1 - cov.coverage;
  const boilerplateCount = cov.notArticle.filter((item) => item.origin === 'boilerplate').length;
  const notArticle: ListItem[] = cov.notArticle.map((item) => ({
    text: item.text,
    badge: ORIGIN_BADGES[item.origin],
    note: ORIGIN_BADGES[item.origin].note,
  }));
  const added: ListItem[] = cov.addedByDesign.map((item) => ({ text: item.text, note: describeAddition(item) }));

  return (
    <SpaceBetween size="l" data-testid="fidelity-view">
      <Container
        header={
          <Header
            variant="h2"
            description="What the agent receives, against the article on the page. Sentence-level, measured on normalised text so formatting differences are not counted as loss."
          >
            Content fidelity
          </Header>
        }
      >
        <SpaceBetween size="m">
          <div data-testid="fidelity-verdict">
            <ColumnLayout columns={3} variant="text-grid">
              <VerdictFigure
                label="Quality score"
                value={report.score === null ? 'n/a' : `${report.score} / 100`}
                status={scoreStatus(report.score)}
                testId="fidelity-score"
                detail={`100 − ${pct(cov.leakage ?? 0)} not the article − ${pct(missingShare)} missing, from 0 to 100. Measured.`}
              />
              <VerdictFigure
                label="Not the article"
                value={`${cov.markdownUnmatched} of ${cov.markdownSentences}`}
                status={leakageStatus(cov.leakage)}
                testId="fidelity-not-article-count"
                detail={
                  <>
                    <span data-testid="fidelity-not-article-sentence">
                      {cov.markdownUnmatched} of {cov.markdownSentences} sentences the agent receives are not the article
                    </span>{' '}
                    (leakage <span data-testid="fidelity-leakage">{pct(cov.leakage)}</span>
                    {boilerplateCount > 0 ? `; ${boilerplateCount} listed items are page boilerplate the cleanup removes` : ''}). What the
                    pipeline adds by design (the title, the metadata block, the generated summary and FAQ, the attribution line,
                    image alt text) is not counted. Measured.
                  </>
                }
              />
              <VerdictFigure
                label="Missing from the Markdown"
                value={`${missing} of ${cov.articleSentences}`}
                status={coverageStatus(cov.coverage)}
                testId="fidelity-missing-count"
                detail={
                  <>
                    <span data-testid="fidelity-missing-sentence">
                      {missing} article {missing === 1 ? 'sentence is' : 'sentences are'} missing
                    </span>{' '}
                    (coverage <span data-testid="fidelity-coverage">{pct(cov.coverage)}</span>: {cov.articleMatched} of{' '}
                    {cov.articleSentences} kept, {cov.exactMatches} exact, {cov.fuzzyMatches} re-flowed
                    {cov.titleMatches > 0 ? `, ${cov.titleMatches} headline inside the title` : ''}
                    {carriedByMetadata(cov.bylineMatches, cov.dateMatches)}). Measured.
                  </>
                }
              />
            </ColumnLayout>
          </div>
          {report.titleFinding ? (
            <Alert type="warning" header="The title is not the headline" data-testid="fidelity-title-finding">
              {describeTitleFinding(report.titleFinding)} An agent reads this line as the article title.
            </Alert>
          ) : null}
          {reused ? (
            <Alert type="info" data-testid="fidelity-reused">
              This Markdown is your catalogue record, published from an earlier ingest, not from this fetch. A difference can be an
              edit between the two versions rather than pipeline loss.
            </Alert>
          ) : null}
          {report.articleFallback ? (
            <Alert type="info">
              The page has no {'<article>'} or {'<main>'} element, so the whole page text is the reference. Leakage is overstated for such pages.
            </Alert>
          ) : null}
          <UnmatchedList
            title="Not the article"
            items={notArticle}
            emptyText="The Markdown contains nothing that is not in the article."
            intro="What the agent receives that is not the article, each with its origin: boilerplate (page furniture the cleanup removes, which the pipeline kept) or unknown. Fragments under four words are listed but not counted."
          />
          <UnmatchedList title="Missing from the Markdown" items={cov.missingFromMarkdown} emptyText="Nothing from the article is missing." />
          <UnmatchedList
            title="Added by the pipeline by design (not counted)"
            items={added}
            emptyText="The pipeline added nothing beyond the article."
          />
          <UnmatchedList
            title="Excluded from the article reference (boilerplate)"
            items={report.excludedFromArticle}
            emptyText="The cleanup removed nothing from the article region."
          />
        </SpaceBetween>
      </Container>

      <Container
        header={
          <Header
            variant="h2"
            description="Left: the article region a cleaning scraper extracts from the HTML, boilerplate removed. Right: the licensed Markdown as a reader sees it. The raw HTML page is not shown; it is chrome around the article."
            actions={
              <Button onClick={() => setShowSource((s) => !s)} data-testid="toggle-markdown-source">
                {showSource ? 'Show rendered' : 'Show source'}
              </Button>
            }
          >
            Side by side
          </Header>
        }
      >
        <ColumnLayout columns={2}>
          <div data-testid="fidelity-article">
            <Box variant="h3">Article text (from the HTML)</Box>
            <ArticleParagraphs text={report.articleSide} />
          </div>
          <div data-testid="fidelity-markdown">
            <Box variant="h3">Licensed Markdown</Box>
            {showSource ? (
              <Box variant="code">
                <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: 0 }}>{markdown}</pre>
              </Box>
            ) : (
              <div style={{ overflowWrap: 'anywhere' }} data-testid="fidelity-rendered-markdown">
                <TextContent>
                  <ReactMarkdown components={components}>{markdown ?? ''}</ReactMarkdown>
                </TextContent>
              </div>
            )}
          </div>
        </ColumnLayout>
      </Container>

      <Container header={<Header variant="h2" description="Counts on each side, and artefacts the conversion introduced.">Structure</Header>}>
        <SpaceBetween size="m">
          <StructureTable report={report} />
          {report.structure.flags.length === 0 ? (
            <StatusIndicator type="success">No structural artefacts flagged.</StatusIndicator>
          ) : (
            <SpaceBetween size="xs">
              {report.structure.flags.map((flag, i) => (
                <StatusIndicator key={i} type="warning">
                  {describeFlag(flag)}
                </StatusIndicator>
              ))}
            </SpaceBetween>
          )}
        </SpaceBetween>
      </Container>
    </SpaceBetween>
  );
}

function ArticleParagraphs({ text }: { text: string }): React.ReactElement {
  const blocks = text.split('\n').filter((b) => b.trim().length > 0);
  if (blocks.length === 0) return <Box color="text-status-inactive">No article text found.</Box>;
  return (
    <SpaceBetween size="xs">
      {blocks.map((block, i) => (
        <Box key={i} variant="p">
          {block}
        </Box>
      ))}
    </SpaceBetween>
  );
}
