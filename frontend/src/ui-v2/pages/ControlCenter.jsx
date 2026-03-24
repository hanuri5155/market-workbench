// frontend/src/ui-v2/pages/ControlCenter.jsx

import { Badge, Button, Panel, Sparkline, StatCard, Switch } from "../components/primitives";
import { eventFeed, strategySwitches, systemStatus } from "../mock/mockData";

export default function ControlCenter() {
  return (
    <div className="ui-v2-page ui-v2-page--control">
      <section className="ui-v2-control-hero">
        <Panel variant="critical" className="ui-v2-command-core">
          <div className="ui-v2-command-orbit" aria-hidden="true" />
          <div className="ui-v2-section-head">
            <div>
              <span className="ui-v2-kicker">Control Center</span>
              <h2>Execution Guard</h2>
            </div>
            <Badge tone="steel">Preview data</Badge>
          </div>
          <div className="ui-v2-command-button-wrap">
            {/* 아직 제어 API를 연결하지 않은 공개 프리뷰 영역이므로 실제 중지 요청은 발생시키지 않는다. */}
            <button type="button" className="ui-v2-command-button" disabled>
              <span>Emergency Stop</span>
              <small>read-only guard</small>
            </button>
          </div>
          <div className="ui-v2-command-footer">
            <span>Control API pending.</span>
            <strong>Design shell</strong>
          </div>
        </Panel>

        <div className="ui-v2-status-grid">
          {systemStatus.map((item) => (
            <Panel key={item.label} interactive>
              <div className="ui-v2-status-card">
                <Badge tone={item.tone}>{item.value}</Badge>
                <h3>{item.label}</h3>
                <p>{item.detail}</p>
              </div>
            </Panel>
          ))}
        </div>
      </section>

      <section className="ui-v2-control-lower">
        <Panel className="ui-v2-strategy-panel">
          <div className="ui-v2-section-head">
            <div>
              <span className="ui-v2-kicker">Strategies</span>
              <h2>Control Matrix</h2>
            </div>
            <Button variant="secondary" disabled>Review</Button>
          </div>
          <div className="ui-v2-switch-stack">
            {strategySwitches.map((item) => (
              <Switch key={item.label} {...item} disabled />
            ))}
          </div>
        </Panel>

        <Panel className="ui-v2-health-panel">
          <div className="ui-v2-section-head">
            <div>
              <span className="ui-v2-kicker">Health</span>
              <h2>System Pulse</h2>
            </div>
            <Badge tone="success">Nominal</Badge>
          </div>
          <Sparkline tone="red" />
          <div className="ui-v2-stat-row">
            <StatCard label="Feed gap" value="0.4" unit="s" tone="success" />
            <StatCard label="Risk heat" value="18" unit="%" tone="red" />
          </div>
        </Panel>

        <Panel className="ui-v2-events-panel">
          <div className="ui-v2-section-head">
            <div>
              <span className="ui-v2-kicker">Recent Events</span>
              <h2>System Feed</h2>
            </div>
          </div>
          <div className="ui-v2-event-list">
            {eventFeed.map((item) => (
              <div className={`ui-v2-event ui-v2-event--${item.level}`} key={`${item.time}-${item.message}`}>
                <time>{item.time}</time>
                <span>{item.message}</span>
              </div>
            ))}
          </div>
        </Panel>
      </section>
    </div>
  );
}
