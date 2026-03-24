import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import AmbientBackground from "../components/AmbientBackground";
import GlassSurface from "../components/glass/GlassSurface";

const SIDEBAR_STORAGE_KEY = "market-workbench.uiV3.sidebarCollapsed";

const navItems = [
  { label: "Control", subtitle: "Execution guard", path: "/", end: true, icon: "overview" },
  { label: "Chart", subtitle: "Live structure", path: "/chart", icon: "chart" },
  { label: "Stats", subtitle: "Performance", path: "/stats", icon: "stats" },
  { label: "Settings", subtitle: "Risk and alerts", path: "/settings", icon: "settings" },
];

function getStoredSidebarState() {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

function SidebarToggleIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <rect x="3.5" y="4" width="17" height="16" rx="3.5" />
      <path d="M9 4v16" />
      <path d="M15 9l3 3-3 3" />
    </svg>
  );
}

function NavIcon({ type }) {
  if (type === "chart") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M4 18h16" />
        <path d="M6 15l3-4 3 2 5-7" />
        <path d="M17 6h2v2" />
      </svg>
    );
  }

  if (type === "stats") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M5 19V9" />
        <path d="M12 19V5" />
        <path d="M19 19v-7" />
      </svg>
    );
  }

  if (type === "settings") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Z" />
        <path d="M12 3.8v2M12 18.2v2M5.4 5.4l1.4 1.4M17.2 17.2l1.4 1.4M3.8 12h2M18.2 12h2M5.4 18.6l1.4-1.4M17.2 6.8l1.4-1.4" />
      </svg>
    );
  }

  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 17.5V8.8L12 4l8 4.8v8.7" />
      <path d="M8 20h8" />
      <path d="M8 14h8" />
    </svg>
  );
}

function WorkspaceNav({ className = "" }) {
  return (
    <nav className={className} aria-label="UI v3 workspace navigation">
      {navItems.map((item) => (
        <NavLink
          key={item.path}
          to={item.path}
          end={item.end}
          title={item.label}
          aria-label={`${item.label}: ${item.subtitle}`}
          className={({ isActive }) => isActive ? "is-active" : ""}
        >
          <span className="ui-v3-nav-icon">
            <NavIcon type={item.icon} />
          </span>
          <span className="ui-v3-nav-copy">
            <strong>{item.label}</strong>
            <small>{item.subtitle}</small>
          </span>
          <span className="ui-v3-nav-arrow" aria-hidden="true">
            <svg viewBox="0 0 24 24">
              <path d="M9 18l6-6-6-6" />
            </svg>
          </span>
        </NavLink>
      ))}
    </nav>
  );
}

function getPriceTone(ticker) {
  if (ticker?.direction === "up") return "positive";
  if (ticker?.direction === "down") return "negative";
  return "neutral";
}

function getFreshnessLabel(updatedAt) {
  if (!updatedAt) return "Last update --";
  return `Updated ${new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(updatedAt))}`;
}

export default function AppShell({
  ticker,
  children,
}) {
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(getStoredSidebarState);
  const rootClasses = [
    "ui-v3-root",
    "ui-v3-root--liquid",
    isSidebarCollapsed ? "is-sidebar-collapsed" : "",
  ].filter(Boolean).join(" ");
  const toggleSidebar = () => setIsSidebarCollapsed((current) => !current);

  useEffect(() => {
    try {
      window.localStorage.setItem(SIDEBAR_STORAGE_KEY, String(isSidebarCollapsed));
    } catch {
      // Sidebar state persistence is optional; keep the interaction available.
    }
  }, [isSidebarCollapsed]);

  return (
    <div className={rootClasses}>
      <AmbientBackground />
      <GlassSurface as="aside" level="chrome" id="ui-v3-liquid-sidebar" className="ui-v3-liquid-sidebar">
        <div className="ui-v3-sidebar-brand-row">
          <NavLink to="/" end className="ui-v3-brand ui-v3-brand--liquid" aria-label="HanWool Market Workbench">
            <span className="ui-v3-brand-mark">H</span>
            <span className="ui-v3-brand-copy">
              <strong>HanWool</strong>
              <small>Market Workbench</small>
            </span>
          </NavLink>

          <button
            type="button"
            className="ui-v3-sidebar-toggle"
            aria-controls="ui-v3-liquid-sidebar"
            aria-expanded={!isSidebarCollapsed}
            aria-label={isSidebarCollapsed ? "Open sidebar" : "Close sidebar"}
            title={isSidebarCollapsed ? "Open sidebar" : "Close sidebar"}
            onClick={toggleSidebar}
          >
            <SidebarToggleIcon />
          </button>
        </div>

        <div className="ui-v3-sidebar-title">
          <span>Market Desk</span>
          <strong>BTCUSDT Workspace</strong>
        </div>

        <WorkspaceNav className="ui-v3-liquid-nav ui-v3-liquid-nav--side" />

        <div className="ui-v3-sidebar-footer">
          <span>Market workspace</span>
          <small>Chart-first trading view</small>
          <a className="ui-v3-legacy-link" href="/ui-v2">Open UI v2</a>
        </div>
      </GlassSurface>

      <section className="ui-v3-content ui-v3-liquid-content">
        <GlassSurface as="header" level="chrome" className="ui-v3-liquid-topline">
          <div className="ui-v3-live-market-head" aria-label="Live BTCUSDT market status">
            <span>Live Market</span>
            <div className="ui-v3-live-market-price">
              <span>BTCUSDT</span>
              <strong className={`is-${getPriceTone(ticker)}`}>{ticker.formattedPrice}</strong>
            </div>
          </div>
          <div className="ui-v3-live-price-meta">
            <span>{getFreshnessLabel(ticker.updatedAt)}</span>
            <span className={`ui-v3-feed-pill is-${ticker.status === "live" ? "live" : "waiting"}`}>
              {ticker.status === "live" ? "Live price" : "Connecting"}
            </span>
          </div>
        </GlassSurface>

        <main className="ui-v3-main">
          {children}
        </main>
      </section>
    </div>
  );
}
