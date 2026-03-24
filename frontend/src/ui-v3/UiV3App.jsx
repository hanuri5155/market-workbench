import { useCallback, useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import useLiveTicker from "../ui-v2/hooks/useLiveTicker";
import AppShell from "./layout/AppShell";
import AnalyticsDashboard from "./pages/AnalyticsDashboard";
import ChartWorkspace from "./pages/ChartWorkspace";
import ControlCenter from "./pages/ControlCenter";
import SettingsPanel from "./pages/SettingsPanel";
import "./tokens/tokens.css";
import "./styles/ui-v3.css";

const RIPPLE_TARGET_SELECTOR = [
  ".ui-v3-metric-card",
  ".ui-v3-route-card",
  ".ui-v3-zone-card",
  ".ui-v3-liquid-topline",
  ".ui-v3-setting-row",
  ".ui-v3-readiness-list > div",
  ".ui-v3-trust-list > div",
  ".ui-v3-event",
  ".ui-v3-motion-button",
  ".ui-v3-legacy-link",
  ".ui-v3-liquid-nav--side a",
  ".ui-v3-switch-control",
  ".ui-v3-switch-track",
  ".ui-v3-settings-pill-nav button",
  ".ui-v3-segmented-control button",
  ".ui-v3-guard-state",
  ".cchart-btn",
].join(",");

export default function UiV3App() {
  const [activeTf, setActiveTf] = useState("15");
  const ticker = useLiveTicker({ tf: activeTf });

  const handleTimeframeChange = useCallback((nextTf) => {
    setActiveTf(String(nextTf || "15"));
  }, []);

  useEffect(() => {
    document.documentElement.classList.add("ui-v3-document");

    const handleRipple = (event) => {
      const target = event.target?.closest?.(RIPPLE_TARGET_SELECTOR);
      if (!target || !document.documentElement.contains(target)) return;
      const isChartControl = target.matches(".cchart-btn");
      if (!isChartControl && target.closest(".ui-v3-chart-stage, .ui-v3-live-chart, .cchart-host")) return;
      if (target.querySelectorAll("[data-ripple]").length > 3) return;

      const rect = target.getBoundingClientRect();
      const size = Math.max(rect.width, rect.height) * 1.2;
      const ripple = document.createElement("span");
      ripple.className = "ui-v3-ripple";
      ripple.setAttribute("data-ripple", "");
      ripple.style.left = `${event.clientX - rect.left - size / 2}px`;
      ripple.style.top = `${event.clientY - rect.top - size / 2}px`;
      ripple.style.width = `${size}px`;
      ripple.style.height = `${size}px`;
      ripple.style.background = "rgba(255,255,255,0.18)";
      ripple.style.transform = "scale(0)";
      ripple.style.animation = "ripple 600ms ease-out forwards";

      target.appendChild(ripple);
      ripple.addEventListener("animationend", () => ripple.remove(), { once: true });
    };

    document.addEventListener("click", handleRipple, true);
    return () => {
      document.documentElement.classList.remove("ui-v3-document");
      document.removeEventListener("click", handleRipple, true);
    };
  }, []);

  return (
    <AppShell activeTf={activeTf} ticker={ticker}>
      <Routes>
        <Route
          path="/"
          element={<ControlCenter ticker={ticker} activeTf={activeTf} />}
        />
        <Route
          path="/chart"
          element={
            <ChartWorkspace
              activeTf={activeTf}
              ticker={ticker}
              onTimeframeChange={handleTimeframeChange}
            />
          }
        />
        <Route path="/stats" element={<AnalyticsDashboard />} />
        <Route path="/analytics" element={<Navigate to="/stats" replace />} />
        <Route path="/settings" element={<SettingsPanel />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}
