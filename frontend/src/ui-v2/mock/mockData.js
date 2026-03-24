// frontend/src/ui-v2/mock/mockData.js

// Control/Stats/Settings는 v0.4.3 시점에 API 계약이 아직 확정되지 않은 디자인 프리뷰다.
// 실제 연동 화면은 ChartWorkspace이며, 아래 데이터는 공개 레포에서 UI 골격만 확인시키기 위한 고정 샘플이다.

export const systemStatus = [
  { label: "Bot Engine", value: "Armed", tone: "ruby", detail: "simulation guard active" },
  { label: "API Gateway", value: "Stable", tone: "success", detail: "42 ms avg latency" },
  { label: "Data Feed", value: "Live", tone: "success", detail: "BTCUSDT stream locked" },
  { label: "Chart Source", value: "Kline", tone: "neutral", detail: "15m primary timeframe" },
];

export const strategySwitches = [
  { label: "Structure Strategy", checked: true, detail: "entry rules synced" },
  { label: "Zone Filter", checked: true, detail: "structure filter enabled" },
  { label: "Volatility Guard", checked: false, detail: "awaiting risk threshold" },
];

export const eventFeed = [
  { time: "09:42:18", level: "info", message: "Position overlay snapshot refreshed" },
  { time: "09:40:02", level: "risk", message: "BTCUSDT upper zone retested" },
  { time: "09:36:55", level: "info", message: "Structure zones synchronized" },
  { time: "09:31:11", level: "warn", message: "Funding window begins in 28 minutes" },
];

export const chartSignals = [
  { label: "Momentum", value: "Bullish", tone: "success" },
  { label: "Structure", value: "Upper Band", tone: "ruby" },
  { label: "Volatility", value: "Compressed", tone: "warning" },
  { label: "Execution", value: "Standby", tone: "neutral" },
];

export const miniMetrics = [
  { label: "Mark Price", value: "69,248.5", unit: "USDT", tone: "primary" },
  { label: "Session P&L", value: "+1.82", unit: "%", tone: "success" },
  { label: "Open Risk", value: "0.42", unit: "R", tone: "warning" },
  { label: "Active Zones", value: "7", unit: "zones", tone: "ruby" },
];

export const analyticsStats = [
  { label: "Total P&L", value: "+18,420", unit: "USDT", tone: "success", delta: "+7.4% MTD" },
  { label: "Today P&L", value: "+642", unit: "USDT", tone: "success", delta: "4 wins / 1 loss" },
  { label: "Win Rate", value: "61.8", unit: "%", tone: "primary", delta: "last 90 trades" },
  { label: "Drawdown", value: "-3.2", unit: "%", tone: "red", delta: "risk within band" },
  { label: "Profit Factor", value: "1.74", unit: "", tone: "primary", delta: "stable" },
  { label: "Avg R", value: "+0.38", unit: "R", tone: "success", delta: "per closed trade" },
];

export const strategyRows = [
  { strategy: "Structure Strategy", trades: 38, winRate: "63%", pnl: "+8,210", risk: "Medium" },
  { strategy: "Zone Filter", trades: 27, winRate: "59%", pnl: "+5,480", risk: "Low" },
  { strategy: "Funding Filter", trades: 12, winRate: "67%", pnl: "+2,110", risk: "Low" },
  { strategy: "Volatility Guard", trades: 9, winRate: "44%", pnl: "-420", risk: "Watch" },
];

export const recentTrades = [
  { time: "09:21", symbol: "BTCUSDT", side: "Long", result: "+0.84R", status: "Closed" },
  { time: "08:58", symbol: "ETHUSDT", side: "Short", result: "+0.31R", status: "Closed" },
  { time: "08:12", symbol: "BTCUSDT", side: "Long", result: "-0.52R", status: "Stopped" },
  { time: "07:44", symbol: "SOLUSDT", side: "Long", result: "+1.12R", status: "Closed" },
];

export const settingsSections = [
  {
    title: "General",
    items: [
      { label: "Workspace density", type: "segmented", value: "Balanced", options: ["Compact", "Balanced", "Spacious"] },
      { label: "Session mode", type: "segmented", value: "Simulation", options: ["Live", "Simulation"] },
      { label: "Auto lock inactive controls", type: "switch", value: true },
    ],
  },
  {
    title: "Chart",
    items: [
      { label: "Default instrument", type: "input", value: "BTCUSDT" },
      { label: "Default timeframe", type: "segmented", value: "15m", options: ["5m", "15m", "1h", "4h"] },
      { label: "Show strategy overlays", type: "switch", value: true },
    ],
  },
  {
    title: "Strategy",
    items: [
      { label: "Max position risk", type: "input", value: "0.50 R" },
      { label: "Entry confirmation", type: "segmented", value: "Strict", options: ["Loose", "Strict"] },
      { label: "Funding guard", type: "switch", value: true },
    ],
  },
  {
    title: "API & Alerts",
    items: [
      { label: "Bybit API status", type: "readonly", value: "Connected" },
      { label: "Telegram alerts", type: "switch", value: true },
      { label: "Critical event sound", type: "switch", value: false },
    ],
  },
];
