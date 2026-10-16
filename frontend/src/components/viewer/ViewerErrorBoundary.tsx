import { TriangleAlert } from 'lucide-react'
import { Component, type ErrorInfo, type ReactNode } from 'react'

import { Button } from '../common/Button'
import { EmptyState } from '../common/EmptyState'

interface Props {
  children: ReactNode
}

/** Messages browsers use when a dynamic `import()` (code-split chunk) fails to load. */
const CHUNK_LOAD_ERROR =
  /dynamically imported module|importing a module script failed|error loading dynamically imported module|failed to load module script/i

interface State {
  error: Error | null
}

/** DOM-level boundary for the lazy viewer chunk (chunk load failures, WebGL context errors). Key it by model URL to reset. */
export class ViewerErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: unknown): State {
    return { error: error instanceof Error ? error : new Error(String(error)) }
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error('[viewer] render failure', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    const message = this.state.error.message
    // `React.lazy` caches a failed chunk import, so re-rendering cannot recover; only a page reload can
    // (e.g. the backend serving `dist/` was restarted or rebuilt while the page was open).
    const chunkFailed = CHUNK_LOAD_ERROR.test(message)
    const webgl = !chunkFailed && /webgl|context/i.test(message)
    return (
      <EmptyState
        tone="danger"
        icon={<TriangleAlert size={20} />}
        title={chunkFailed ? 'The 3D viewer could not be loaded' : 'The 3D viewer crashed'}
        description={
          chunkFailed
            ? 'The viewer code could not be downloaded from the server. Reload the page once the backend is reachable.'
            : webgl
              ? 'WebGL could not be initialized. Check that hardware acceleration is enabled in your browser.'
              : message.replace(/([?&])token=[^&\s"']+/g, '$1token=***')
        }
        action={
          chunkFailed ? (
            <Button size="sm" onClick={() => window.location.reload()}>
              Reload page
            </Button>
          ) : (
            <Button size="sm" onClick={() => this.setState({ error: null })}>
              Reload viewer
            </Button>
          )
        }
      />
    )
  }
}
