export const systemStatus = [
  {
    label: "Bot Engine",
    value: "Armed",
    tone: "negative",
    detail: "simulation guard active",
  },
  {
    label: "API Gateway",
    value: "Stable",
    tone: "positive",
    detail: "42 ms avg latency",
  },
  {
    label: "Data Feed",
    value: "Live",
    tone: "positive",
    detail: "BTCUSDT stream locked",
  },
  {
    label: "Chart Source",
    value: "Kline",
    tone: "source",
    detail: "15m primary timeframe",
  },
];

export const strategySwitches = [
  {
    label: "Structure Zone",
    checked: true,
    detail: "zone signal synced",
  },
  {
    label: "Zone Validation",
    checked: true,
    detail: "structure validation enabled",
  },
  {
    label: "Volatility Guard",
    checked: false,
    detail: "awaiting risk threshold",
  },
];

export const eventFeed = [
  {
    time: "09:42:18",
    level: "info",
    message: "Position overlay snapshot refreshed",
  },
  {
    time: "09:40:02",
    level: "risk",
    message: "BTCUSDT upper zone retested",
  },
  {
    time: "09:36:55",
    level: "info",
    message: "Structure zones synchronized",
  },
  {
    time: "09:31:11",
    level: "warn",
    message: "Funding window begins in 28 minutes",
  },
];

export const chartSignals = [
  { label: "Momentum", value: "Bullish", tone: "positive" },
  { label: "Structure", value: "Upper Band", tone: "negative" },
  { label: "Volatility", value: "Compressed", tone: "warning" },
  { label: "Execution", value: "Standby", tone: "source" },
];

export const analyticsStats = [
  {
    label: "Total P&L",
    value: "+18,420",
    unit: "USDT",
    detail: "+7.4% MTD",
    tone: "positive",
  },
  {
    label: "Today P&L",
    value: "+642",
    unit: "USDT",
    detail: "4 wins / 1 loss",
    tone: "positive",
  },
  {
    label: "Win Rate",
    value: "61.8",
    unit: "%",
    detail: "last 90 trades",
    tone: "source",
  },
  {
    label: "Drawdown",
    value: "-3.2",
    unit: "%",
    detail: "risk within band",
    tone: "negative",
  },
  {
    label: "Profit Factor",
    value: "1.74",
    detail: "stable",
    tone: "source",
  },
  {
    label: "Avg R",
    value: "+0.38",
    unit: "R",
    detail: "per closed trade",
    tone: "positive",
  },
];

export const strategyRows = [
  {
    strategy: "Structure Zone",
    trades: 38,
    winRate: "63%",
    pnl: "+8,210",
    risk: "Medium",
  },
  {
    strategy: "Zone Validation",
    trades: 27,
    winRate: "59%",
    pnl: "+5,480",
    risk: "Low",
  },
  {
    strategy: "Funding Filter",
    trades: 12,
    winRate: "67%",
    pnl: "+2,110",
    risk: "Low",
  },
  {
    strategy: "Volatility Guard",
    trades: 9,
    winRate: "44%",
    pnl: "-420",
    risk: "Watch",
  },
];

export const recentTrades = [
  {
    time: "09:21",
    symbol: "BTCUSDT",
    side: "Long",
    result: "+0.84R",
    status: "Closed",
  },
  {
    time: "08:58",
    symbol: "ETHUSDT",
    side: "Short",
    result: "+0.31R",
    status: "Closed",
  },
  {
    time: "08:12",
    symbol: "BTCUSDT",
    side: "Long",
    result: "-0.52R",
    status: "Stopped",
  },
  {
    time: "07:44",
    symbol: "SOLUSDT",
    side: "Long",
    result: "+1.12R",
    status: "Closed",
  },
];

export const settingsSections = [
  {
    title: "General",
    detail: "Workspace behavior for daily monitoring.",
    items: [
      {
        label: "Workspace density",
        type: "segmented",
        value: "Balanced",
        options: ["Compact", "Balanced", "Spacious"],
      },
      {
        label: "Session mode",
        type: "segmented",
        value: "Simulation",
        options: ["Live", "Simulation"],
      },
      {
        label: "Auto lock inactive controls",
        type: "switch",
        value: true,
      },
    ],
  },
  {
    title: "Chart",
    detail: "Default market view and overlay behavior.",
    items: [
      {
        label: "Default instrument",
        type: "input",
        value: "BTCUSDT",
      },
      {
        label: "Default timeframe",
        type: "segmented",
        value: "15m",
        options: ["5m", "15m", "1h", "4h"],
      },
      {
        label: "Show strategy overlays",
        type: "switch",
        value: true,
      },
    ],
  },
  {
    title: "Strategy",
    detail: "Risk posture and entry confirmation rules.",
    items: [
      {
        label: "Max position risk",
        type: "input",
        value: "0.50 R",
      },
      {
        label: "Entry confirmation",
        type: "segmented",
        value: "Strict",
        options: ["Loose", "Strict"],
      },
      {
        label: "Funding guard",
        type: "switch",
        value: true,
      },
    ],
  },
  {
    title: "API & Alerts",
    detail: "Connection status and operator notification preferences.",
    items: [
      {
        label: "Bybit API status",
        type: "readonly",
        value: "Connected",
      },
      {
        label: "Telegram alerts",
        type: "switch",
        value: true,
      },
      {
        label: "Critical event sound",
        type: "switch",
        value: false,
      },
    ],
  },
];
