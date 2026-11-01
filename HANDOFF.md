# HANDOFF — market-alert-relay

다른 Claude 세션이 이어받기 위한 인수인계 문서. (작성: 2026-11-01)
사용자와의 대화는 **한국어(반말·짧은 지시)** 로 진행된다. 답도 한국어로, 짧고 결론 먼저.

---

## 1. 이 시스템이 하는 일

FinancialJuice(FJ) 속보·경제일정 + 미국/유럽/일본 지표 원출처를 텔레그램 채널 **`@haesunking`** 으로 릴레이.
사용자는 **해외선물 트레이더**. 목표는 "무료 중에서 최고" (블룸버그/로이터급을 지향하되 **돈은 안 씀**).

| 구성요소 | 파일 | 동작 |
|---|---|---|
| 속보 릴레이 | `scripts/financialjuice_relay.py` | FJ 뉴스 API를 1초 간격 폴링 → 번역 → 전송 |
| 발표 5분 전 알림 | `scripts/calendar_prealert.py` | 릴레이 루프 안에서 동작 |
| 지표 원출처 감시 | `scripts/release_watch.py` | 발표 순간 원문에서 숫자 직접 추출 → 🚨 알림 |
| 경제일정 다이제스트 | `scripts/calendar_relay.py` + `.github/workflows/calendar.yml` | 매일 06:25 KST/ET, 월요일 주간 |
| 상시 구동 | `.github/workflows/financialjuice-relay.yml` | 잡 1개 약 5.5시간, 파이썬 10분 구간 반복 |
| 감시견 | `.github/workflows/relay-watchdog.yml` | 하트비트 30분 넘게 끊기면 알림 + 재기동 |
| 프루브(무관) | `fj-ws-probe.yml`, `scripts/fj_ws_probe.py` | 수동 실행 전용 조사 도구. 검토 대상에서 제외하라고 했음 |

### 루프 구조 (financialjuice_relay.py `run_loop`)
매 주기 순서가 중요: **`calendar_prealert.tick` → `release_watch.tick` → `main()`(뉴스)**.
지표가 뉴스 배치(건당 3.5초 간격)에 밀리지 않게 하려고 지표를 먼저 돌린다. 각 단계 예외는 서로 격리돼 있다.

### 워크플로 동작
- `git reset --hard origin/main` 을 10분 구간마다 해서 **main 에 push 하면 다음 10분 구간부터 새 코드가 돈다** (잡 재시작 불필요).
  단 워크플로 파일 자체 변경(예: pypdf 설치 스텝)은 **다음 잡(최대 5.5시간 뒤)** 부터 적용.
- 상태 파일 커밋 대상(파일별 개별 `git add`): `fj_last_newsid.txt`, `fj_heartbeat.txt`, `fj_sent_titles.json`, `cal_prealert.json`, `release_watch.json`.
- 커밋 금지(.gitignore): `.state/fj_info_cache.txt`(FJ info 토큰), `.state/fj_fail.json`, `.state/*.tmp`.
- 잡 종료 직전 `gh workflow run` 으로 다음 잡을 직접 띄움 + 15분 cron(7/23/37/53분) 보험 + watchdog(11/29/41/59분).

---

## 2. 사용자 규칙 (반드시 지킬 것)

1. **무료만.** 유료 API·VPS·구독 제안 금지 ("아직은 돈 안 쓰고 싶어").
2. **해외선물 트레이더 관점**으로 기능을 고른다.
3. **봇이 시장 방향을 해석하지 않는다.** "CPI 상회 → 매파 → 주식↓" 같은 서프라이즈 스코어링은 사용자가 **못 믿겠다고 거절**. 팩트(숫자·예상·이전)만 보낸다.
4. **`#속보` 해시태그는 release_watch 가 원출처에서 직접 뽑은 지표 메시지에만** 붙인다. 일반 뉴스에는 절대 붙이지 않는다 (한 번 잘못 붙였다가 지적받음).
5. 지표 메시지에 **링크 넣지 말 것** (FOMC 링크 넣었다가 "왜 넣냐" 지적). 파싱 실패 시에만 링크를 대체 내용으로 쓴다.
6. 지표 원출처 메시지는 **번역을 거치지 않고** 한글 라벨 + 숫자 + 캘린더 예상/이전치로 조립 (속도 + 가독성).
7. **같은 시각 발표의 사전 알림은 한 통으로 묶는다.**
8. 사용자 이메일(`CONTACT_EMAIL` 시크릿, BLS/DOL UA 용)은 **저장소에 절대 하드코딩·커밋하지 말 것**. 로그도 마스킹 유지(`re***@domain`). 다른 서비스로 보내지 말 것.
9. 커스텀 이모지 ID 같은 **공개 식별자는 시크릿 말고 코드에 그냥 넣으라**고 함 ("뭐하러 시크릿에 넣어 그냥써").
10. 사용자는 **병합 권한을 위임**했다 ("니가 병합해"). 작업 브랜치에 커밋·푸시 후 main 에도 반영한다(아래 4절 절차).
11. 코드 검토 요청 시 **`code-review` 스킬/서브에이전트를 쓰지 말고** 직접 읽어서 검토 (스킬 호출을 사용자가 거부한 적 있음).
12. 제안을 할 때는 **실측으로 확인한 것만** 말하고, 추측이면 추측이라고 밝힌다. 틀린 걸 단정했다가 정정한 적이 여러 번 있어 신뢰에 민감함.

