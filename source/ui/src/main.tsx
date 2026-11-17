import React from 'react'
import ReactDOM from 'react-dom/client'
import AppInitializer from './components/AppInitializer'
import '@cloudscape-design/global-styles/index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AppInitializer />
  </React.StrictMode>,
)