import type { MobileTab } from '../../store/uiStore'

/** DOM ids linking the mobile tab bar (`role="tab"`) with its panels (`role="tabpanel"`). */
export const mobileTabPanelId = (tab: MobileTab) => `mobile-panel-${tab}`
export const mobileTabId = (tab: MobileTab) => `mobile-tab-${tab}`
