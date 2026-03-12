//// frontend/src/components/OtpGate.jsx
import { useEffect, useState, useCallback, useRef } from "react";
import "./OtpGate.css";

/**
 * 앱 전체를 덮는 Google OTP 팝업
 * - 백엔드 /api/auth/otp/status 로 세션 존재 여부를 확인
 * - 없으면 팝업을 띄우고 /api/auth/otp/verify 로 코드 검증 요청
 * - 성공 시 팝업 닫기
 */
export default function OtpGate() {
    const [checking, setChecking] = useState(true);     // 최초 세션 체크 중인지
    const [visible, setVisible] = useState(false);      // 팝업 표시 여부
    const [digits, setDigits] = useState(["", "", "", "", "", ""]);
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState("");
    const [locked, setLocked] = useState(false);      // 시도 횟수 초과로 잠긴 상태인지 여부
    const [isDesktopLike, setIsDesktopLike] = useState(false);  //  데스크탑 판정 여부

    const inputsRef = useRef([]);

    // 세션 상태 체크
    const checkStatus = useCallback(async () => {
        try {
            const res = await fetch("/api/auth/otp/status", {
                method: "GET",
                // 쿠키 전송을 명시적으로 보장하기 위해 credentials 포함
                credentials: "include",
            });
            if (res.ok) {
                // 이미 유효한 세션 → 팝업 안 띄움
                setVisible(false);
            } else {
                setVisible(true);
            }
        } catch (e) {
            // 요청 실패 시에도 OTP 팝업을 열어 차단 유지
            setVisible(true);
        } finally {
            setChecking(false);
        }
    }, []);

    // 데스크탑/모바일·패드 판정 (아이패드는 무조건 모바일/패드 취급)
    useEffect(() => {
        const detectDesktopLike = () => {
            if (typeof navigator === "undefined") return false;

            const ua = navigator.userAgent || "";
            const platform = navigator.platform || "";
            const maxTouchPoints = navigator.maxTouchPoints || 0;

            // iPad 구분: 옛날 UA + iPadOS( MacIntel + 터치 ) 둘 다 처리
            const isIPadUA = /\biPad\b/i.test(ua);
            const isIPadOS =
                platform === "MacIntel" && maxTouchPoints > 1; // iPadOS Safari가 Mac처럼 보이는 문제

            const isIPad = isIPadUA || isIPadOS;
            const isIPhone = /iPhone/i.test(ua);
            const isAndroid = /Android/i.test(ua);

            // 아이패드/아이폰/안드로이드는 모두 모바일·패드 취급
            if (isIPad || isIPhone || isAndroid) {
                return false;
            }

            // 나머지는 데스크탑/노트북 취급
            return true;
        };

        setIsDesktopLike(detectDesktopLike());
    }, []);

    useEffect(() => {
        checkStatus();
    }, [checkStatus]);

    // OTP 팝업이 열릴 때 첫 번째 입력칸 포커스
    useEffect(() => {
        if (!visible || submitting || locked) return;
        if (typeof document === "undefined") return;

        const rafId = requestAnimationFrame(() => {
            const firstInput = inputsRef.current[0];
            if (!firstInput) return;

            const active = document.activeElement;
            const isOtpInputFocused =
                active && inputsRef.current.includes(active);

            if (isOtpInputFocused) return;
            if (
                active &&
                active !== document.body &&
                active !== document.documentElement
            ) {
                return;
            }

            firstInput.focus();
        });

        return () => cancelAnimationFrame(rafId);
    }, [visible, submitting, locked]);

    // 숫자 입력 핸들러
    const handleChange = (idx, value) => {
        if (value.length > 1) {
            value = value.slice(-1);
        }
        if (value && !/^[0-9]$/.test(value)) {
            return;
        }

        const next = [...digits];
        next[idx] = value;
        setDigits(next);
        setError("");

        if (value && idx < 5) {
            const nextInput = inputsRef.current[idx + 1];
            if (nextInput) nextInput.focus();
        }
    };

    const handleKeyDown = (idx, e) => {
        if (e.key === "Backspace" && !digits[idx] && idx > 0) {
            const prevInput = inputsRef.current[idx - 1];
            if (prevInput) prevInput.focus();
        }
    };

    const extractOtp6FromText = (text) => {
        const digitsOnly = String(text ?? "").replace(/\D/g, "");
        if (digitsOnly.length < 6) return null;
        return digitsOnly.slice(-6);
    };

    const applyOtp6 = (otp6) => {
        const next = otp6.split("");
        setDigits(next);
        setError("");
        const lastInput = inputsRef.current[5];
        if (lastInput) lastInput.focus();
    };

    const focusFirstInput = () => {
        const firstInput = inputsRef.current[0];
        if (firstInput) firstInput.focus();
    };

    const handlePasteEvent = (e) => {
        if (submitting || locked) return;
        const text = e.clipboardData?.getData("text") ?? "";
        const otp6 = extractOtp6FromText(text);
        if (!otp6) return;
        e.preventDefault();
        applyOtp6(otp6);
    };

    const handlePasteClick = async () => {
        if (submitting || locked) return;

        if (typeof navigator === "undefined" || !navigator.clipboard?.readText) {
            setError(
                "클립보드에서 코드를 읽을 수 없습니다.\n첫 칸에 붙여넣기(롱프레스/Ctrl+V)로 입력해 주세요."
            );
            focusFirstInput();
            return;
        }

        try {
            const text = await navigator.clipboard.readText();
            const otp6 = extractOtp6FromText(text);
            if (!otp6) {
                setError(
                    "클립보드에 6자리 숫자가 없습니다.\n첫 칸에 붙여넣기(롱프레스/Ctrl+V)로 입력해 주세요."
                );
                focusFirstInput();
                return;
            }
            applyOtp6(otp6);
        } catch (e) {
            focusFirstInput();
        }
    };

    const code = digits.join("");

    // OTP 제출
    const handleSubmit = async () => {
        if (code.length !== 6) {
            setError("6자리 코드를 모두 입력해 주세요.");
            return;
        }

        //  이미 잠겨 있으면 더 이상 요청 보내지 않음
        if (locked) return;

        setSubmitting(true);
        setError("");

        try {
            const res = await fetch("/api/auth/otp/verify", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                credentials: "include",
                body: JSON.stringify({ code }),
            });

                        if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                const detail = data?.detail;

                // 1)  실패 횟수 초과(429) — 잠금 처리
                if (
                    detail &&
                    typeof detail === "object" &&
                    detail.code === "OTP_TOO_MANY_ATTEMPTS"
                ) {
                    const sec = detail.retry_after_seconds ?? 0;
                    const min = Math.ceil(sec / 60) || 1;

                    // 숫자 모두 초기화
                    setDigits(["", "", "", "", "", ""]);

                    // 잠금 플래그 ON → 자동 제출/입력 막기
                    setLocked(true);

                    setError(
                        `시도 횟수를 너무 많이 초과했습니다.\n약 ${min}분 후에 다시 시도해 주세요.`
                    );
                    return;
                }

                // 2)  잘못된 코드(INVALID_OTP_CODE)
                if (
                    detail === "INVALID_OTP_CODE" ||
                    (detail &&
                        typeof detail === "object" &&
                        detail.code === "INVALID_OTP_CODE")
                ) {
                    setError("코드가 올바르지 않습니다. 다시 시도해 주세요.");

                    // 틀린 코드 → 입력값 초기화 + 첫 칸 포커스
                    setDigits(["", "", "", "", "", ""]);
                    if (inputsRef.current[0]) {
                        inputsRef.current[0].focus();
                    }
                    return;
                }

                // 3) 그 외 알 수 없는 케이스
                setError("인증 중 오류가 발생했습니다.");
                return;
            }


            // 성공: 팝업 닫기
            setVisible(false);

            // OTP 인증 성공: 기존 재요청 이벤트를 유지하고
            // 전체 페이지 새로고침으로 차트/제어 상태 완전 재초기화
            if (typeof window !== "undefined") {
                window.dispatchEvent(new Event("market-workbench:otp-auth-success"));
                window.location.reload();
            }
        } catch (e) {
            setError("서버에 연결할 수 없습니다.");
        } finally {
            setSubmitting(false);
        }
    };

    //  6자리 모두 입력되면 자동 제출
    useEffect(() => {
        // 이미 서버로 보내는 중(submitting=true)이면 중복 전송 방지
        //  잠겨 있을 때(locked=true)는 자동 제출 막기
        if (code.length === 6 && !submitting && !locked) {
            handleSubmit();
        }
    }, [code, submitting, locked]);


    // 엔터로도 전송 (Enter 눌렀을 때)
    const handleFormSubmit = (e) => {
        e.preventDefault();
        if (!submitting) {
            handleSubmit();
        }
    };

    // 아직 세션 체크 중이면 아무것도 렌더링하지 않음
    if (checking) return null;
    if (!visible) return null;

    return (
        <div className="otp-overlay">
            <form
                className={`otp-card ${
                    isDesktopLike ? "otp-card-desktop" : "otp-card-mobile"
                }`}
                onSubmit={handleFormSubmit}
            >
                <div className="otp-title">Security Verification</div>
                <div className="otp-sub">Google 2FA Code</div>

                <div className="otp-input-row">
                    {digits.map((v, idx) => (
                        <input
                            key={idx}
                            type="text"
                            inputMode="numeric"
                            autoComplete="one-time-code"
                            maxLength={1}
                            className="otp-input"
                            value={v}
                            onChange={(e) => handleChange(idx, e.target.value)}
                            onKeyDown={(e) => handleKeyDown(idx, e)}
                            onPaste={handlePasteEvent}
                            ref={(el) => (inputsRef.current[idx] = el)}
                            disabled={submitting || locked}  //  잠금/전송 중 입력 금지
                        />
                    ))}
                </div>
                <div className="otp-paste-row">
                    <button
                        type="button"
                        className="otp-paste-btn"
                        onClick={handlePasteClick}
                        disabled={submitting || locked}
                    >
                        Paste
                    </button>
                </div>
                <div className="otp-error">{error}</div>
                <div className="otp-status">Market Workbench · Google 2FA</div>
            </form>
        </div>
    );
}
