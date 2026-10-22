import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import './styles/global.css'
import App from './App.tsx'
import { bootDocument } from './app/documentAttributes.ts'

const container = document.getElementById('root')
if (!container) throw new Error('Root element #root not found')

// Mode, density, motion and posture attributes BEFORE the first render (no flash, UI_DESIGN §3.6).
bootDocument()

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
