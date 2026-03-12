//// frontend/src/components/ZoneNotificationList.jsx
import { useMemo, useState, useCallback } from "react";
import { useZoneNotifications } from "../contexts/ZoneNotificationContext";
import { saveZoneStateToServer } from "../api/zoneState";
import "./ZoneNotificationList.css";

/**
 * 한 줄 Notification 카드
 * - 포맷: "15 • Long • 86470.91 • 86000.91 • 0.21%"
 */
function ZoneNotificationItem({
    item,
    isHovered,
    onHoverIn,
    onHoverOut,
    onSidePillClick,
    onEditClick,
}) {
    const { timeframe, side, entryPrice, stopPrice, percentageText, isActive } = item;

    const sideLabel = side === "Short" ? "Short" : "Long";
    const sideClass = side === "Short" ? "short" : "long";

    const mainText = `${timeframe} • ${entryPrice.toFixed(
        2
    )} • ${stopPrice.toFixed(2)} • ${percentageText}`;

    const activeClass = isActive ? " is-active" : "";

    return (
        <div
            className={`zone-noti-item${isHovered ? " is-hovered" : ""}${activeClass}`}
            onMouseEnter={onHoverIn}
            onMouseLeave={onHoverOut}
        >
            <div className="zone-noti-main">
                <button
                    type="button"
                    className={`zone-noti-side-pill ${sideClass}`}
                    onClick={(e) => {
                        e.stopPropagation();
                        if (typeof onSidePillClick === "function") {
                            onSidePillClick();
                        }
                    }}
                >
                    {sideLabel}
                </button>
                <span className="zone-noti-text-line">{mainText}</span>
            </div>
            {/*  오른쪽 edit 아이콘 버튼 (편집 모달 오픈) */}
            <button
                type="button"
                className="zone-noti-edit-btn"
                onClick={(e) => {
                    e.stopPropagation();
                    if (typeof onEditClick === "function") {
                        onEditClick(item);   //  현재 알림 item을 그대로 전달
                    }
                }}
            >
                <img
                    src="/edit-icon.png"
                    alt="편집"
                    className="zone-noti-edit-icon"
                />
            </button>
        </div>
    );
}

/**
 * 왼쪽 20% 컨테이너 전체를 채우는 Notification 리스트
 */
