import { MessageSquareText } from 'lucide-react'
import { API_DOCS_URL } from './api/config'
import { DashboardLayout } from './components/layout/DashboardLayout'
import type { NavigationItem } from './components/layout/Sidebar'
import { ThemeSwitcher } from './components/layout/ThemeSwitcher'
import { AnalyzePage } from './features/analyze/AnalyzePage'
import { ConnectionIndicator } from './features/status/ConnectionIndicator'
import { SystemStatusPanel } from './features/status/SystemStatusPanel'
import { useApiHealth } from './features/status/useApiHealth'

const NAVIGATION: ReadonlyArray<NavigationItem> = [
  { id: 'analyze', label: 'Analyze Ticket', href: '#analyze', icon: <MessageSquareText aria-hidden="true" /> },
]

export default function App() {
  const health = useApiHealth()

  return (
    <DashboardLayout
      pageTitle="Analyze Ticket"
      navigation={NAVIGATION}
      currentNavId="analyze"
      docsUrl={API_DOCS_URL}
      topBarActions={
        <>
          <ConnectionIndicator health={health} />
          <ThemeSwitcher />
        </>
      }
      sidebarFooter={(collapsed) => <SystemStatusPanel health={health} compact={collapsed} />}
    >
      <AnalyzePage />
    </DashboardLayout>
  )
}
