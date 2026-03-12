# Zabbix Agent2 운영 메모

> 운영 환경 정리용 템플릿.
> 실제 호스트명, IP, 토큰, 스크린샷은 별도 관리.

## 목표
- (A) systemd 서비스 다운 감지: market-workbench-api.service / market-workbench-bot.service
- (B) localhost /healthz 실패 및 지연 감지
- (C) 디스크 부족 사전 감지
- (D) 텔레그램 알림 수신

## 서버 환경
- OCI 인스턴스:
- OS:
- Docker 버전:
- 기존 운영:
  - market-workbench-api.service
  - market-workbench-bot.service
  - mysql-container

## 설치/설정 로그
- script 로그 파일: docs/zabbix/00_session.log
- 주요 명령 출력 캡쳐:
  - docker compose ps
  - systemctl status zabbix-agent2
  - systemctl status market-workbench-api
  - curl http://127.0.0.1:8000/healthz

## Zabbix 구성
### 4.1 Zabbix Server (Docker)
- compose 파일:
- 접속 방식(SSH 터널 등):
- 관리자 비밀번호 변경 여부:

### 4.2 Zabbix Agent2 (Host)
- /etc/zabbix/zabbix_agent2.conf 변경 요약:
- Docker network subnet:
- 재시작 로그:

### 4.3 템플릿/트리거
1) systemd: market-workbench-api down
2) systemd: market-workbench-bot down
3) /healthz 문자열 매칭 실패
4) /healthz 응답 지연
5) 디스크 90%

## 알람 테스트 결과
- 서비스 stop/start 시 Problem 생성/해제 화면
- 알림(텔레그램) 수신 화면(토큰/ID 마스킹)
