# Publisher Content Monetization for AI Agents — Demo UI

This is the demo user interface for the Publisher Content Monetization for AI Agents framework, built with React and AWS Cloudscape Design System.

## Features

### Core Features (Always Available)
- **Unified Publisher Dashboard**: Advanced analytics with interactive Sankey chart visualizations
  - **Bot Activity Flow**: Visual representation of bot traffic and security outcomes
  - **Content Performance Flow**: Content category performance and revenue attribution
  - **Partner Analytics Flow**: AI partner usage patterns and revenue generation
- **Content Upload**: Intuitive interface for publishers to upload and manage content
- **Analytics Dashboard**: Real-time monitoring, bot activity analysis, and system metrics

### Demo Features (Configurable)
- **AI Agent Simulator**: Simulate AI bot interactions and content discovery workflows
- **Demo Controller**: Interactive demonstrations of the complete system workflow
- **Demo Scenarios**: Step-by-step guided demonstrations
- **Mock Data**: Sample data for demonstration purposes

## Feature Flags

This application uses feature flags to control demo functionality, making it suitable for both production deployments and demonstration purposes.

### Configuration

Feature flags are controlled via environment variables:

- `VITE_ENABLE_DEMO=true/false` - Master switch for all demo features
- `VITE_ENABLE_DEMO_CONTROLLER=true/false` - Enable/disable Demo Controller page
- `VITE_ENABLE_DEMO_SCENARIOS=true/false` - Enable/disable demo scenarios
- `VITE_USE_MOCK_DATA=true/false` - Enable/disable mock data (demo-toggle gated at runtime)

### Deployment Modes

1. **Production Mode** (`VITE_ENABLE_DEMO=false`)
   - Only core publisher and analytics features
   - No demo-specific navigation or components
   - Suitable for customer deployments

2. **Demo Mode** (`VITE_ENABLE_DEMO=true`)
   - All features enabled including demo components
   - Suitable for AWS field demonstrations
   - Interactive demo scenarios available

## Tech Stack

- **React 18** with TypeScript
- **AWS Cloudscape Design System** for UI components
- **Vite** for fast development and building
- **React Router** for navigation
- **Recharts** for basic charts and **D3.js + d3-sankey** for advanced Sankey visualizations
- **Axios** for API communication

## Getting Started

### Prerequisites

- Node.js 16+
- npm or yarn

### Recommended: build via the Makefile (what CI uses)

From the **repository root**, `make build-ui` generates `source/ui/.env` from the
deployed CDK stack outputs and then runs the production build:

```bash
make build-ui          # setup-ui-env (writes source/ui/.env) + npm install + npm run build
make deploy-ui-stack   # cdk deploy CMUIStack (pushes dist/ to S3 + CloudFront)
```

`setup-ui-env` also derives the UI display name from `solution-manifest.yaml`
(`name:`), stripping the `Guidance for ` prefix and ` on AWS` suffix into
`VITE_SOLUTION_NAME`. If that var is unset, the UI falls back to the built-in
name in `src/config/solutionName.ts`.

### Manual local development / build

1. Navigate to the UI directory:
   ```bash
   cd source/ui
   ```

2. Install dependencies:
   ```bash
   npm install
   ```

3. Configure environment variables:
   ```bash
   # Copy the example environment file
   cp .env.example .env

   # Edit .env with your configuration:
   #   VITE_API_BASE_URL=https://your-api-gateway-url.amazonaws.com
   #   VITE_ENABLE_DEMO=true   (demo features) | false (production)
   #   VITE_SOLUTION_NAME=...   (optional; overrides the built-in name)
   ```

4. Start the development server:
   ```bash
   npm run dev
   ```