---

## 3. 메시지 양식 (사용자 취향 — 바꾸지 말 것)

### 3-1. 속보 (financialjuice_relay.py)
```
🚨 {번역문}          ← 🚨 는 FJ Level 이 'active-critical'(빨간 뉴스)일 때만
(원문: {영문 원문})
(HH:MM)              ← 한국시간, **별도 줄**. 붙여쓰기 시도했다가 사용자가 "원복" 지시
```
번역 실패/동일하면 `{원문}\n(HH:MM)`.

### 3-2. 발표 5분 전 알림 (calendar_prealert.py)
```
⏰ 5분 후 지표 발표 (HH:MM)

🇺🇸 {한글 지표명}
   예상 X / 이전 Y
🇺🇸 ...
```

### 3-3. 지표 원출처 알림 (release_watch.py)
```
🚨 🇺🇸 미국 PCE 물가 (9월)
전월 +0.5% (예상 0.5%·이전 0.3%)
근원 +0.3% (예상 0.3%·이전 0.2%)
전년 +3.6% (예상 3.6%·이전 3.4%)
근원 전년 +3.1% (예상 3.1%·이전 3.0%)
#속보
```
- 마지막 줄 `#속보` 고정.
- 실업수당 단위는 캘린더와 맞춤: `194K`, `1.701M`.
- ECB 헤더 `(25bp 인상)`, BOJ 헤더 `(7-2 다수결)`/`(만장일치)`, BOJ 줄 `정책금리 1.25% 인상 (...)`.
- FOMC: `동결 · 목표범위 3.75~4.00%` (링크 없음).
- EIA: `원유 -1.2M` 처럼 백만배럴 부호 표기.

### 3-4. 경제일정 다이제스트 (calendar_relay.py)
헤더 `📈 주요 경제일정 (한국시간)` / `📈 이번주 주요 경제일정 (한국시간)` — "(한국시간)" 표기는 사용자 요청.

### 3-5. 시스템 알림
`⚠️ 뉴스 릴레이 오류 ...` / `✅ 뉴스 릴레이 정상화`

---

## 4. 개발·배포 절차

- 작업 브랜치: **`claude/code-review-8cidxk`**. 여기서 커밋·푸시한 다음 main 에 반영한다.
- **main 의 히스토리는 shallow 라 브랜치와 merge-base 가 없다** ("refusing to merge unrelated histories"). 그래서 main 반영은:
  ```
  git checkout main && git pull --ff-only origin main
  git checkout claude/code-review-8cidxk -- <바꾼 파일들>
  git commit -m "<같은 메시지>" && git push origin main
  git checkout claude/code-review-8cidxk
  ```
