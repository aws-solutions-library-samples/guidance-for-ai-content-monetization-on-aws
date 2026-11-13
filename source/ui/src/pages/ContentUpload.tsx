import React, { useState } from 'react'
import {
  Container,
  Header,
  SpaceBetween,
  Grid,
  Box,
  Button,
  Form,
  FormField,
  Input,
  Textarea,
  Select,
  DatePicker,
  Toggle,
  Alert,
  ExpandableSection,
  CodeEditor,
  Modal
} from '@cloudscape-design/components'

// IAB Content Categories (standardized format matching OpenAPI spec)
const iabCategories = [
  { label: 'Medical Health', value: 111 },
  { label: 'Technology & Computing', value: 126 },
  { label: 'Business and Finance', value: 52 },
  { label: 'Economy', value: 80 },
  { label: 'Finance', value: 107 },
  { label: 'Arts & Entertainment', value: 150 },
  { label: 'Automotive', value: 1 },
  { label: 'Education', value: 25 },
  { label: 'Sports', value: 100 },
  { label: 'Travel', value: 140 }
]

// Helper function to convert UI selection to standardized format
const convertToStandardizedCategories = (selectedCategories: { value: string | number; label?: string }[]) => {
  return selectedCategories.map(cat => {
    const categoryId = typeof cat.value === 'number' ? cat.value : parseInt(cat.value);
    return {
      id: categoryId,
      name: cat.label,
      tier_1: cat.label,
      tier_2: null,
      tier_3: null,
      tier_4: null,
      parent_id: null
    };
  });
};

const contentTypes = [
  { label: 'Article', value: 'article' },
  { label: 'Blog Post', value: 'blog-post' },
  { label: 'News Article', value: 'news-article' },
  { label: 'Research Paper', value: 'research-paper' },
  { label: 'White Paper', value: 'white-paper' },
  { label: 'Case Study', value: 'case-study' },
  { label: 'Tutorial', value: 'tutorial' },
  { label: 'Review', value: 'review' }
]

const languages = [
  { label: 'English', value: 'en' },
  { label: 'Spanish', value: 'es' },
  { label: 'French', value: 'fr' },
  { label: 'German', value: 'de' },
  { label: 'Italian', value: 'it' },
  { label: 'Portuguese', value: 'pt' },
  { label: 'Chinese (Simplified)', value: 'zh-cn' },
  { label: 'Japanese', value: 'ja' },
  { label: 'Korean', value: 'ko' }
]

