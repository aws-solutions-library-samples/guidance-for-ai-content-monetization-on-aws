/**
 * Category Multipliers Manager Component
 * 
 * Provides a paginated, searchable interface for managing IAB category multipliers
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
  Pagination,
  TextFilter,
  FormField,
  Modal,
  Alert,
  StatusIndicator
} from '@cloudscape-design/components';

interface CategoryMultiplier {
  categoryId: string;
  categoryName: string;
  multiplier: string;
  fullPath?: string;
  tierLevel?: number;
}

interface CategoryMultipliersManagerProps {
  multipliers: { [key: string]: string };
  categoryMappings: { [key: string]: string };
  onUpdate: (categoryId: string, multiplier: string) => void;
  onBulkUpdate?: (updates: { [key: string]: string }) => void;
  readonly?: boolean;
}

export default function CategoryMultipliersManager({
  multipliers,
  categoryMappings,
  onUpdate,
  onBulkUpdate,
  readonly = false
}: CategoryMultipliersManagerProps) {
  const [currentPageIndex, setCurrentPageIndex] = useState(1);
  const [pageSize] = useState(10);
  const [filteringText, setFilteringText] = useState('');
  const [editingCategory, setEditingCategory] = useState<string | null>(null);
  const [editingValue, setEditingValue] = useState('');
  const [showAddModal, setShowAddModal] = useState(false);
  const [newCategoryId, setNewCategoryId] = useState('');
  const [newMultiplier, setNewMultiplier] = useState('1.0');
  const [bulkEditMode, setBulkEditMode] = useState(false);
  const [selectedItems, setSelectedItems] = useState<CategoryMultiplier[]>([]);

  // Convert multipliers to table data
  const tableData = useMemo(() => {
    const data: CategoryMultiplier[] = [];
    
    // Add existing multipliers
    Object.entries(multipliers).forEach(([categoryId, multiplier]) => {
      data.push({
        categoryId,
        categoryName: categoryMappings[categoryId] || `Category ${categoryId}`,
        multiplier,
        fullPath: categoryMappings[categoryId] || `Category ${categoryId}`,
        tierLevel: 1
      });
    });

    // Add available categories that don't have multipliers yet
    Object.entries(categoryMappings).forEach(([categoryId, categoryName]) => {
      if (!multipliers[categoryId]) {
        data.push({
          categoryId,
          categoryName,
          multiplier: '1.0',
          fullPath: categoryName,
          tierLevel: 1
        });
      }
    });

    return data;
  }, [multipliers, categoryMappings]);

  // Filter and paginate data
  const filteredData = useMemo(() => {
    if (!filteringText) return tableData;
    
    const lowerFilter = filteringText.toLowerCase();
    return tableData.filter(item => 
      item.categoryName.toLowerCase().includes(lowerFilter) ||
      item.categoryId.includes(lowerFilter) ||
      item.fullPath?.toLowerCase().includes(lowerFilter)
    );
  }, [tableData, filteringText]);

  const paginatedData = useMemo(() => {
    const startIndex = (currentPageIndex - 1) * pageSize;
    return filteredData.slice(startIndex, startIndex + pageSize);
  }, [filteredData, currentPageIndex, pageSize]);

  const handleEdit = (categoryId: string, currentValue: string) => {
    setEditingCategory(categoryId);
    setEditingValue(currentValue);
  };

  const handleSaveEdit = () => {
    if (editingCategory && editingValue) {
      onUpdate(editingCategory, editingValue);
      setEditingCategory(null);
      setEditingValue('');
    }
  };

  const handleCancelEdit = () => {
    setEditingCategory(null);
    setEditingValue('');
  };

  const handleAddCategory = () => {
    if (newCategoryId && newMultiplier) {
      onUpdate(newCategoryId, newMultiplier);
      setShowAddModal(false);
      setNewCategoryId('');
      setNewMultiplier('1.0');
    }
  };

  const handleBulkUpdate = () => {
    if (onBulkUpdate && selectedItems.length > 0) {
      const updates: { [key: string]: string } = {};
      selectedItems.forEach(item => {
        updates[item.categoryId] = item.multiplier;
      });
      onBulkUpdate(updates);
      setBulkEditMode(false);
      setSelectedItems([]);
    }
  };

  const columnDefinitions = [
    {
      id: 'categoryId',
      header: 'Category ID',
      cell: (item: CategoryMultiplier) => (
        <Box fontFamily="monospace">{item.categoryId}</Box>
      ),
      sortingField: 'categoryId',
      width: 120
    },
    {
      id: 'categoryName',
      header: 'Category Name',
      cell: (item: CategoryMultiplier) => (
        <Box>
          <div>{item.categoryName}</div>
          {item.fullPath && item.fullPath !== item.categoryName && (
            <Box variant="small" color="text-body-secondary">
              {item.fullPath}
            </Box>
          )}
        </Box>
      ),
      sortingField: 'categoryName'
    },
    {
      id: 'multiplier',
      header: 'Multiplier',
      cell: (item: CategoryMultiplier) => {
        const isEditing = editingCategory === item.categoryId;
        const hasMultiplier = multipliers[item.categoryId];
        
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
              onClick={() => handleEdit(item.categoryId, item.multiplier)}
              ariaLabel={`Edit multiplier for ${item.categoryName}`}
            />
          </SpaceBetween>
        );
      },
      sortingField: 'multiplier',
      width: 200
    },
    {
      id: 'status',
      header: 'Status',
      cell: (item: CategoryMultiplier) => {
        const hasMultiplier = multipliers[item.categoryId];
        return (
          <StatusIndicator type={hasMultiplier ? "success" : "info"}>
            {hasMultiplier ? "Configured" : "Default"}
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
          description="Manage pricing multipliers for IAB content categories"
          counter={`(${filteredData.length})`}
          actions={
            !readonly && (
              <SpaceBetween direction="horizontal" size="xs">
                {bulkEditMode ? (
                  <>
                    <Button onClick={() => setBulkEditMode(false)}>
                      Cancel Bulk Edit
                    </Button>
                    <Button
                      variant="primary"
                      onClick={handleBulkUpdate}
                      disabled={selectedItems.length === 0}
                    >
                      Apply Changes ({selectedItems.length})
                    </Button>
                  </>
                ) : (
                  <>
                    <Button onClick={() => setBulkEditMode(true)}>
                      Bulk Edit
                    </Button>
                    <Button
                      variant="primary"
                      onClick={() => setShowAddModal(true)}
                    >
                      Add Category
                    </Button>
                  </>
                )}
              </SpaceBetween>
            )
          }
        >
          Category Multipliers
        </Header>
      }
    >
      <SpaceBetween size="l">
        <TextFilter
          filteringText={filteringText}
          onChange={({ detail }) => setFilteringText(detail.filteringText)}
          filteringPlaceholder="Search categories..."
          countText={`${filteredData.length} ${filteredData.length === 1 ? 'category' : 'categories'}`}
        />

        <Table
          columnDefinitions={columnDefinitions}
          items={paginatedData}
          selectionType={bulkEditMode ? "multi" : undefined}
          selectedItems={selectedItems}
          onSelectionChange={({ detail }) => setSelectedItems(detail.selectedItems)}
          loadingText="Loading categories..."
          empty={
            <Box textAlign="center" color="inherit">
              <b>No categories found</b>
              <Box variant="p" color="inherit">
                {filteringText ? 'Try adjusting your search criteria.' : 'No categories available.'}
              </Box>
            </Box>
          }
          pagination={
            <Pagination
              currentPageIndex={currentPageIndex}
              onChange={({ detail }) => setCurrentPageIndex(detail.currentPageIndex)}
              pagesCount={Math.ceil(filteredData.length / pageSize)}
              ariaLabels={{
                nextPageLabel: "Next page",
                previousPageLabel: "Previous page",
                pageLabel: (pageNumber) => `Page ${pageNumber} of all pages`
              }}
            />
          }
        />
      </SpaceBetween>

      {/* Add Category Modal */}
      <Modal
        visible={showAddModal}
        onDismiss={() => setShowAddModal(false)}
        header="Add Category Multiplier"
        closeAriaLabel="Close modal"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setShowAddModal(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                onClick={handleAddCategory}
                disabled={!newCategoryId || !newMultiplier}
              >
                Add Category
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Alert type="info">
            Add a custom category multiplier. You can use any IAB category ID or create custom categories.
          </Alert>
          
          <FormField label="Category ID">
            <Input
              value={newCategoryId}
              onChange={({ detail }) => setNewCategoryId(detail.value)}
              placeholder="e.g., 126 or custom-category"
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