export default function ZoneNotificationList() {
    const {
        items,
        hoveredBoxId,
        setHoveredBoxId,
        toggleBoxActive,
    } = useZoneNotifications();

    //  편집 중인 알림 정보 (없으면 null)
    const [editingItem, setEditingItem] = useState(null);
    //  진입가격 입력 값
    const [entryInput, setEntryInput] = useState("");
    //  박스 높이 (%) 입력 값 (0.10 ~ 0.50)
    const [heightInput, setHeightInput] = useState(0.2);
    //  모달을 열었을 때의 원래 박스 높이 (%)
    const [heightInitial, setHeightInitial] = useState(0.2);

    // edit 아이콘 클릭 시 모달 열기
    const openEditModal = useCallback((item) => {
        setEditingItem(item);

        // 진입가격 초기값 (소수 2자리)
        if (typeof item.entryPrice === "number") {
            setEntryInput(item.entryPrice.toFixed(2));
        } else {
            setEntryInput("");
        }

        // percentageText: 예) "0.21%"
        const rawPct = parseFloat((item.percentageText || "").replace("%", ""));
        let safePct = Number.isFinite(rawPct) ? rawPct : 0.2;

        // 범위를 0.10 ~ 0.50으로 클램프
        if (safePct < 0.1) safePct = 0.1;
        if (safePct > 0.5) safePct = 0.5;

        //  현재 높이 값 세팅
        setHeightInput(safePct);
        //  "모달을 열 당시"의 원본 높이 값도 따로 저장
        setHeightInitial(safePct);
    }, []);

    const closeEditModal = useCallback(() => {
        setEditingItem(null);
    }, []);

    // 원본 되돌리기: entry_override 를 NULL 로 저장
    const handleResetToBase = useCallback(async () => {
        if (!editingItem) return;

        if (!editingItem.startTimeMs) {
            alert("이 박스의 시작 시간이 없어 되돌릴 수 없습니다.");
            return;
        }

        const startTimeIso = new Date(editingItem.startTimeMs).toISOString();

        try {
            await saveZoneStateToServer({
                symbol: editingItem.symbol || "BTCUSDT",
                intervalMin: editingItem.intervalMin,
                startTime: startTimeIso,
                side: (editingItem.side || "Long").toUpperCase() === "SHORT" ? "SHORT" : "LONG",
                isActive: editingItem.isActive ?? true,
                entryOverride: null,
            });

            // 모달 닫기
            setEditingItem(null);
        } catch (e) {
            console.error("[ZoneNotif] 원본 되돌리기 실패:", e);
            alert("원본 되돌리기에 실패했습니다. 콘솔 로그를 확인해 주세요.");
        }
    }, [editingItem]);

    // "저장" 버튼 클릭 시 DB 연동
        // "저장" 버튼 클릭 시 DB 연동
    const handleSave = useCallback(async () => {
        if (!editingItem) return;

        // 1) 진입가격 숫자 검증
        const entryFromInput = Number(entryInput);
        if (!Number.isFinite(entryFromInput) || entryFromInput <= 0) {
            alert("유효한 진입가격을 입력해 주세요.");
            return;
        }

        // 2) 박스 높이(%) 숫자/범위 검증
        const height = Number(heightInput);
        if (!Number.isFinite(height) || height < 0.1 || height > 0.5) {
            alert("박스 높이는 0.10 ~ 0.50% 범위에서만 설정할 수 있습니다.");
            return;
        }

        if (!editingItem.startTimeMs) {
            alert("이 박스의 시작 시간이 없어 저장할 수 없습니다.");
            return;
        }

        // 3) 기본값: 사용자가 입력한 진입가 그대로
        let finalEntry = entryFromInput;

        // 4) "높이(%)가 실제로 바뀌었는지" 체크
        const heightChanged =
            Math.abs(height - heightInitial) >= 0.001; // 0.001% 이상 차이 나면 변경으로 간주

        // 5) 높이 변경이 있었다면 → 실시간 가격 대비 퍼센트에 맞게 entry 재계산
        const currentPrice =
            typeof editingItem.currentPriceAtBuild === "number" &&
            Number.isFinite(editingItem.currentPriceAtBuild)
                ? editingItem.currentPriceAtBuild
                : null;

        const stop = Number(editingItem.stopPrice);
        const sideLower = (editingItem.side || "Long").toLowerCase(); // "long" | "short"

        if (
            heightChanged &&
            Number.isFinite(stop) &&
            Number.isFinite(currentPrice) &&
            currentPrice > 0
        ) {
            // 목표 박스 높이(절대값, 가격 단위)
            const targetHeightAbs = (currentPrice * height) / 100;

            if (sideLower === "long") {
                // 롱: SL(손절) 아래, 진입은 위쪽으로 떨어진 거리
                finalEntry = stop + targetHeightAbs;
            } else if (sideLower === "short") {
                // 숏: SL 위, 진입은 아래쪽으로 떨어진 거리
                finalEntry = stop - targetHeightAbs;
            }
        }

        const startTimeIso = new Date(editingItem.startTimeMs).toISOString();

        try {
            await saveZoneStateToServer({
                symbol: editingItem.symbol || "BTCUSDT",
                intervalMin: editingItem.intervalMin,
                startTime: startTimeIso,
                side: (editingItem.side || "Long").toUpperCase() === "SHORT" ? "SHORT" : "LONG",
                isActive: editingItem.isActive ?? true,
                entryOverride: finalEntry,
            });

            // 성공 시 모달 닫기
            setEditingItem(null);
        } catch (e) {
            console.error("[ZoneNotif] 저장 실패:", e);
            alert("저장 중 오류가 발생했습니다. 콘솔 로그를 확인해 주세요.");
        }
    }, [editingItem, entryInput, heightInput, heightInitial]);


    const list = useMemo(
        () => items ?? [],
        [items, hoveredBoxId] // hoveredBoxId 변경도 반영해 리스트 계산을 최신화
    );

    return (
        <div className="zone-noti-root">
            <div className="zone-noti-header">Structure Zone List</div>
            <div className="zone-noti-scroll">
                {list.length === 0 ? (
                    <div className="zone-noti-empty">
                        차트 안에 보이는 활성 Structure Zone이 없습니다.
                    </div>
                ) : (
                    list.map((item) => {
                        // 알림 항목 id와 차트 오버레이 id를 같은 값으로 유지하기 위함
                        const isHovered = hoveredBoxId === item.id;

                        return (
                            <ZoneNotificationItem
                                key={item.id}
                                item={item}
                                isHovered={isHovered}
                                onHoverIn={() => setHoveredBoxId(item.id)}
                                onHoverOut={() => setHoveredBoxId(null)}
                                onSidePillClick={() => toggleBoxActive(item.id)}
                                onEditClick={openEditModal}
                            />
                        );
                    })
                )}
            </div>
            {/*  편집 모달 (화면 중앙 고정) */}
            {editingItem && (
                <div
                    className="zone-edit-modal-backdrop"
                    onClick={closeEditModal}
                >
                    <div
                        className="zone-edit-modal"
                        onClick={(e) => e.stopPropagation()}
                    >
                        <div className="zone-edit-modal-title">
                            Structure Zone 수정
                        </div>
                        <div className="zone-edit-modal-subtitle">
                            {editingItem.timeframe}분 • {editingItem.side}
                        </div>

                        {/* 진입가격 입력 */}
                        <div className="zone-edit-modal-row">
                            <label className="zone-edit-label">
                                진입가격
                            </label>
                            <input
                                type="number"
                                className="zone-edit-input"
                                value={entryInput}
                                onChange={(e) => setEntryInput(e.target.value)}
                            />
                        </div>

                        {/* 박스 높이 (%) 입력 + 슬라이더 */}
                        <div className="zone-edit-modal-row">
                            <label className="zone-edit-label">
                                박스 높이 (%)
                            </label>

                            <div className="zone-edit-height-row">
                                <input
                                    type="number"
                                    min="0.10"
                                    max="0.50"
                                    step="0.01"
                                    className="zone-edit-input"
                                    value={heightInput}
                                    onChange={(e) =>
                                        setHeightInput(
                                            parseFloat(e.target.value) || 0.1
                                        )
                                    }
                                />
                                <span className="zone-edit-percent-suffix">
                                    %
                                </span>
                            </div>

                            <input
                                type="range"
                                min="0.10"
                                max="0.50"
                                step="0.01"
                                className="zone-edit-slider"
                                value={heightInput}
                                onChange={(e) =>
                                    setHeightInput(
                                        parseFloat(e.target.value) || 0.1
                                    )
                                }
                            />

                            <div className="zone-edit-help">
                                0.10% ~ 0.50% 범위에서만 설정할 수 있습니다.
                            </div>
                        </div>

                        {/* 버튼 영역 */}
                        <div className="zone-edit-modal-actions">
                            {/* 원본 되돌리기 */}
                            <button
                                type="button"
                                className="zone-edit-btn reset"
                                onClick={handleResetToBase}
                            >
                                원본
                            </button>

                            {/* 오른쪽: 취소 / 저장 */}
                            <div className="zone-edit-actions-right">
                                <button
                                    type="button"
                                    className="zone-edit-btn cancel"
                                    onClick={closeEditModal}
                                >
                                    취소
                                </button>
                                <button
                                    type="button"
                                    className="zone-edit-btn save"
                                    onClick={handleSave}
                                >
                                    저장
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
