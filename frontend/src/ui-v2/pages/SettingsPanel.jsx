// frontend/src/ui-v2/pages/SettingsPanel.jsx

import { Badge, Button, Panel, SegmentedControl, Switch } from "../components/primitives";
import { settingsSections } from "../mock/mockData";

function SettingControl({ item }) {
  if (item.type === "segmented") {
    return <SegmentedControl options={item.options} value={item.value} />;
  }

  if (item.type === "switch") {
    return <Switch label={item.label} checked={item.value} disabled />;
  }

  if (item.type === "readonly") {
    return <Badge tone="success">{item.value}</Badge>;
  }

  return <input className="ui-v2-input" value={item.value} readOnly />;
}

export default function SettingsPanel() {
  return (
    <div className="ui-v2-page ui-v2-page--settings">
      <aside className="ui-v2-settings-nav">
        {settingsSections.map((section, index) => (
          <button key={section.title} type="button" className={index === 0 ? "is-active" : ""}>
            {section.title}
          </button>
        ))}
      </aside>

      <section className="ui-v2-settings-stack">
        <Panel variant="active" className="ui-v2-settings-header">
          <div>
            <span className="ui-v2-kicker">Settings</span>
            <h2>Premium Admin Surface</h2>
            <p>Manage workspace behavior, chart defaults, strategy risk, and alert routing.</p>
          </div>
          <div className="ui-v2-settings-actions">
            {/* 설정 저장 API가 붙기 전까지 공개 화면에서는 입력값을 서버로 보내지 않는다. */}
            <Badge tone="steel">Preview data</Badge>
            <Button variant="secondary" disabled>Reset</Button>
            <Button variant="primary" disabled>Apply changes</Button>
          </div>
        </Panel>

        {settingsSections.map((section) => (
          <Panel key={section.title} className="ui-v2-settings-section">
            <div className="ui-v2-section-head">
              <div>
                <span className="ui-v2-kicker">Configuration</span>
                <h2>{section.title}</h2>
              </div>
            </div>
            <div className="ui-v2-setting-list">
              {section.items.map((item) => (
                <div className="ui-v2-setting-row" key={item.label}>
                  {item.type === "switch" ? (
                    <SettingControl item={item} />
                  ) : (
                    <>
                      <div>
                        <strong>{item.label}</strong>
                        <span>Current value</span>
                      </div>
                      <SettingControl item={item} />
                    </>
                  )}
                </div>
              ))}
            </div>
          </Panel>
        ))}
      </section>
    </div>
  );
}
