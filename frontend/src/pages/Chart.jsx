//// frontend/src/pages/Chart.jsx
import GlassScene from "../components/GlassScene";
import CandleChart from "../components/CandleChart";
import ZoneNotificationList from "../components/ZoneNotificationList";
import { ZoneNotificationProvider } from "../contexts/ZoneNotificationContext";
import "./Chart.css";

const PANEL_MIN_HEIGHT = "85vh";

function ChartPanel({ minHeight, panelClassName, contentClassName, children }) {
  return (
    <GlassScene minHeight={minHeight} className={panelClassName}>
      <div className={["chart-panel-body", contentClassName].filter(Boolean).join(" ")}>
        {children}
      </div>
    </GlassScene>
  );
}

export default function Chart() {
  return (
    <ZoneNotificationProvider>
      <div className="chart-grid">
        {/* 좌측 20%: Structure Zone Notification 패널 */}
        <ChartPanel
          minHeight={PANEL_MIN_HEIGHT}
          panelClassName="chart-panel chart-panel--list"
          contentClassName="chart-panel-column"
        >
          {/* 여기 안에서 iOS 스타일 알림 카드들이 스크롤 되도록 구현 */}
          <ZoneNotificationList />
        </ChartPanel>

        {/* 우측 80%: 차트 */}
        <ChartPanel
          minHeight={PANEL_MIN_HEIGHT}
          panelClassName="chart-panel chart-panel--chart"
        >
          <CandleChart />
        </ChartPanel>
      </div>
    </ZoneNotificationProvider>
  );
}
