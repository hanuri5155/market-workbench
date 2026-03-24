import DataTable from "../components/DataTable";
import MetricCard from "../components/MetricCard";
import PageHeader from "../components/PageHeader";
import SectionPanel from "../components/SectionPanel";
import StatusBadge from "../components/StatusBadge";
import TrendLine from "../components/TrendLine";
import {
  analyticsStats,
  recentTrades,
  strategyRows,
} from "../domain/tradingWorkbenchData";

const strategyColumns = [
  { key: "strategy", label: "Strategy" },
  { key: "trades", label: "Trades" },
  { key: "winRate", label: "Win rate" },
  { key: "pnl", label: "P&L" },
  { key: "risk", label: "Risk" },
];

const tradeColumns = [
  { key: "time", label: "Time" },
  { key: "symbol", label: "Symbol" },
  { key: "side", label: "Side" },
  { key: "result", label: "Result" },
  { key: "status", label: "Status" },
];

function getStrategyTone(row, key) {
  if (key === "pnl" && String(row.pnl).startsWith("-")) return "negative";
  if (key === "pnl") return "positive";
  if (key === "risk" && row.risk === "Watch") return "warning";
  if (key === "risk" && row.risk === "Low") return "positive";
  return "";
}

function getTradeTone(row, key) {
  if (key === "side" && row.side === "Long") return "positive";
  if (key === "side" && row.side === "Short") return "negative";
  if (key === "result" && String(row.result).startsWith("-")) return "negative";
  if (key === "result") return "positive";
  if (key === "status" && row.status === "Stopped") return "negative";
  return "";
}

export default function AnalyticsDashboard() {
  return (
    <div className="ui-v3-page ui-v3-page--analytics">
      <PageHeader
        eyebrow="Stats"
        title="Trading performance"
        detail="Session P&L, drawdown, strategy quality, and recent closed execution context carried forward from UI v2."
        status="Review"
        tone="source"
      />

      <section className="ui-v3-status-grid-wide ui-v3-status-grid-wide--six" aria-label="Performance metrics">
        {analyticsStats.map((item) => (
          <MetricCard key={item.label} {...item} />
        ))}
      </section>

      <section className="ui-v3-analytics-grid">
        <SectionPanel
          eyebrow="Performance"
          title="Equity Curve"
          action={<StatusBadge tone="positive">Session analytics</StatusBadge>}
          className="ui-v3-equity-panel"
        >
          <TrendLine tone="positive" />
          <div className="ui-v3-drawdown-row">
            <span>Max drawdown</span>
            <strong>-3.2%</strong>
          </div>
        </SectionPanel>

        <SectionPanel eyebrow="Breakdown" title="Strategy Performance">
          <DataTable
            columns={strategyColumns}
            rows={strategyRows}
            getTone={getStrategyTone}
          />
        </SectionPanel>
      </section>

      <SectionPanel
        eyebrow="Recent Trades"
        title="Closed Execution Log"
        action={<StatusBadge tone="muted">Last 24h</StatusBadge>}
      >
        <DataTable
          columns={tradeColumns}
          rows={recentTrades}
          getTone={getTradeTone}
        />
      </SectionPanel>
    </div>
  );
}
