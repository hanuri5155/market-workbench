// frontend/src/ui-v2/pages/ChartWorkspace.jsx

import CandleChart from "../../components/CandleChart";
import ZoneNotificationList from "../../components/ZoneNotificationList";
import { ZoneNotificationProvider } from "../../contexts/ZoneNotificationContext";
import { Panel } from "../components/primitives";

export default function ChartWorkspace({ isActive, readOnly = false }) {
  return (
    <ZoneNotificationProvider>
      <div className="ui-v2-page ui-v2-page--chart">
        <section className="ui-v2-chart-layout">
          <Panel className="ui-v2-chart-frame" variant="active">
            <div className="ui-v2-chart-stage">
              <div className={`ui-v2-live-chart${readOnly ? " is-readonly" : ""}`}>
                {/* v0.4.3 기준 실제 연결 화면: 기존 CandleChart 런타임, 캔들 WS, 오버레이 복구 경로를 V2 셸 안에 그대로 탑재한다. */}
                <CandleChart isActive={isActive} />
              </div>
            </div>
          </Panel>

          <aside className="ui-v2-chart-side">
            <Panel className="ui-v2-notification-panel">
              <div className="ui-v2-section-head">
                <div>
                  <span className="ui-v2-kicker">Structure Zones</span>
                  <h2>Structure Feed</h2>
                </div>
              </div>
              <div className={`ui-v2-notification-host${readOnly ? " is-readonly" : ""}`}>
                {/* Structure Feed는 mock이 아니라 공개 repo의 Zone 알림 컨텍스트와 동일한 WS/REST 상태를 사용한다. */}
                <ZoneNotificationList />
              </div>
            </Panel>

            {/* <Panel>
              <div className="ui-v2-section-head">
                <div>
                  <span className="ui-v2-kicker">Strategy State</span>
                  <h2>Signal Stack</h2>
                </div>
              </div>
              <div className="ui-v2-signal-stack">
                {chartSignals.map((item) => (
                  <div key={item.label} className="ui-v2-signal-row">
                    <span>{item.label}</span>
                    <Badge tone={item.tone}>{item.value}</Badge>
                  </div>
                ))}
              </div>
            </Panel> */}

            {/*
            <Panel className="ui-v2-position-card" variant="critical">
              <span className="ui-v2-kicker">Current Position</span>
              <h2>Flat · waiting for signal</h2>
              <p>
                {readOnly
                  ? "Market structure is locked for review in this workspace."
                  : "No active exposure. Strategy gates are monitoring structure zones."}
              </p>
              <div className="ui-v2-position-meter">
                <span style={{ width: "0%" }} />
              </div>
              <div className="ui-v2-position-meta">
                <span>Max risk 0.50 R</span>
                <strong>0.00 R open</strong>
              </div>
            </Panel>
            */}
          </aside>
        </section>

        {/*
        <section className="ui-v2-metric-strip">
          {miniMetrics.map((item) => (
            <StatCard key={item.label} {...item} />
          ))}
        </section>
        */}
      </div>
    </ZoneNotificationProvider>
  );
}