5. Open [http://localhost:3000](http://localhost:3000) in your browser

### Building

```bash
# Standard build (uses the current .env)
npm run build
```

The built files will be in the `dist` directory. (`npm run build:demo` /
`build:production` force the demo flag on/off via Vite `--mode`; the Makefile
path uses `npm run build` with `VITE_ENABLE_DEMO` set in the generated `.env`.)

## Project Structure

```
ui/
├── src/
│   ├── pages/           # Page components
│   │   ├── UnifiedDashboard.tsx    # Advanced Sankey chart analytics
│   │   ├── AddContent.tsx          # Unified content addition interface
│   │   ├── ContentUpload.tsx       # Direct content upload component
│   │   ├── SystemStatus.tsx        # Business-focused processing status
│   │   ├── SystemManagement.tsx    # Technical system monitoring
│   │   ├── BotControl.tsx          # Bot control management
│   │   ├── AIAgentSimulator.tsx    # AI agent testing (feature-flagged)
│   │   └── DemoController.tsx      # Demo scenarios (feature-flagged)
│   ├── App.tsx          # Main app component with routing
│   └── main.tsx         # Entry point
├── package.json
├── vite.config.ts
└── tsconfig.json
```

## Available Scripts

- `npm run dev` - Start development server
- `npm run build` - Build for production
- `npm run preview` - Preview production build
- `npm run lint` - Run ESLint

## Integration with Backend

The UI is designed to work with the AI Content Monetization API backend. Key integration points:

### API Endpoints
- `POST /api/content` - Upload content (ContentUpload page)
- `GET /api/content` - List content (PublisherDashboard)
- `POST /query` - Content discovery (the AI agent simulator uses mock results)
- `GET /api/analytics` - System metrics (AnalyticsDashboard)
- `GET /api/info` - System information

### Authentication
The UI expects JWT tokens for authentication. Update the axios configuration to include:
```typescript
axios.defaults.headers.common['Authorization'] = `Bearer ${token}`
```

### Real-time Updates
For real-time analytics, consider implementing WebSocket connections or Server-Sent Events.

## Deployment

### AWS S3 + CloudFront

1. Build the project:
   ```bash
   npm run build
   ```

2. Upload the `dist` folder to an S3 bucket configured for static website hosting

3. Configure CloudFront distribution for the S3 bucket

4. Update CORS settings on your API Gateway to allow the CloudFront domain

### AWS Amplify

1. Connect your repository to AWS Amplify

2. Configure build settings:
   ```yaml
   version: 1
   frontend:
     phases:
       preBuild:
         commands:
           - cd ui && npm install
       build:
         commands:
           - cd ui && npm run build
     artifacts:
       baseDirectory: ui/dist
       files:
         - '**/*'
   ```

## Advanced Visualizations

### Sankey Chart Implementation

The Unified Dashboard features advanced Sankey chart visualizations built with D3.js that provide deep insights into data flows:

#### Key Features
- **Interactive Flow Diagrams**: Visual representation of data flowing from sources to outcomes
- **Three-Column Layout**: Sources (left) → Consolidation Hub (middle) → Outcomes (right)
- **Color-Coded Flows**: Consistent color coding maintained throughout the entire flow
- **Hover Interactions**: Detailed tooltips with metrics, percentages, and flow information
- **Responsive Design**: Optimized for different screen sizes with proper margin handling

#### Chart Types
1. **Bot Activity Flow**: Shows how different bot types contribute to revenue or get blocked
2. **Content Performance Flow**: Visualizes how content categories generate API requests and revenue
3. **Partner Analytics Flow**: Displays AI partner usage patterns and revenue attribution

#### Technical Implementation
- **D3.js Integration**: Uses d3-sankey for professional-grade flow diagrams
- **React Hooks**: Leverages useRef and useEffect for seamless React-D3 integration
- **TypeScript Support**: Fully typed interfaces for data models and component props
- **Performance Optimized**: Efficient rendering with proper cleanup and memory management

#### Data Model Structure
```typescript
interface SankeyData {
  nodes: Array<{
    name: string;
    color: string;
    value?: number;
    revenue?: number;
  }>;
  links: Array<{
    source: number;
    target: number;
    value: number;
    revenue?: number;
    color: string;
  }>;
}
```

## Customization

### Theming
The app uses AWS Cloudscape Design System. You can customize the theme by modifying the global styles import in `main.tsx`.

### Adding New Pages
1. Create a new component in `src/pages/`
2. Add the route to `App.tsx`
3. Update the navigation items in `App.tsx`

### Mock Data
Currently, the UI uses mock data for demonstration. Replace the mock data with actual API calls:

1. Update the data fetching logic in each component
2. Add proper error handling
3. Implement loading states
4. Add data refresh capabilities

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## License

This project is licensed under the MIT License.