- main 에는 relay-bot 이 약 10분마다 상태 커밋을 올린다 → push 전에 항상 `pull --ff-only`.
- **force push 는 자동 모드 분류기가 차단**한다. 브랜치가 갈라지면 `git merge -s ours --allow-unrelated-histories origin/<branch>` 로 트리 유지하며 합친다.
- 커밋 메시지 끝 고정 트레일러:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01CisfET945QEvtomdeqhDyT
  ```
  모델 식별자를 커밋/코드/PR 에 넣지 말 것. PR 은 요청 없으면 만들지 말 것.
- 테스트는 실데이터 fetch + 가짜 시각으로 `tick()` 을 돌리는 시뮬레이션으로 했다(테스트 스위트 없음). pypdf 는 `python3 -m venv` 에 깔아서 시험.
- 저장소 루트에서 스크립트 실행 시 `.state/` 에 찌꺼기가 생길 수 있음 → 커밋 전 `git status` 확인, 임시 파일은 지우기.

---

## 5. release_watch 세부 (가장 손이 많이 간 부분)

### 감시 대상 (WATCHERS)
| key | 출처 | 비고 |
|---|---|---|
| cpi / empsit / ppi | BLS `*.nr0.htm` | UA 에 이메일 필수(`CONTACT_EMAIL`). 없으면 꺼짐 |
| pce / gdp | BEA RSS `apps.bea.gov/rss/rss.xml` + 본문 | PCE 는 본문에서 전월/근원/전년/근원전년 |
| fomc | Fed RSS `press_monetary.xml` | 제목 "Federal Reserve issues FOMC statement" |
| eia | `ir.eia.gov/wpsr/table1.csv` | Difference 열 = 주간 변화 |
| claims | DOL `www.dol.gov/ui/data.pdf` | HEAD 로 Last-Modified 만 매번 확인, 바뀌면 GET+pypdf. 브라우저 UA·연락처 없는 봇 UA 는 403 → 이메일 UA 또는 `Python-urllib/3`. 캐시 회피용 `?_=epoch` |
| ecb | ECB RSS `rss/press.html` → 결정문 HTML | "Monetary policy decisions" / `ecb.mpYYMMDD` |
| boj | BOJ RSS `en/rss/whatsnew.xml` → 결정문 PDF | 링크 패턴 `/mopo/mpmdeci/mpr_YYYY/kYYMMDDa.pdf`. 발표 시각 미정 → `arm_before 2h / arm_after 3h / early / slow_poll 3s` |

### 판정 방식
- 캘린더(프리알림이 30분마다 갱신한 `_cal_cache`, 6시간 앞, `include_extra=True`) 로 발표 시각을 안다.
- 발표 30분 전(`ARM_BEFORE_SECONDS=1800`) 원문 지문을 baseline 으로 저장 → 발표 후 지문이 바뀌면 발송 → `done_for` 기록. 상태는 `.state/release_watch.json` (커밋됨, 재시작에도 유지).
- 발표 전 baseline 재시도 간격 20초(`PRE_POLL_SECONDS`), 발표 후 60초간 1초, 이후 5초. 발표 후 5분(`ARM_AFTER`)까지.
- 기준을 못 잡고 발표 시각을 넘기면 **침묵**(오발송보다 낫다는 원칙).
- RSS 에 지난 결정문이 밀려나 없으면 `NO_DOC` 를 baseline 으로 삼는다(ECB/BOJ).
- 예상/이전 짝짓기: `calendar_kind(title)` 로 캘린더 항목을 종류별로 분류해 추출 항목의 kind 와 **정확히 같은 것만** 붙인다.
  - 10/29 버그: PCE 시각에 뜨는 분기 PCE(Prices Advance)·소득·소비·GDP 디플레이터가 `mom` 으로 분류돼 엉뚱한 예상치가 붙었음 → 각각 `quarterly/income/spending/deflator` 로 분리해 수정 (7228bd10).
  - ECB 는 캘린더에 `ECB Deposit Rate`(deposit) + `ECB Interest Rate`(rate) 가 뜸. rate 는 값이 가장 가까운 남은 금리(MRO)에 붙는다.
- 과거 사고: `ARM_BEFORE` 가 120초였을 때 뉴스 배치가 창을 가로질러 EIA 를 조용히 놓쳤음 → 1800초로 확대.

### 실전 확인 상태 (`.state/release_watch.json` 기준)
cpi, ppi, empsit, pce, gdp, fomc, eia, claims, ecb(10/29), boj(10/30) 모두 `done_for` 기록 = 발송됨.
**ECB/BOJ 의 실제 메시지 내용은 아직 사용자와 확인 못 함** (확인해 주겠다고 제안했지만 답 없음).

---

## 6. 진행 중 / 보류 / 거절된 것

### 보류 (사용자 행동 필요)
- **움직이는 사이렌 이모지**: 커스텀 이모지 ID `5395695537687123235` (Decoration_Pack). 텔레그램 규정상 봇이 custom_emoji 를 보내려면 **Fragment 사용자명을 산 봇**이어야 하고, 아니면 200 OK 를 주면서 엔티티를 조용히 제거함(응답 `entities: null` 로 실측). 채널 부스트(8레벨)와 무관. 사용자에게 Fragment 구매 절차(TON 지갑 → fragment.com → 사용자명 구매 → 봇에 Assign)를 안내함. **"됐다"고 하면** `SIREN_CUSTOM_EMOJI_ID` 기본값에 ID 를 코드로 넣고(시크릿 X) 검증 발송할 것. 전송 로직·400 폴백은 남아 있음.

### 제안했지만 답 없음
- 공포탐욕지수(CNN, 비공식 API) 추가 — 채널에 이미 다른 봇이 올리는 것으로 보여 중복 가능성 언급함. **이 릴레이는 공탐지수를 보내지 않는다.**
- ECB 발송 결과 확인.

### 의도적으로 안 한 것
- **FOMC 예상/이전치 짝짓기**: 캘린더의 `US Interest Rate Decision` 이 실제 발표 시각(18:00 UTC)과 다른 시각(19:00)으로 올라온 적이 있어, 감시 패턴에 넣으면 baseline 을 발표 후에 잡아 **알림을 통째로 놓칠 위험**. 넣으려면 그룹과 별개로 ±2시간 내 항목을 짝짓기 용도로만 찾는 방식이어야 함.

### 사용자가 거절함 (다시 제안하지 말 것)
- 서프라이즈 스코어링/방향 해석 (규칙 3).
- 대형 변동 시세 알림(Yahoo), 롤오버 알림, 주제 해시태그 자동 분류 등 추천 묶음 → "흠 별로네".
- 초기에 제안한 지연 단축안 1·2번 → "둘 다 맘에 안 드네".
- 장 시작 알림: CME 휴장·조기마감까지 정확히 못 할 거면 안 만드는 게 낫다고 정리됨 (미구현).
- VPS·DB·유료 데이터 등 유료/인프라 이전 제안.

### 알려진 한계
- FJ 무료 피드 자체 지연 약 10~14초(하트비트 `lag`). 사이트의 웹소켓(Azure Web PubSub `NewsHubPS`)은 토큰이 Cloudflare 챌린지 뒤에 있어 사용 불가로 결론.
- GitHub Actions 무료 티어: cron 지연, 잡 인계 공백(30초~2분).
- 번역: 비공식 무료 엔드포인트 체인 `clients5.google.com → translate.googleapis.com(gtx) → MyMemory`, 제공자당 타임아웃 5초. 전부 실패 시 원문 전송. MyMemory 는 오류를 200 으로 주므로 `responseStatus` 검사 필수(이미 반영).
- DOL 캐시 회피 쿼리가 아카마이에서 실제로 통하는지는 실전 타이밍 로그로 아직 검증 안 됨.
- `release_watch.json` 의 GDP/PPI baseline 처럼 오래된/불완전 지문이 남아 있을 수 있음(판정엔 무해, 다음 발표 때 갱신).

---

## 7. 잡음 필터 (financialjuice_relay.py)
- `NOISE_KEYWORDS`: 민방위/기상/스포츠/부고, `currency strength`(FJ 통화강도 자동 게시물).
- `NOISE_REGEXES`: `\bMO[OC]\s+imbalance\b` (MOC/MOO 불균형 — 사용자 요청으로 제거).
- 자체 게시글 제외: Morning Juice / Week Ahead / Market Wrap, 출처 없는 링크성 글.
- 같은 제목 24시간 중복 제거(`fj_sent_titles.json`).

---

## 8. 시크릿 / 환경
- `TELEGRAM_BOT_TOKEN` (저장소 시크릿)
- `CONTACT_EMAIL` (저장소 시크릿, 사용자가 직접 넣음) — BLS·DOL UA 용
- `SIREN_CUSTOM_EMOJI_ID` (선택 env, 현재 미사용·기본값 빈 문자열)
- 워크플로가 `pypdf==6.0.0` 을 `pip --user --break-system-packages` 로 설치. 실패하면 실업수당/BOJ 감시만 꺼짐.
- 이 원격 세션에는 `gh` CLI 가 없다 → GitHub 작업은 `mcp__github__*` 도구 사용. Actions 로그 조회도 그쪽.
