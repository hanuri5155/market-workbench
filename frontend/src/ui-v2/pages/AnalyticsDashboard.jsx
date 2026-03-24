// frontend/src/ui-v2/pages/AnalyticsDashboard.jsx

import { analyticsStats, recentTrades, strategyRows } from "../mock/mockData";
import { Badge, DataTable, Panel, Sparkline, StatCard } from "../components/primitives";

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

export default function AnalyticsDashboard() {
  return (
    <div className="ui-v2-page ui-v2-page--analytics">
      <section className="ui-v2-analytics-grid">
        {analyticsStats.map((item) => (
          <StatCard key={item.label} {...item} />
        ))}
      </section>

      <section className="ui-v2-analytics-main">
        <Panel className="ui-v2-equity-panel" variant="active">
          <div className="ui-v2-section-head">
            <div>
              <span className="ui-v2-kicker">Performance</span>
              <h2>Equity Curve</h2>
            </div>
            <Badge tone="steel">Preview data</Badge>
          </div>
          {/* 통계 API는 아직 공개용 계약이 없어서 실제 손익이 아닌 샘플 곡선만 렌더링한다. */}
          <div className="ui-v2-equity-chart">
            <Sparkline tone="success" />
            <div className="ui-v2-equity-grid" aria-hidden="true" />
          </div>
          <div className="ui-v2-drawdown-band">
            <span>Max drawdown</span>
            <strong>-3.2%</strong>
          </div>
        </Panel>

        <Panel>
          <div className="ui-v2-section-head">
            <div>
              <span className="ui-v2-kicker">Breakdown</span>
              <h2>Strategy Performance</h2>
            </div>
          </div>
          <DataTable
            columns={strategyColumns}
            rows={strategyRows}
            getTone={(row, key) => {
              if (key === "pnl" && String(row.pnl).startsWith("-")) return "red";
              if (key === "pnl") return "success";
              if (key === "risk" && row.risk === "Watch") return "warning";
              return "";
            }}
          />
        </Panel>
      </section>

      <Panel className="ui-v2-trades-panel">
        <div className="ui-v2-section-head">
          <div>
            <span className="ui-v2-kicker">Recent Trades</span>
            <h2>Closed Execution Log</h2>
          </div>
          <Badge>last 24h</Badge>
        </div>
        <DataTable
          columns={tradeColumns}
          rows={recentTrades}
          getTone={(row, key) => {
            if (key === "result" && String(row.result).startsWith("-")) return "red";
            if (key === "result") return "success";
            return "";
          }}
        />
      </Panel>
    </div>
  );
}
