import { useEffect, useMemo, useRef } from "react";
import "./AppleGlassNav.css";

// 화면 전환 탭을 유리 질감 토글 UI로 보여주기 위함
export default function AppleGlassNav({ items = [], activeKey, onChange }) {
  const hostRef = useRef(null);
  const groupName = useMemo(
    () => "glass-nav-" + Math.random().toString(36).slice(2, 8),
    []
  );

  // 이전 선택값을 attribute로 남겨 CSS 전환 방향 계산에 쓰기 위함
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const radios = host.querySelectorAll('input.switcher__input[type="radio"]');
    let previousOption = null;

    const initiallyChecked = host.querySelector(
      'input.switcher__input[type="radio"]:checked'
    );
    if (initiallyChecked) {
      previousOption = initiallyChecked.getAttribute("c-option");
      host.setAttribute("c-previous", previousOption ?? "");
    }

    const cleanups = Array.from(radios).map((radio) => {
      const handler = () => {
        if (!radio.checked) return;
        host.setAttribute("c-previous", previousOption ?? "");
        previousOption = radio.getAttribute("c-option");
      };

      radio.addEventListener("change", handler);
      return () => radio.removeEventListener("change", handler);
    });

    return () => cleanups.forEach((off) => off());
  }, [items, activeKey]);

  return (
    <fieldset ref={hostRef} className="switcher" aria-label="Navigation">
      <legend className="switcher__legend">Navigation</legend>
      {items.map((item, index) => (
        <label key={item.key} className="switcher__option" title={item.label}>
          <input
            className="switcher__input"
            type="radio"
            name={groupName}
            value={item.key}
            c-option={index + 1}
            checked={activeKey === item.key}
            onChange={() => onChange?.(item.key)}
          />
          <span className="switcher__icon" aria-hidden="true">
            {iconFor(item.key)}
          </span>
        </label>
      ))}
    </fieldset>
  );
}

const ICONS_BY_KEY = {
  controlcenter: {
    src: "/control-icon.png",
    alt: "control",
    className: "control-icon",
  },
  chart: {
    src: "/chart-icon.png",
    alt: "chart",
    className: "chart-icon",
  },
  stats: {
    src: "/stats-icon.png",
    alt: "stats",
    className: "stats-icon",
  },
  settings: {
    src: "/setting-icon.png",
    alt: "setting",
    className: "setting-icon",
  },
};

function iconFor(key) {
  const icon = ICONS_BY_KEY[key];
  return icon ? <img {...icon} /> : "⬤";
}
