/**
 * A scenario card on the Test page: title, description, an optional
 * badge, either an action button or embedded content, and an optional note.
 */

import React from 'react';
import { Badge, Box, Button, Container, Header, SpaceBetween } from '@cloudscape-design/components';

export interface ScenarioCardProps {
  readonly title: string;
  readonly description: string;
  readonly badge?: string;
  readonly badgeColor?: 'blue' | 'grey' | 'green' | 'red';
  readonly actionText?: string;
  readonly onAction?: () => void;
  /** Visible caveat, for example that the card's data is mock. */
  readonly note?: string;
  readonly children?: React.ReactNode;
}

export default function ScenarioCard({
  title,
  description,
  badge,
  badgeColor = 'blue',
  actionText,
  onAction,
  note,
  children,
}: ScenarioCardProps): React.ReactElement {
  return (
    <Container
      header={
        <Header variant="h2" description={description} info={badge ? <Badge color={badgeColor}>{badge}</Badge> : undefined}>
          {title}
        </Header>
      }
    >
      <SpaceBetween size="m">
        {note ? <Box color="text-status-warning">{note}</Box> : null}
        {actionText && onAction ? (
          <Button variant="primary" onClick={onAction}>
            {actionText}
          </Button>
        ) : null}
        {children}
      </SpaceBetween>
    </Container>
  );
}
