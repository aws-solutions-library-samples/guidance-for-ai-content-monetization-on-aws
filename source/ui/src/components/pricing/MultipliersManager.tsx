/**
 * Multipliers Manager Component
 * 
 * Generic component for managing different types of multipliers (recency, demand, etc.)
 */

import { useState, useMemo } from 'react';
import {
  Container,
  Header,
  Table,
  Input,
  Button,
  SpaceBetween,
  Box,
  FormField,
  Modal,
  Alert,
  StatusIndicator
} from '@cloudscape-design/components';

interface MultiplierItem {
  key: string;
  displayName: string;
  multiplier: string;
  description?: string;
}

interface MultipliersManagerProps {
  title: string;
  description: string;
  multipliers: { [key: string]: string };
  displayNames: { [key: string]: string };
  descriptions?: { [key: string]: string };
  onUpdate: (key: string, multiplier: string) => void;
  onRemove?: (key: string) => void;
  readonly?: boolean;
  allowCustom?: boolean;
}

export default function MultipliersManager({
  title,
  description,
  multipliers,
  displayNames,
  descriptions = {},
  onUpdate,
  onRemove,
  readonly = false,
  allowCustom = false
}: MultipliersManagerProps) {
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editingValue, setEditingValue] = useState('');
  const [showAddModal, setShowAddModal] = useState(false);
  const [newKey, setNewKey] = useState('');
  const [newDisplayName, setNewDisplayName] = useState('');
  const [newMultiplier, setNewMultiplier] = useState('1.0');

  // Convert multipliers to table data
  const tableData = useMemo(() => {
    const data: MultiplierItem[] = [];
    
    // Add existing multipliers
    Object.entries(multipliers).forEach(([key, multiplier]) => {
      data.push({
        key,
        displayName: displayNames[key] || key.replace(/_/g, ' '),
        multiplier,
        description: descriptions[key]
      });
    });

    // Add predefined keys that don't have multipliers yet
    Object.entries(displayNames).forEach(([key, displayName]) => {
      if (!multipliers[key]) {
        data.push({
          key,
          displayName,
          multiplier: '1.0',
          description: descriptions[key]
        });
      }
    });

    return data.sort((a, b) => a.displayName.localeCompare(b.displayName));
  }, [multipliers, displayNames, descriptions]);

  const handleEdit = (key: string, currentValue: string) => {
    setEditingKey(key);
    setEditingValue(currentValue);
  };

  const handleSaveEdit = () => {
    if (editingKey && editingValue) {
      onUpdate(editingKey, editingValue);
      setEditingKey(null);
      setEditingValue('');
    }
  };

  const handleCancelEdit = () => {
    setEditingKey(null);
    setEditingValue('');
  };

  const handleAddCustom = () => {
    if (newKey && newMultiplier) {
      onUpdate(newKey, newMultiplier);
      setShowAddModal(false);
      setNewKey('');
      setNewDisplayName('');
      setNewMultiplier('1.0');
    }
  };

  const handleRemove = (key: string) => {
    if (onRemove) {
      onRemove(key);
    }
  };

  const columnDefinitions = [
    {
      id: 'displayName',
      header: 'Name',
      cell: (item: MultiplierItem) => (
        <Box>
          <div>{item.displayName}</div>
          {item.description && (
            <Box variant="small" color="text-body-secondary">
              {item.description}
            </Box>
          )}
        </Box>
      ),
      sortingField: 'displayName'
    },
    {
      id: 'key',
      header: 'Key',
      cell: (item: MultiplierItem) => (
        <Box fontFamily="monospace" fontSize="body-s">
          {item.key}
        </Box>
      ),
      sortingField: 'key',
      width: 150
    },
    {
      id: 'multiplier',
      header: 'Multiplier',
      cell: (item: MultiplierItem) => {
        const isEditing = editingKey === item.key;
        const hasMultiplier = multipliers[item.key];
        
        if (readonly) {
          return (
            <Box fontWeight={hasMultiplier ? "bold" : "normal"}>
              {item.multiplier}x
            </Box>
          );
        }

        if (isEditing) {
          return (
            <SpaceBetween direction="horizontal" size="xs">
              <Input
                value={editingValue}
                onChange={({ detail }) => setEditingValue(detail.value)}
                type="number"
                step="0.1"
                placeholder="1.0"
                autoFocus
              />
              <Button
                variant="primary"
                iconName="check"
                onClick={handleSaveEdit}
              />
              <Button
                iconName="close"
                onClick={handleCancelEdit}
              />
            </SpaceBetween>
          );
        }

        return (
          <SpaceBetween direction="horizontal" size="xs">
            <Box fontWeight={hasMultiplier ? "bold" : "normal"}>
              {item.multiplier}x
            </Box>
            <Button
              variant="inline-icon"
              iconName="edit"
              onClick={() => handleEdit(item.key, item.multiplier)}
              ariaLabel={`Edit multiplier for ${item.displayName}`}
            />
            {onRemove && hasMultiplier && !displayNames[item.key] && (
              <Button
                variant="inline-icon"
                iconName="remove"
                onClick={() => handleRemove(item.key)}
                ariaLabel={`Remove ${item.displayName}`}
              />
            )}
          </SpaceBetween>
        );
      },
      sortingField: 'multiplier',
      width: 200
    },
    {
      id: 'status',
      header: 'Status',
      cell: (item: MultiplierItem) => {
        const hasMultiplier = multipliers[item.key];
        const isDefault = displayNames[item.key];
        
        if (hasMultiplier) {
          return (
            <StatusIndicator type="success">
              {isDefault ? "Configured" : "Custom"}
            </StatusIndicator>
          );
        }
        
        return (
          <StatusIndicator type="info">
            Default
          </StatusIndicator>
        );
      },
      width: 120
    }
  ];

  return (
    <Container
      header={
        <Header
          variant="h3"
          description={description}
          counter={`(${tableData.length})`}
          actions={
            !readonly && allowCustom && (
              <Button
                variant="primary"
                onClick={() => setShowAddModal(true)}
              >
                Add Custom
              </Button>
            )
          }
        >
          {title}
        </Header>
      }
    >
      <Table
        columnDefinitions={columnDefinitions}
        items={tableData}
        loadingText="Loading multipliers..."
        empty={
          <Box textAlign="center" color="inherit">
            <b>No multipliers configured</b>
            <Box variant="p" color="inherit">
              Configure multipliers to adjust pricing based on {title.toLowerCase()}.
            </Box>
          </Box>
        }
      />

      {/* Add Custom Multiplier Modal */}
      <Modal
        visible={showAddModal}
        onDismiss={() => setShowAddModal(false)}
        header={`Add Custom ${title.slice(0, -1)}`}
        closeAriaLabel="Close modal"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setShowAddModal(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                onClick={handleAddCustom}
                disabled={!newKey || !newMultiplier}
              >
                Add Multiplier
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Alert type="info">
            Add a custom multiplier for specific use cases not covered by the default options.
          </Alert>
          
          <FormField label="Key" description="Internal identifier (use lowercase with underscores)">
            <Input
              value={newKey}
              onChange={({ detail }) => setNewKey(detail.value)}
              placeholder="e.g., premium_content"
            />
          </FormField>
          
          <FormField label="Display Name" description="Human-readable name">
            <Input
              value={newDisplayName}
              onChange={({ detail }) => setNewDisplayName(detail.value)}
              placeholder="e.g., Premium Content"
            />
          </FormField>
          
          <FormField label="Multiplier">
            <Input
              value={newMultiplier}
              onChange={({ detail }) => setNewMultiplier(detail.value)}
              type="number"
              step="0.1"
              placeholder="1.0"
            />
          </FormField>
        </SpaceBetween>
      </Modal>
    </Container>
  );
}