export default function ContentUpload() {
  const [formData, setFormData] = useState({
    title: '',
    content: '',
    summary: '',
    author: '',
    publishDate: '',
    category: null,
    contentType: null,
    language: { label: 'English', value: 'en' },
    tags: [],
    isPublic: true,
    allowAITraining: true,
    pricePerAccess: '0.05',
    geographicRestrictions: []
  })

  const [showPreview, setShowPreview] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  const [uploadStatus, setUploadStatus] = useState(null)

  const generateJSON = () => {
    // Convert selected category to standardized IAB format
    const standardizedCategories = formData.category ? 
      convertToStandardizedCategories([formData.category]) : [];
    
    const jsonOutput = {
      metadata: {
        title: formData.title,
        author: formData.author,
        publishDate: formData.publishDate,
        iab_categories: standardizedCategories, // Use standardized format
        contentType: formData.contentType?.value,
        language: formData.language?.value,
        tags: formData.tags.map(tag => tag.value),
        summary: formData.summary,
        pricing: {
          pricePerAccess: parseFloat(formData.pricePerAccess),
          currency: 'USD'
        },
        access: {
          isPublic: formData.isPublic,
          allowAITraining: formData.allowAITraining,
          geographicRestrictions: formData.geographicRestrictions.map(geo => geo.value)
        }
      },
      content: {
        text: formData.content,
        wordCount: formData.content.split(' ').length,
        characterCount: formData.content.length
      },
      timestamp: new Date().toISOString(),
      version: '1.0'
    }
    return JSON.stringify(jsonOutput, null, 2)
  }

  const handleSubmit = async () => {
    setIsUploading(true)
    try {
      // Simulate API call
      await new Promise(resolve => setTimeout(resolve, 2000))
      
      const jsonData = generateJSON()
      
      // In a real implementation, this would upload to S3
      console.log('Generated JSON:', jsonData)
      
      setUploadStatus('success')
      setIsUploading(false)
      
      // Reset form after successful upload
      setTimeout(() => {
        setFormData({
          title: '',
          content: '',
          summary: '',
          author: '',
          publishDate: '',
          category: null,
          contentType: null,
          language: { label: 'English', value: 'en' },
          tags: [],
          isPublic: true,
          allowAITraining: true,
          pricePerAccess: '0.05',
          geographicRestrictions: []
        })
        setUploadStatus(null)
      }, 3000)
      
    } catch {
      setUploadStatus('error')
      setIsUploading(false)
    }
  }

  const isFormValid = formData.title && formData.content && formData.author && formData.category && formData.contentType

  return (
    <SpaceBetween size="l">
      <Header
        variant="h1"
        description="Upload and publish content for AI training access"
        actions={
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={() => setShowPreview(true)} disabled={!isFormValid}>
              Preview JSON
            </Button>
            <Button 
              variant="primary" 
              onClick={handleSubmit}
              disabled={!isFormValid || isUploading}
              loading={isUploading}
            >
              {isUploading ? 'Uploading...' : 'Upload Content'}
            </Button>
          </SpaceBetween>
        }
      >
        Content Upload
      </Header>

      {uploadStatus && (
        <Alert
          type={uploadStatus === 'success' ? 'success' : 'error'}
          dismissible
          onDismiss={() => setUploadStatus(null)}
          header={uploadStatus === 'success' ? 'Upload Successful' : 'Upload Failed'}
        >
          {uploadStatus === 'success' 
            ? 'Your content has been successfully uploaded and is being processed.'
            : 'There was an error uploading your content. Please try again.'
          }
        </Alert>
      )}

      <Form>
        <Container>
          <SpaceBetween size="l">
            <Header variant="h2">Basic Information</Header>
            
            <Grid gridDefinition={[{ colspan: 8 }, { colspan: 4 }]}>
              <SpaceBetween size="m">
                <FormField label="Content Title" constraintText="Required">
                  <Input
                    value={formData.title}
                    onChange={({ detail }) => setFormData({...formData, title: detail.value})}
                    placeholder="Enter a descriptive title for your content"
                  />
                </FormField>

                <FormField label="Author" constraintText="Required">
                  <Input
                    value={formData.author}
                    onChange={({ detail }) => setFormData({...formData, author: detail.value})}
                    placeholder="Author name"
                  />
                </FormField>

                <FormField label="Summary" constraintText="Brief description for discovery">
                  <Textarea
                    value={formData.summary}
                    onChange={({ detail }) => setFormData({...formData, summary: detail.value})}
                    placeholder="Provide a brief summary of the content..."
                    rows={3}
                  />
                </FormField>
              </SpaceBetween>

              <SpaceBetween size="m">
                <FormField label="Category" constraintText="Required - IAB Content Category">
                  <Select
                    selectedOption={formData.category}
                    onChange={({ detail }) => setFormData({...formData, category: detail.selectedOption})}
                    options={iabCategories}
                    placeholder="Select content category"
                  />
                </FormField>

                <FormField label="Content Type" constraintText="Required">
                  <Select
                    selectedOption={formData.contentType}
                    onChange={({ detail }) => setFormData({...formData, contentType: detail.selectedOption})}
                    options={contentTypes}
                    placeholder="Select content type"
                  />
                </FormField>

                <FormField label="Language">
                  <Select
                    selectedOption={formData.language}
                    onChange={({ detail }) => setFormData({...formData, language: detail.selectedOption})}
                    options={languages}
                  />
                </FormField>

                <FormField label="Publish Date">
                  <DatePicker
                    value={formData.publishDate}
                    onChange={({ detail }) => setFormData({...formData, publishDate: detail.value})}
                    placeholder="YYYY-MM-DD"
                  />
                </FormField>
              </SpaceBetween>
            </Grid>
          </SpaceBetween>
        </Container>

        <Container>
          <SpaceBetween size="l">
            <Header variant="h2">Content</Header>
            
            <FormField label="Content Text" constraintText="Required - Main content body">
              <Textarea
                value={formData.content}
                onChange={({ detail }) => setFormData({...formData, content: detail.value})}
                placeholder="Paste or type your content here..."
                rows={15}
              />
            </FormField>

            <Box variant="small" color="text-body-secondary">
              Word count: {formData.content.split(' ').filter(word => word.length > 0).length} | 
              Character count: {formData.content.length}
            </Box>
          </SpaceBetween>
        </Container>

        <ExpandableSection headerText="Access & Pricing Settings" defaultExpanded={false}>
          <Container>
            <SpaceBetween size="m">
              <Grid gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}>
                <SpaceBetween size="m">
                  <FormField label="Price per Access (USD)">
                    <Input
                      type="number"
                      value={formData.pricePerAccess}
                      onChange={({ detail }) => setFormData({...formData, pricePerAccess: detail.value})}
                      step="0.01"
                    />
                  </FormField>

                  <FormField label="Public Access">
                    <Toggle
                      checked={formData.isPublic}
                      onChange={({ detail }) => setFormData({...formData, isPublic: detail.checked})}
                    >
                      Allow public discovery
                    </Toggle>
                  </FormField>
                </SpaceBetween>

                <SpaceBetween size="m">
                  <FormField label="AI Training Permission">
                    <Toggle
                      checked={formData.allowAITraining}
                      onChange={({ detail }) => setFormData({...formData, allowAITraining: detail.checked})}
                    >
                      Allow AI training use
                    </Toggle>
                  </FormField>
                </SpaceBetween>
              </Grid>
            </SpaceBetween>
          </Container>
        </ExpandableSection>
      </Form>

      {/* JSON Preview Modal */}
      <Modal
        onDismiss={() => setShowPreview(false)}
        visible={showPreview}
        closeAriaLabel="Close modal"
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setShowPreview(false)}>
                Close
              </Button>
              <Button 
                variant="primary" 
                onClick={() => {
                  navigator.clipboard.writeText(generateJSON())
                  setShowPreview(false)
                }}
              >
                Copy JSON
              </Button>
            </SpaceBetween>
          </Box>
        }
        header="Generated JSON Preview"
      >
        <SpaceBetween size="m">
          <Alert type="info">
            This is the JSON that will be uploaded to S3 for processing by the MediaLake pipeline.
          </Alert>
          <CodeEditor
            ace={undefined}
            language="json"
            value={generateJSON()}
            readOnly
            preferences={{
              fontSize: 14,
              tabSize: 2
            }}
          />
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  )
}