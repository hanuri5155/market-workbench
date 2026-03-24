import { useMemo, useState } from "react";
import PageHeader from "../components/PageHeader";
import SectionPanel from "../components/SectionPanel";
import StatusBadge from "../components/StatusBadge";
import { settingsSections } from "../domain/tradingWorkbenchData";

function getSettingKey(sectionTitle, itemLabel) {
  return `${sectionTitle}:${itemLabel}`;
}

function getSectionId(title) {
  return String(title).toLowerCase().replace(/[^a-z0-9]+/g, "-");
}

function buildInitialSettings() {
  return settingsSections.reduce((acc, section) => {
    section.items.forEach((item) => {
      acc[getSettingKey(section.title, item.label)] = item.value;
    });
    return acc;
  }, {});
}

function SegmentedSetting({ section, item, value, onChange }) {
  return (
    <div className="ui-v3-segmented-control" role="group" aria-label={item.label}>
      {item.options.map((option) => (
        <button
          key={option}
          type="button"
          className={option === value ? "is-active" : ""}
          aria-pressed={option === value}
          onClick={() => onChange(getSettingKey(section.title, item.label), option)}
        >
          {option}
        </button>
      ))}
    </div>
  );
}

function SwitchSetting({ section, item, value, onChange }) {
  return (
    <button
      type="button"
      className={`ui-v3-switch-control${value ? " is-on" : ""}`}
      role="switch"
      aria-checked={Boolean(value)}
      aria-label={`${item.label}: ${value ? "on" : "off"}`}
      onClick={() => onChange(getSettingKey(section.title, item.label), !value)}
    >
      <span />
    </button>
  );
}

function SettingControl({ section, item, value, onChange }) {
  if (item.type === "segmented") {
    return <SegmentedSetting section={section} item={item} value={value} onChange={onChange} />;
  }

  if (item.type === "switch") {
    return <SwitchSetting section={section} item={item} value={value} onChange={onChange} />;
  }

  if (item.type === "readonly") {
    return (
      <StatusBadge tone="positive" className="ui-v3-setting-badge">
        {value}
      </StatusBadge>
    );
  }

  return <span className="ui-v3-input-shell">{value}</span>;
}

export default function SettingsPanel() {
  const [activeTitle, setActiveTitle] = useState(settingsSections[0]?.title || "");
  const [settingValues, setSettingValues] = useState(() => buildInitialSettings());
  const activeSection = useMemo(
    () => settingsSections.find((section) => section.title === activeTitle) || settingsSections[0],
    [activeTitle],
  );

  const updateSettingValue = (key, value) => {
    setSettingValues((prev) => ({ ...prev, [key]: value }));
  };

  return (
    <div className="ui-v3-page ui-v3-page--settings">
      <PageHeader
        eyebrow="Settings"
        title="Trading workspace settings"
        detail="Workspace behavior, chart defaults, strategy risk posture, and alert routing from the previous control surface."
        status="Preview"
        tone="source"
      />

      <section className="ui-v3-settings-tabs" aria-label="Settings tabs">
        <div className="ui-v3-settings-pill-nav" role="tablist" aria-label="Settings sections">
          {settingsSections.map((section) => {
            const isActive = section.title === activeSection.title;
            return (
              <button
                key={section.title}
                id={`ui-v3-settings-tab-${getSectionId(section.title)}`}
                type="button"
                role="tab"
                aria-selected={isActive}
                aria-controls={`ui-v3-settings-panel-${getSectionId(section.title)}`}
                className={isActive ? "is-active" : ""}
                onClick={() => setActiveTitle(section.title)}
              >
                {section.title}
              </button>
            );
          })}
        </div>

        <SectionPanel
          eyebrow="Settings Surface"
          title={activeSection.title}
          action={<StatusBadge tone="muted">Runtime unchanged</StatusBadge>}
          className="ui-v3-settings-section ui-v3-settings-tab-panel"
        >
          <div
            id={`ui-v3-settings-panel-${getSectionId(activeSection.title)}`}
            role="tabpanel"
            aria-labelledby={`ui-v3-settings-tab-${getSectionId(activeSection.title)}`}
            className="ui-v3-setting-list"
          >
            <p className="ui-v3-settings-panel-copy">{activeSection.detail}</p>
            {activeSection.items.map((item) => {
              const key = getSettingKey(activeSection.title, item.label);
              return (
                <div className="ui-v3-setting-row" key={item.label}>
                  <div>
                    <strong>{item.label}</strong>
                    <span>Current value</span>
                  </div>
                  <SettingControl
                    section={activeSection}
                    item={item}
                    value={settingValues[key]}
                    onChange={updateSettingValue}
                  />
                </div>
              );
            })}
          </div>
        </SectionPanel>
      </section>
    </div>
  );
}
