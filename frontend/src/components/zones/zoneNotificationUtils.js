// 차트 내부 Zone 구조를 알림 계산에 맞는 공통 형태로 맞추기 위함
export function normalizeZoneBoxes(rawBoxes) {
  if (!Array.isArray(rawBoxes)) return [];

  return rawBoxes.map((b) => {
    const intervalMin = b.intervalMin ?? b.tf ?? 15;

    const rawSide = (b.side || b.direction || "Long").toString();
    const side = rawSide.toLowerCase() === "short" ? "Short" : "Long";

    const boxTop = Number(b.boxTop ?? b.topPrice ?? b.upper ?? 0);
    const boxBottom = Number(b.boxBottom ?? b.bottomPrice ?? b.lower ?? 0);
    const isBroken = Boolean(b.isBroken === true || b.broken === true);

    const entryPrice =
      side === "Long"
        ? Number(b.entryPrice ?? boxTop)
        : Number(b.entryPrice ?? boxBottom);

    const stopPrice =
      b.stopPrice != null
        ? Number(b.stopPrice)
        : side === "Long"
        ? Number(b.slPrice ?? boxBottom)
        : Number(b.slPrice ?? boxTop);

    const id =
      b.id ??
      `${intervalMin}-${b.startTime ?? b.startTs ?? ""}-${side}-${boxTop}-${boxBottom}`;

    const startTs =
      typeof b.startTs === "number"
        ? b.startTs
        : b.startTime
        ? Number(b.startTime)
        : NaN;

    return {
      id,
      intervalMin,
      side,
      boxTop: Math.max(boxTop, boxBottom),
      boxBottom: Math.min(boxTop, boxBottom),
      entryPrice,
      stopPrice,
      isBroken,
      isActive: !!b.isActive,
      // 수정 모달 저장 시 다시 서버로 보내야 하는 메타데이터
      symbol: b.symbol ?? "BTCUSDT",
      startTimeMs: Number.isFinite(startTs) ? startTs : null,
    };
  });
}

// 현재 차트 화면 안에 완전히 들어온 Zone만 남기기 위함
export function getVisibleZoneBoxes(boxes, visibleHigh, visibleLow) {
  if (!Array.isArray(boxes)) return [];

  if (!Number.isFinite(visibleHigh) || !Number.isFinite(visibleLow)) {
    return [];
  }

  const hi = Math.max(visibleHigh, visibleLow);
  const lo = Math.min(visibleHigh, visibleLow);

  return boxes.filter((box) => {
    if (!box || box.isBroken) return false;

    const top = box.boxTop;
    const bottom = box.boxBottom;

    if (!Number.isFinite(top) || !Number.isFinite(bottom)) {
      return false;
    }

    const topInside = top <= hi && top >= lo;
    const bottomInside = bottom <= hi && bottom >= lo;

    return topInside && bottomInside;
  });
}


// 현재 화면 중앙 가격과 가까운 Zone부터 우선 노출하기 위함
export function selectTop15AroundMidPrice(boxes, midPrice) {
  if (!Array.isArray(boxes) || boxes.length === 0) return [];

  const up = [];
  const down = [];

  for (const box of boxes) {
    if (box.entryPrice > midPrice) {
      up.push(box);
    } else {
      down.push(box);
    }
  }

  const byDistance = (a, b) =>
    Math.abs(a.entryPrice - midPrice) - Math.abs(b.entryPrice - midPrice);

  up.sort(byDistance);
  down.sort(byDistance);

  const upPicked = up.slice(0, 5);
  const downPicked = down.slice(0, 5);

  let picked = [...upPicked, ...downPicked];

  if (picked.length < 15) {
    const restUp = up.slice(5);
    const restDown = down.slice(5);
    const rest = [...restUp, ...restDown];

    for (const box of rest) {
      if (picked.length >= 15) break;
      picked.push(box);
    }
  }

  picked.sort((a, b) => b.entryPrice - a.entryPrice);
  return picked;
}

// 박스 높이가 현재가 대비 몇 퍼센트인지 표시하기 위함
export function computeBoxRangePercent(box, currentPrice) {
  if (!currentPrice || currentPrice <= 0) return null;

  const height = Math.abs(box.boxTop - box.boxBottom);
  if (!height) return 0;

  const pct = (height / currentPrice) * 100;
  return Number(pct.toFixed(2));
}


// 알림 패널 한 줄에 바로 쓸 수 있는 형태로 변환하기 위함
export function buildNotificationItemsFromZones(boxes, currentPrice) {
  return boxes.map((box) => {
    const pct = computeBoxRangePercent(box, currentPrice);

    return {
      id: box.id,
      timeframe: box.intervalMin,
      side: box.side,
      entryPrice: box.entryPrice,
      stopPrice: box.stopPrice,
      percentageText: pct == null ? "-" : `${pct.toFixed(2)}%`,
      isActive: !!box.isActive,

      // 수정 저장 시 다시 서버로 보낼 필수 식별자
      symbol: box.symbol ?? "BTCUSDT",
      intervalMin: box.intervalMin,
      startTimeMs: box.startTimeMs ?? null,

      // 높이 퍼센트 수정 시 같은 기준 가격으로 다시 계산하기 위함
      currentPriceAtBuild:
        typeof currentPrice === "number" && Number.isFinite(currentPrice)
          ? currentPrice
          : null,
    };
  });
}
