import { Monitor, Moon, Sun } from 'lucide-react'
import { SegmentedControl, type SegmentedOption } from '../ui/SegmentedControl'
import { useTheme } from '../../theme/useTheme'
import type { ThemePreference } from '../../theme/themeContext'

const OPTIONS: ReadonlyArray<SegmentedOption<ThemePreference>> = [
  { value: 'light', label: 'Light theme', icon: <Sun aria-hidden="true" />, iconOnly: true },
  { value: 'dark', label: 'Dark theme', icon: <Moon aria-hidden="true" />, iconOnly: true },
  { value: 'system', label: 'Match system theme', icon: <Monitor aria-hidden="true" />, iconOnly: true },
]

export function ThemeSwitcher() {
  const { preference, setPreference } = useTheme()
  return (
    <SegmentedControl
      legend="Color theme"
      size="sm"
      options={OPTIONS}
      value={preference}
      onChange={setPreference}
    />
  )
}
