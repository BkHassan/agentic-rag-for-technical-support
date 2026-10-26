import { useEffect, useState, type ReactNode } from 'react'
import { Menu, PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { IconButton } from '../ui'
import { useMediaQuery } from '../../lib/useMediaQuery'
import { AppFooter } from './AppFooter'
import { MobileNavDrawer } from './MobileNavDrawer'
import { Sidebar, type NavigationItem } from './Sidebar'
import { TopBar } from './TopBar'
import styles from './DashboardLayout.module.css'

const DESKTOP_QUERY = '(min-width: 960px)'
const COLLAPSED_STORAGE_KEY = 'support-console-sidebar-collapsed'
const SIDEBAR_ID = 'primary-sidebar'
const DRAWER_ID = 'navigation-drawer'

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSED_STORAGE_KEY) === 'true'
  } catch {
    return false
  }
}

interface DashboardLayoutProps {
  pageTitle: string
  navigation: ReadonlyArray<NavigationItem>
  currentNavId: string
  docsUrl: string
  topBarActions?: ReactNode
  sidebarFooter?: (collapsed: boolean) => ReactNode
  children: ReactNode
}

export function DashboardLayout({
  pageTitle,
  navigation,
  currentNavId,
  docsUrl,
  topBarActions,
  sidebarFooter,
  children,
}: DashboardLayoutProps) {
  const isDesktop = useMediaQuery(DESKTOP_QUERY)
  const [collapsed, setCollapsed] = useState(readCollapsed)
  const [drawerOpen, setDrawerOpen] = useState(false)

  useEffect(() => {
    try {
      window.localStorage.setItem(COLLAPSED_STORAGE_KEY, String(collapsed))
    } catch {
      // Not persisted when storage is unavailable.
    }
  }, [collapsed])

  // The drawer only exists on small screens; close it if the viewport grows.
  const drawerVisible = drawerOpen && !isDesktop

  const navigationControl = isDesktop ? (
    <IconButton
      label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
      icon={collapsed ? <PanelLeftOpen aria-hidden="true" /> : <PanelLeftClose aria-hidden="true" />}
      aria-expanded={!collapsed}
      aria-controls={SIDEBAR_ID}
      onClick={() => setCollapsed((value) => !value)}
    />
  ) : (
    <IconButton
      label="Open navigation"
      icon={<Menu aria-hidden="true" />}
      aria-haspopup="dialog"
      aria-expanded={drawerVisible}
      aria-controls={DRAWER_ID}
      onClick={() => setDrawerOpen(true)}
    />
  )

  return (
    <div className={styles.layout} data-collapsed={collapsed}>
      <a href="#main" className={styles.skipLink}>
        Skip to main content
      </a>

      <aside className={styles.sidebarSlot} aria-label="Sidebar">
        <Sidebar
          id={SIDEBAR_ID}
          items={navigation}
          currentId={currentNavId}
          collapsed={collapsed}
          footer={sidebarFooter}
        />
      </aside>

      {!isDesktop ? (
        <MobileNavDrawer id={DRAWER_ID} open={drawerVisible} onClose={() => setDrawerOpen(false)}>
          <Sidebar
            items={navigation}
            currentId={currentNavId}
            footer={sidebarFooter}
            onNavigate={() => setDrawerOpen(false)}
          />
        </MobileNavDrawer>
      ) : null}

      <div className={styles.column}>
        <TopBar title={pageTitle} navigationControl={navigationControl} actions={topBarActions} />
        <main id="main" tabIndex={-1} className={styles.main}>
          <div className={styles.content}>{children}</div>
        </main>
        <AppFooter docsUrl={docsUrl} />
      </div>
    </div>
  )
}
