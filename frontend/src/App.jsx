//// frontend/src/App.jsx
import { useMemo } from "react";
import { Routes, Route, useLocation, useNavigate, Navigate } from "react-router-dom";
import AppleGlassNav from "./components/AppleGlassNav";      // (텍스트 라벨로 바꾼 CodePen 포팅)
import "./App.css";
import "./styles/tokens.css";
import "./styles/layout.css";
import "./styles/scene.css";
import StarfieldBg from "./components/StarfieldBg";
import ControlCenter from "./pages/ControlCenter";
import Chart from "./pages/Chart";
import Stats from "./pages/Stats";
import Settings from "./pages/Settings";
import OtpGate from "./components/OtpGate";

const NAV_ITEMS = [
  { key: "controlcenter", label: "Control Center", path: "/" },
  { key: "chart", label: "Chart", path: "/chart" },
  { key: "stats", label: "Stats", path: "/stats" },
  { key: "settings", label: "Settings", path: "/settings" },
];

const ROUTE_BY_KEY = NAV_ITEMS.reduce((acc, item) => {
  acc[item.key] = item.path;
  return acc;
}, {});

const DEFAULT_ROUTE_KEY = "controlcenter";

export default function App() {
  const location = useLocation();
  const navigate = useNavigate();

  const items = useMemo(
    () => NAV_ITEMS.map(({ key, label }) => ({ key, label })),
    []
  );

  // URL <-> key 매핑
  const pathForKey = (k) => ROUTE_BY_KEY[k] ?? ROUTE_BY_KEY[DEFAULT_ROUTE_KEY];
  const keyForPath = (p) => {
    if (p === ROUTE_BY_KEY[DEFAULT_ROUTE_KEY]) return DEFAULT_ROUTE_KEY;

    const match = NAV_ITEMS.find(
      (item) => item.path !== ROUTE_BY_KEY[DEFAULT_ROUTE_KEY] && p.startsWith(item.path)
    );

    return match ? match.key : DEFAULT_ROUTE_KEY;
  };

  const activeKey = keyForPath(location.pathname);

  return (
    <div id="app-root">
      <StarfieldBg />
      
      {/* OTP 게이트: 세션 없으면 앱 전체를 덮는 팝업 */}
      <OtpGate />
      {/* 네비게이션 */}
      <div className="app-nav">
        <div className="app-nav-inner">
          <AppleGlassNav
            items={items}
            activeKey={activeKey}
            onChange={(k) => {
              navigate(pathForKey(k));
            }}
          />
        </div>
      </div>

      {/* 라우트 렌더링 */}
      <div className="app-routes">
        <Routes>
          <Route path="/" element={<ControlCenter />} />
          <Route path="/chart" element={<Chart />} />
          <Route path="/stats" element={<Stats />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
    </div>
  );
}
