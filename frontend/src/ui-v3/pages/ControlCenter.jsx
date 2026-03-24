import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  fetchStrategyFlags,
  updateEnableZoneStrategy,
  updateEnableTrading,
} from "../../api/strategyFlag";
import GlassSurface from "../components/glass/GlassSurface";
import LiveStreamIndicator from "../components/LiveStreamIndicator";
import MetricCard from "../components/MetricCard";
import StatusBadge from "../components/StatusBadge";
import TrendLine from "../components/TrendLine";
import {
  chartSignals,
  eventFeed,
  systemStatus,
} from "../domain/tradingWorkbenchData";

const STRATEGY_FLAG_DEFAULTS = {
  enableTrading: false,
  enableZoneStrategy: false,
};

const workspaceLinks = [
  {
    title: "Chart Cockpit",
    path: "/chart",
    label: "Open chart",
    detail: "Live candles, structure zones, and Bollinger context.",
  },
  {
    title: "Performance Stats",
    path: "/stats",
    label: "View stats",
    detail: "P&L, win rate, drawdown, strategy breakdown, and closed trades.",
  },
  {
    title: "Strategy Settings",
    path: "/settings",
    label: "Configure view",
    detail: "Workspace density, default chart, risk guard, and alerts.",
  },
  {
    title: "Legacy UI v2",
    path: "/ui-v2",
    label: "Open UI v2",
    detail: "Preserved comparison route for rollback review.",
    external: true,
  },
];

function RouteCard({ item }) {
  const content = (
    <>
      <span>{item.label}</span>
      <strong>{item.title}</strong>
      <p>{item.detail}</p>
    </>
  );

  if (item.external) {
    return <a className="ui-v3-route-card" href={item.path}>{content}</a>;
  }

  return <Link className="ui-v3-route-card" to={item.path}>{content}</Link>;
}

function StrategyFlagSwitch({ item, checked, disabled, busy, onToggle }) {
  return (
    <div className={`ui-v3-switch-row${disabled ? " is-disabled" : ""}`}>
      <div>
        <strong>{item.label}</strong>
        <span>{disabled ? "Enable strategy controls first" : item.detail}</span>
      </div>
      <button
        type="button"
        className={`ui-v3-switch-track${checked ? " is-on" : ""}`}
        role="switch"
        aria-checked={checked}
        aria-label={`${item.label}: ${checked ? "enabled" : "standby"}`}
        disabled={disabled || busy}
        onClick={onToggle}
      >
        <span />
      </button>
    </div>
  );
}

function EventItem({ item }) {
  const tone = item.level === "risk" ? "risk" : item.level === "warn" ? "warn" : "info";

  return (
    <div className={`ui-v3-event ui-v3-event--${tone}`}>
      <time>{item.time}</time>
      <span>{item.message}</span>
    </div>
  );
}

function normalizeStrategyFlags(data) {
  if (!data) return STRATEGY_FLAG_DEFAULTS;

  return {
    enableTrading: Boolean(data.enable_trading ?? data.enableTrading ?? false),
    enableZoneStrategy: Boolean(data.enable_zone_strategy ?? data.enableZoneStrategy ?? false),
  };
}

function getCurrentStatus(ticker, activeTf, strategyFlags) {
  return systemStatus.map((item) => {
    if (item.label === "Bot Engine") {
      return {
        ...item,
        value: strategyFlags.enableTrading ? "Enabled" : "Paused",
        tone: strategyFlags.enableTrading ? "positive" : "negative",
        detail: strategyFlags.enableTrading
          ? "strategy controls enabled"
          : "strategy controls paused",
      };
    }

    if (item.label === "Data Feed") {
      return {
        ...item,
        value: ticker.status === "live" ? "Live" : "Waiting",
        tone: ticker.status === "live" ? "positive" : "warning",
        detail: ticker.status === "live"
          ? "BTCUSDT stream locked"
          : "awaiting candle stream",
      };
    }

    if (item.label === "Chart Source") {
      return {
        ...item,
        detail: `${activeTf}m primary timeframe`,
      };
    }

    return item;
  });
}

