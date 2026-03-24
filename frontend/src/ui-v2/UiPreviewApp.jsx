// frontend/src/ui-v2/UiPreviewApp.jsx

import { useEffect, useMemo, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import AppShell, { getUiV2PathForTab, getUiV2TabFromPath } from "./components/AppShell";
import AnalyticsDashboard from "./pages/AnalyticsDashboard";
import ChartWorkspace from "./pages/ChartWorkspace";
import ControlCenter from "./pages/ControlCenter";
import SettingsPanel from "./pages/SettingsPanel";
import "./theme/tokens.css";
import "./ui-v2.css";

function normalizeBasePath(basePath) {
  if (!basePath || basePath === "/") return "";
  return basePath.endsWith("/") ? basePath.slice(0, -1) : basePath;
}

function buildPath(basePath, segment = "") {
  return `${basePath}${segment}` || "/";
}

export default function UiPreviewApp({ basePath = "/ui-preview", preview = true }) {
  const location = useLocation();
  const navigate = useNavigate();
  const normalizedBasePath = useMemo(() => normalizeBasePath(basePath), [basePath]);
  const activeTab = getUiV2TabFromPath(location.pathname, normalizedBasePath);
  // 차트는 klinecharts 인스턴스와 WS 구독 비용이 크다.
  // 한 번 열린 뒤에는 탭 이동 시 display만 전환해 runtime을 유지한다.
  const [hasChartMounted, setHasChartMounted] = useState(activeTab === "chart");

  useEffect(() => {
    document.documentElement.classList.add("ui-v2-document");
    return () => document.documentElement.classList.remove("ui-v2-document");
  }, []);

  useEffect(() => {
    if (activeTab === "chart") {
      setHasChartMounted(true);
    }
  }, [activeTab]);

  return (
    <AppShell
      activeTab={activeTab}
      basePath={normalizedBasePath}
      preview={preview}
      onTabChange={(key) => navigate(getUiV2PathForTab(key, normalizedBasePath))}
    >
      {hasChartMounted ? (
        <div
          className="ui-v2-chart-mount ui-v2-tab-motion"
          style={{ display: activeTab === "chart" ? "block" : "none" }}
        >
          <ChartWorkspace isActive={activeTab === "chart"} readOnly={preview} />
        </div>
      ) : null}

      {activeTab !== "chart" ? (
        <div className="ui-v2-tab-motion" key={activeTab}>
          <Routes>
            <Route path={buildPath(normalizedBasePath, "/control")} element={<ControlCenter />} />
            <Route path={buildPath(normalizedBasePath, "/stats")} element={<AnalyticsDashboard />} />
            <Route path={buildPath(normalizedBasePath, "/analytics")} element={<Navigate to={buildPath(normalizedBasePath, "/stats")} replace />} />
            <Route path={buildPath(normalizedBasePath, "/settings")} element={<SettingsPanel />} />
            <Route path="*" element={<Navigate to={buildPath(normalizedBasePath)} replace />} />
          </Routes>
        </div>
      ) : null}
    </AppShell>
  );
}
