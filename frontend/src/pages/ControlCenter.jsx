//// frontend/src/pages/ControlCenter.jsx
import { useEffect, useState, useCallback } from "react";
import GlassScene from "../components/GlassScene";
import {
  fetchStrategyFlags,
  updateEnableTrading,
  updateEnableZoneStrategy,
} from "../api/strategyFlag";
import "./ControlCenter.css";  //  iOS 스위치 스타일 정의(3-2 단계에서 만듦)

function StrategyToggleRow({ id, label, checked, disabled, onChange }) {
  return (
    <div className={"cc-strategy-item" + (disabled ? " is-disabled" : "")}>
      <div className="cc-strategy-texts">
        <div className="cc-strategy-label">{label}</div>
      </div>

      <div className="cc-switch-container">
        <input
          type="checkbox"
          id={id}
          className="cc-switch-checkbox"
          checked={checked}
          disabled={disabled}
          onChange={onChange}
        />
        <label className="cc-switch" htmlFor={id}>
          <span className="cc-slider" />
        </label>
      </div>
    </div>
  );
}

export default function ControlCenter() {
  const [enableTrading, setEnableTrading] = useState(false);
  const [enableZoneStrategy, setEnableZoneStrategy] = useState(false);
  const [loading, setLoading] = useState(true);

  //  공통 로딩 함수: 최초 마운트 + OTP 통과 시 모두 여기로
  const loadFlags = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchStrategyFlags();
      if (!data) {
        return;
      }

      const vTrading =
        data.enable_trading ?? data.enableTrading ?? false;
      const vZone =
        data.enable_zone_strategy ??
        data.enableZoneStrategy ??
        false;

      setEnableTrading(Boolean(vTrading));
      setEnableZoneStrategy(Boolean(vZone));
    } finally {
      setLoading(false);
    }
  }, []);

  //  1) 컴포넌트 최초 마운트 시 1회 호출
  useEffect(() => {
    loadFlags();
  }, [loadFlags]);

  //  2) OTP 인증 성공할 때마다 다시 /api/config 재요청
  useEffect(() => {
    const handleOtpSuccess = () => {
      loadFlags();
    };

    if (typeof window !== "undefined") {
      window.addEventListener("market-workbench:otp-auth-success", handleOtpSuccess);
    }

    return () => {
      if (typeof window !== "undefined") {
        window.removeEventListener("market-workbench:otp-auth-success", handleOtpSuccess);
      }
    };
  }, [loadFlags]);

  const handleCircleClick = () => {
    if (loading) return; // 아직 초기값 못 가져왔으면 클릭 무시

    const next = !enableTrading;

    // 1) UI를 낙관적으로 먼저 바꾸고
    setEnableTrading(next);
    // 2) 백엔드에 저장 (실패 시 콘솔에만 에러 남김)
    updateEnableTrading(next);
  };

  const handleToggleZoneStrategy = () => {
    if (loading) return;
    if (!enableTrading) return;

    const next = !enableZoneStrategy;
    setEnableZoneStrategy(next);
    updateEnableZoneStrategy(next);
  };

  // GlassScene에 붙일 상태 클래스
  const circleClass = enableTrading ? "cc-circle-on" : "cc-circle-off";
  const strategiesDisabled = !enableTrading;
  const strategyToggles = [
    {
      id: "cc-zone-strategy-switch",
      label: "Structure Zone",
      checked: enableZoneStrategy,
      onChange: handleToggleZoneStrategy,
    },
  ];

  return (
    // cards-grid: 여러 유리 카드의 배치 그리드(가운데 정렬)
    <div className="cards-grid">
      {/*  Strategy GlassScene 카드  */}
      <GlassScene
        className="cc-strategy-card"
      >
        {/* 상단: Enable Trading Circle 버튼 (가운데 정렬) */}
        <div className="cc-strategy-circle-row">
          <GlassScene
            type="circle"
            initialOn={enableTrading}
            className={`${circleClass} cc-trading-circle`}
            onClick={handleCircleClick}
            // 크기 조절은 CSS에서 처리
          >
            <img
              src="/power-icon.png"
              alt="Power"
              className="cc-power-icon"
            />
          </GlassScene>
        </div>

        {/* 전략 토글 리스트 */}
        <div className="cc-strategy-list">
          {strategyToggles.map((toggle) => (
            <StrategyToggleRow
              key={toggle.id}
              id={toggle.id}
              label={toggle.label}
              checked={toggle.checked}
              disabled={strategiesDisabled}
              onChange={toggle.onChange}
            />
          ))}
        </div>
      </GlassScene>

      {/* Pill */}
      {/* <GlassScene type="pill">
        Pill Button
      </GlassScene> */}

      {/* 소형 카드 */}
      {/* <GlassScene minHeight={160}>
        <h3>Overview</h3>
      </GlassScene> */}

      {/* 소형 카드: 미니 차트 */}
      {/* <GlassScene minHeight={220}>
        <h3>Mini Chart</h3>
      </GlassScene> */}

      {/* 대형 카드: 상세 */}
      {/* <GlassScene minHeight={360}>
        <h3>Details</h3>
      </GlassScene> */}
    </div>
  );
}