export default function ControlCenter({ ticker, activeTf }) {
  const isLive = ticker.status === "live";
  const [strategyFlags, setStrategyFlags] = useState(STRATEGY_FLAG_DEFAULTS);
  const [flagsLoading, setFlagsLoading] = useState(true);
  const [flagsBusy, setFlagsBusy] = useState(false);
  const [flagsError, setFlagsError] = useState("");
  const [guardMotion, setGuardMotion] = useState("");
  const guardMotionTimerRef = useRef(null);
  const currentStatus = getCurrentStatus(ticker, activeTf, strategyFlags);

  const loadStrategyFlags = useCallback(async () => {
    setFlagsLoading(true);
    setFlagsError("");
    try {
      const data = await fetchStrategyFlags();
      if (!data) {
        setFlagsError("Control state unavailable");
        return;
      }
      setStrategyFlags(normalizeStrategyFlags(data));
    } finally {
      setFlagsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadStrategyFlags();
  }, [loadStrategyFlags]);

  useEffect(() => () => {
    if (guardMotionTimerRef.current) {
      window.clearTimeout(guardMotionTimerRef.current);
    }
  }, []);

  useEffect(() => {
    const handleOtpSuccess = () => {
      loadStrategyFlags();
    };

    if (typeof window !== "undefined") {
      window.addEventListener("market-workbench:otp-auth-success", handleOtpSuccess);
    }

    return () => {
      if (typeof window !== "undefined") {
        window.removeEventListener("market-workbench:otp-auth-success", handleOtpSuccess);
      }
    };
  }, [loadStrategyFlags]);

  const setFlagOptimistically = useCallback(async (key, nextValue, persist) => {
    if (flagsLoading || flagsBusy) return;

    setFlagsBusy(true);
    setFlagsError("");
    setStrategyFlags((prev) => ({
      ...prev,
      [key]: nextValue,
    }));

    try {
      const saved = await persist(nextValue);
      if (saved === false) {
        setFlagsError("Control state update failed");
        await loadStrategyFlags();
      }
    } catch (error) {
      setFlagsError("Control state update failed");
      await loadStrategyFlags();
    } finally {
      setFlagsBusy(false);
    }
  }, [flagsBusy, flagsLoading, loadStrategyFlags]);

  const handleToggleTrading = useCallback(() => {
    const nextEnabled = !strategyFlags.enableTrading;
    if (guardMotionTimerRef.current) {
      window.clearTimeout(guardMotionTimerRef.current);
    }
    setGuardMotion(nextEnabled ? "is-arming" : "is-disarming");
    guardMotionTimerRef.current = window.setTimeout(() => {
      setGuardMotion("");
      guardMotionTimerRef.current = null;
    }, 1240);

    setFlagOptimistically(
      "enableTrading",
      nextEnabled,
      updateEnableTrading,
    );
  }, [setFlagOptimistically, strategyFlags.enableTrading]);

  const handleGuardAnimationEnd = useCallback((event) => {
    if (event.target !== event.currentTarget) return;
    if (!String(event.animationName || "").startsWith("ui-v3-power-")) return;
    if (guardMotionTimerRef.current) {
      window.clearTimeout(guardMotionTimerRef.current);
      guardMotionTimerRef.current = null;
    }
    setGuardMotion("");
  }, []);

  const handleToggleZoneStrategy = useCallback(() => {
    if (!strategyFlags.enableTrading) return;
    setFlagOptimistically(
      "enableZoneStrategy",
      !strategyFlags.enableZoneStrategy,
      updateEnableZoneStrategy,
    );
  }, [
    setFlagOptimistically,
    strategyFlags.enableZoneStrategy,
    strategyFlags.enableTrading,
  ]);

  const strategyControls = [
    {
      label: "Structure Zone",
      checked: strategyFlags.enableZoneStrategy,
      detail: "Public demo strategy decision layer",
      onToggle: handleToggleZoneStrategy,
    },
  ];
  const strategyDisabled = !strategyFlags.enableTrading || flagsLoading;
  const masterActionCopy = strategyFlags.enableTrading
    ? {
        label: "Pause Strategy Controls",
        detail: "control layer on",
        badge: "Controls Enabled",
        tone: "positive",
        className: "is-enabled",
      }
    : {
        label: "Enable Strategy Controls",
        detail: "control layer off",
        badge: "Controls Paused",
        tone: "negative",
        className: "is-paused",
      };

  return (
    <div className="ui-v3-page ui-v3-page--command">
      <section className="ui-v3-control-hero" aria-label="Control center">
        <GlassSurface level="panel" interactive className="ui-v3-command-panel ui-v3-command-panel--guard">
          <div className="ui-v3-command-panel-head">
            <div>
              <span className="ui-v3-kicker">Control Center</span>
              <h2>Execution Guard</h2>
            </div>
            <StatusBadge tone={masterActionCopy.tone} pulse={!flagsLoading}>
              {flagsLoading ? "Loading Flags" : masterActionCopy.badge}
            </StatusBadge>
          </div>

          <div className="ui-v3-guard-orbit" aria-hidden="true">
            <span />
          </div>

          <button
            type="button"
            className={`ui-v3-guard-state ${masterActionCopy.className} ${guardMotion}`}
            aria-label={masterActionCopy.label}
            aria-pressed={strategyFlags.enableTrading}
            disabled={flagsLoading || flagsBusy}
            onClick={handleToggleTrading}
            onAnimationEnd={handleGuardAnimationEnd}
          >
            <span className="ui-v3-guard-power-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" focusable="false">
                <path d="M12 3v8" />
                <path d="M7.05 7.05a7 7 0 1 0 9.9 0" />
              </svg>
            </span>
            <span className="ui-v3-guard-state-copy">
              <strong>{flagsLoading ? "Loading" : strategyFlags.enableTrading ? "Online" : "Offline"}</strong>
              <small>{flagsBusy ? "updating" : masterActionCopy.detail}</small>
            </span>
          </button>

          <div className="ui-v3-command-footer">
            <span>
              {flagsError || "Existing control-state contract preserved."}
            </span>
            <strong>{strategyFlags.enableTrading ? "Controls On" : "Controls Off"}</strong>
          </div>
        </GlassSurface>

        <GlassSurface level="panel" interactive className="ui-v3-command-panel ui-v3-command-panel--pulse">
          <div className="ui-v3-command-panel-head">
            <div>
              <span className="ui-v3-kicker">Health</span>
              <h2>System Pulse</h2>
            </div>
            <StatusBadge tone={isLive ? "positive" : "warning"} pulse={isLive}>
              {isLive ? "Nominal" : "Waiting"}
            </StatusBadge>
          </div>

          <TrendLine tone={ticker.direction === "down" ? "negative" : "positive"} />
          <div className="ui-v3-pulse-row">
            <MetricCard
              label="Feed gap"
              value={isLive ? "0.4s" : "--"}
              detail="latest stream cadence"
              tone={isLive ? "positive" : "warning"}
              compact
            />
            <MetricCard
              label="Risk heat"
              value="18%"
              detail="current guard posture"
              tone="negative"
              compact
            />
          </div>
        </GlassSurface>
      </section>

      <section className="ui-v3-status-grid-wide" aria-label="System status">
        {currentStatus.map((item) => (
          <MetricCard key={item.label} {...item} />
        ))}
      </section>

      <section className="ui-v3-intel-ribbon" aria-label="Chart signal context">
        {chartSignals.map((item) => (
          <MetricCard
            key={item.label}
            label={item.label}
            value={item.value}
            tone={item.tone}
            compact
          />
        ))}
      </section>

      <section className="ui-v3-command-grid ui-v3-command-grid--control" aria-label="Strategy and event context">
        <GlassSurface level="panel" interactive className="ui-v3-command-panel">
          <div className="ui-v3-command-panel-head">
            <div>
              <span className="ui-v3-kicker">Strategies</span>
              <h2>Control Matrix</h2>
            </div>
            <StatusBadge tone={strategyFlags.enableTrading ? "positive" : "negative"}>
              {strategyFlags.enableTrading ? "Editable" : "Locked"}
            </StatusBadge>
          </div>
          <div className="ui-v3-switch-stack">
            {strategyControls.map((item) => (
              <StrategyFlagSwitch
                key={item.label}
                item={item}
                checked={Boolean(item.checked)}
                disabled={strategyDisabled}
                busy={flagsBusy}
                onToggle={item.onToggle}
              />
            ))}
          </div>
        </GlassSurface>

        <GlassSurface level="panel" interactive className="ui-v3-command-panel">
          <div className="ui-v3-command-panel-head">
            <div>
              <span className="ui-v3-kicker">Recent Events</span>
              <h2>System Feed</h2>
            </div>
          </div>
          <div className="ui-v3-event-feed">
            {eventFeed.map((item) => (
              <EventItem key={`${item.time}-${item.message}`} item={item} />
            ))}
          </div>
        </GlassSurface>
      </section>

      <GlassSurface as="section" level="panel" interactive className="ui-v3-command-panel ui-v3-command-panel--stream" aria-label="Live stream">
        <div className="ui-v3-command-panel-head">
          <div>
            <span className="ui-v3-kicker">Live Market</span>
            <h2>BTCUSDT stream and chart context</h2>
          </div>
          <StatusBadge tone="source">bot_http</StatusBadge>
        </div>
        <div className="ui-v3-stream-context">
          <LiveStreamIndicator ticker={ticker} compact />
          <MetricCard
            label="Mark price"
            value={ticker.formattedPrice}
            detail="current live candle reference"
            tone={ticker.direction === "down" ? "negative" : "positive"}
            compact
          />
          <MetricCard
            label="Primary timeframe"
            value={`${activeTf}m`}
            detail="current chart workspace"
            tone="source"
            compact
          />
        </div>
      </GlassSurface>

      <section className="ui-v3-route-board" aria-label="Workspace routes">
        {workspaceLinks.map((item) => (
          <RouteCard key={item.title} item={item} />
        ))}
      </section>
    </div>
  );
}
