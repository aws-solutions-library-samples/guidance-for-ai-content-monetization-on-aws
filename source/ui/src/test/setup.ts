import '@testing-library/jest-dom'

// Mock import.meta.env
Object.defineProperty(import.meta, 'env', {
  value: {
    VITE_ENABLE_DEMO: 'false',
    VITE_AWS_REGION: 'us-west-2',
    VITE_USER_POOL_ID: 'test-pool-id',
    VITE_USER_POOL_CLIENT_ID: 'test-client-id',
  },
  writable: true
})

// Mock window.location
Object.defineProperty(window, 'location', {
  value: {
    origin: 'http://localhost:3000'
  },
  writable: true
})