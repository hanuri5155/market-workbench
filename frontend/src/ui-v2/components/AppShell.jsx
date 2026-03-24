// frontend/src/ui-v2/components/AppShell.jsx

import { useEffect, useState } from "react";
import { Badge } from "./primitives";
import useLiveTicker from "../hooks/useLiveTicker";

const shellTabs = [
  // v0.4.3 공개 스냅샷에서 실제 데이터 경로가 연결된 화면은 Chart다.
  // 채용 담당자가 첫 진입에서 mock 화면을 먼저 보지 않도록 루트 경로를 차트 탭에 배정한다.
  { key: "chart", label: "Chart", segment: "", icon: "chart" },
  { key: "control", label: "Control Center", segment: "/control", icon: "control" },
  { key: "analytics", label: "Stats", segment: "/stats", icon: "analytics" },
  { key: "settings", label: "Settings", segment: "/settings", icon: "settings" },
];

const SIDEBAR_STORAGE_KEY = "marketWorkbench.uiV2.sidebarCollapsed";

function getStoredSidebarState() {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

function normalizeBasePath(basePath) {
  if (!basePath || basePath === "/") return "";
  return basePath.endsWith("/") ? basePath.slice(0, -1) : basePath;
}

function stripBasePath(pathname, basePath) {
  const normalizedBase = normalizeBasePath(basePath);
  if (!normalizedBase) return pathname;
  if (!pathname.startsWith(normalizedBase)) return pathname;
  return pathname.slice(normalizedBase.length) || "/";
}

export function getUiV2TabFromPath(pathname, basePath = "/ui-preview") {
  const relativePath = stripBasePath(pathname, basePath);
  if (relativePath.startsWith("/control")) return "control";
  if (relativePath.startsWith("/settings")) return "settings";
  if (relativePath.startsWith("/stats") || relativePath.startsWith("/analytics")) return "analytics";
  if (relativePath.startsWith("/chart")) return "chart";
  return "chart";
}

export function getUiV2PathForTab(key, basePath = "/ui-preview") {
  const normalizedBase = normalizeBasePath(basePath);
  const segment = shellTabs.find((item) => item.key === key)?.segment ?? "";
  return `${normalizedBase}${segment}` || "/";
}

function SidebarToggleIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <rect x="3.5" y="4" width="17" height="16" rx="3" />
      <path d="M9 4v16" />
      <path d="M15 10l2 2-2 2" />
    </svg>
  );
}

function TabIcon({ type }) {
  if (type === "chart") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="M4 18h16" />
        <path d="M6 15l3-4 3 2 5-7" />
        <path d="M17 6h2v2" />
      </svg>
    );
  }

  if (type === "analytics") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="M5 19V9" />
        <path d="M12 19V5" />
        <path d="M19 19v-7" />
      </svg>
    );
  }

  if (type === "settings") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="M12 8.2a3.8 3.8 0 1 0 0 7.6 3.8 3.8 0 0 0 0-7.6Z" />
        <path d="M12 3.8v2.1M12 18.1v2.1M5.2 5.2l1.5 1.5M17.3 17.3l1.5 1.5M3.8 12h2.1M18.1 12h2.1M5.2 18.8l1.5-1.5M17.3 6.7l1.5-1.5" />
      </svg>
    );
  }

  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M5 12a7 7 0 1 1 14 0" />
      <path d="M12 5v7l4 2" />
      <path d="M4 18h16" />
    </svg>
  );
}

export default function AppShell({
  activeTab,
  basePath = "/ui-preview",
  preview = false,
  onTabChange,
  children,
}) {
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(getStoredSidebarState);
  const ticker = useLiveTicker();
  const tickerTone =
    ticker.direction === "up" ? "up" :
    ticker.direction === "down" ? "down" : "flat";
  const statusLabel =
    ticker.status === "live"
      ? "Live price"
      : ticker.status === "connecting"
        ? "Connecting"
        : "Waiting for feed";
  const rootClasses = [
    "ui-v2-root",
    `ui-v2-root--${activeTab}`,
    isSidebarCollapsed ? "is-sidebar-collapsed" : "",
  ].filter(Boolean).join(" ");
  const toggleSidebar = () => setIsSidebarCollapsed((current) => !current);

  useEffect(() => {
    try {
      // 공개 포트폴리오 화면에서도 사이드바 접힘 상태가 유지되도록 브라우저 로컬 저장소에만 기록한다.
      window.localStorage.setItem(SIDEBAR_STORAGE_KEY, String(isSidebarCollapsed));
    } catch {
      // Sidebar persistence is optional; the interaction should still work.
    }
  }, [isSidebarCollapsed]);

  return (
    <div className={rootClasses}>
      <div className="ui-v2-ambient" aria-hidden="true" />
      <aside id="ui-v2-sidebar" className="ui-v2-sidebar">
        <div className="ui-v2-sidebar-head">
          <div className="ui-v2-brand-panel">
            <a className="ui-v2-brand" href="/" aria-label="Market Workbench home">
              <span className="ui-v2-brand-mark">H</span>
              <span className="ui-v2-brand-copy">
                <strong>Market Workbench</strong>
                <small>HAN WOOL</small>
              </span>
            </a>
            <button
              type="button"
              className="ui-v2-sidebar-toggle"
              aria-controls="ui-v2-sidebar"
              aria-expanded={!isSidebarCollapsed}
              aria-label={isSidebarCollapsed ? "Open sidebar" : "Close sidebar"}
              title={isSidebarCollapsed ? "Open sidebar" : "Close sidebar"}
              onClick={toggleSidebar}
            >
              <SidebarToggleIcon />
            </button>
          </div>
        </div>

        <nav className="ui-v2-nav" aria-label="Main navigation">
          {shellTabs.map((item) => (
            <button
              key={item.key}
              type="button"
              className={activeTab === item.key ? "is-active" : ""}
              aria-label={item.label}
              title={item.label}
              onClick={() => onTabChange(item.key)}
            >
              <span className="ui-v2-nav-icon">
                <TabIcon type={item.icon} />
              </span>
              <span className="ui-v2-nav-label">{item.label}</span>
            </button>
          ))}
        </nav>

        <div className="ui-v2-sidebar-status">
          <div className="ui-v2-sidebar-status-ring">
            <span />
          </div>
          <div className="ui-v2-sidebar-status-copy">
            <strong>System Online</strong>
            <small>Live market workspace</small>
          </div>
        </div>
      </aside>

      <div className="ui-v2-workspace">
        <header className="ui-v2-topbar">
          <div className="ui-v2-market-head">
            <div className="ui-v2-kicker">Live Market</div>
            <div className={`ui-v2-market-price ui-v2-market-price--${tickerTone}`}>
              <span className="ui-v2-market-symbol">{ticker.symbol}</span>
              <strong>{ticker.formattedPrice}</strong>
            </div>
          </div>
          <div className="ui-v2-topbar-status">
            {activeTab === "chart" ? null : (
              <Badge tone="steel">Design preview</Badge>
            )}
            {preview && activeTab === "chart" ? (
              <Badge tone="steel">Read-only preview</Badge>
            ) : null}
            <Badge tone={ticker.status === "live" ? "ruby" : "warning"} pulse={ticker.status === "live"}>
              {statusLabel}
            </Badge>
            {/* <Badge tone="steel">{ticker.tf}m · Kline</Badge> */}
          </div>
        </header>

        <main className="ui-v2-main">
          {children}
        </main>
      </div>
    </div>
  );
}
