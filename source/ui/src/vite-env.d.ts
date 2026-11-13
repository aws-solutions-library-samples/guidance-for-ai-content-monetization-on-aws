/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_SOLUTION_NAME: string
  readonly VITE_ENABLE_DEMO: string
  readonly VITE_AWS_REGION: string
  readonly VITE_USER_POOL_ID: string
  readonly VITE_USER_POOL_CLIENT_ID: string
  readonly VITE_IDENTITY_POOL_ID: string
  readonly VITE_COGNITO_DOMAIN: string
  readonly VITE_API_BASE_URL: string
  readonly VITE_API_VERSION: string
  readonly VITE_ENABLE_ANALYTICS: string
  readonly VITE_ENABLE_DEMO_CONTROLLER: string
  readonly VITE_ENABLE_DEMO_SCENARIOS: string
  readonly VITE_ENABLE_MOCK_DATA: string
  readonly VITE_MEDIALAKE_STACK_NAME: string
  readonly VITE_MEDIALAKE_REGION: string
  readonly VITE_MEDIALAKE_USER_POOL_ID: string
  readonly VITE_MEDIALAKE_USER_POOL_CLIENT_ID: string
  readonly VITE_MEDIALAKE_IDENTITY_POOL_ID: string
  readonly VITE_MEDIALAKE_UI_URL: string
  readonly VITE_COMPARISON_FIXTURE_URL?: string
  readonly VITE_COMPARISON_POLL_LIMIT_MS?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}