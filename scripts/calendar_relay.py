# 경제일정 알림 — 파이낸셜주스 GetCalendar 기반
#
# 하루 두 번, 보내는 시각으로부터 24시간 안의 "최고 중요도(별3)" 일정만 정리해서 전송.
#   - 한국시간 06:25
#   - 미국동부시간 06:25 (서머타임 자동 대응)
#
# [데이터 출처]
#   https://live.financialjuice.com/FJService.asmx/GetCalendar?info="..."&TimeOffset=0
#   -> JSON 배열. 항목 필드: RealDate, Time, Title, CountryCode, ImpID, TypeID,
#      Forecast, Previous, Actual, Speaker, Breaking, Active ...
#
# [검증된 사실 — 저장소의 fj_page.html(사이트 원본 캡처)과 대조해서 확인]
#   * RealDate 는 UTC 다.
#       일본 서비스PPI 23:50 = JST 08:50 / 중국 공업이익 01:30 = CST 09:30
#       EIA 원유재고 14:30 = ET 10:30 / FOMC 18:00 = ET 14:00
#   * ImpID 는 1=High, 2=Medium, 3=Low. (GetCalendarFilters 의 Imp 목록과 일치)
#       1 = FOMC, BoE/BoJ 금리결정, 미국 PCE, 유로존 GDP, 독일 CPI, 애플 실적 ...
#       3 = 모기지신청건수, 시추기 수, 국채 응찰배율, 휴장일 ...
#     따라서 "인베스팅 별 3개"에 해당하는 건 ImpID == 1 이다. 숫자가 거꾸로니 주의.
#   * Breaking=true 는 사이트가 즉시 밀어주는 항목(FOMC 등). ⚡ 로 표시함.
#
# [중복 발송 방지]
#   예약(cron)은 몇십 분씩 밀리는 일이 흔해서 슬롯마다 예비 시각을 여러 개 걸어둠.
#   그래서 "그 날 그 슬롯을 이미 보냈는지"를 .state/cal_sent.json 에 기록하고,
#   현지시각이 06:25~11:00 사이일 때만 보낸다. 밀려도 늦게나마 한 번은 나가고,
#   여러 번 깨어나도 두 번 나가지는 않는다.
#
# 사용법:
#   python3 scripts/calendar_relay.py          # 정상 동작 (슬롯 판단)
#   python3 scripts/calendar_relay.py test     # 슬롯 무시하고 지금 즉시 전송
#   python3 scripts/calendar_relay.py dry      # 전송 안 하고 화면에만 출력
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

CHAT_ID = "@haesunking"
HOME_URL = "https://www.financialjuice.com/home"
CAL_URL = "https://live.financialjuice.com/FJService.asmx/GetCalendar"
GOOGLE_UNOFFICIAL_URL = "https://translate.googleapis.com/translate_a/single"
STATE_PATH = ".state/cal_sent.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

WANT_IMP = {"1"}          # 최고 중요도만. 2까지 넣고 싶으면 {"1", "2"}
WINDOW_HOURS = 24         # 보내는 시각으로부터 몇 시간치를 볼지
SEND_HOUR, SEND_MIN = 6, 25
LATE_LIMIT_HOUR = 11      # 예약이 이 시각을 넘게 밀리면 그 날은 포기
MAX_EVENTS = 40           # 안전장치. 넘으면 잘라냄
TG_LIMIT = 3800           # 텔레그램 한 통 최대치(4096)보다 여유 있게

SLOTS = [
    ("kst", "Asia/Seoul", "한국시간"),
    ("us", "America/New_York", "미국동부시간"),
]

KST = ZoneInfo("Asia/Seoul")
WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]

FLAG = {
    "US": "🇺🇸", "EU": "🇪🇺", "CN": "🇨🇳", "JP": "🇯🇵", "GB": "🇬🇧",
    "KR": "🇰🇷", "DE": "🇩🇪", "FR": "🇫🇷", "IT": "🇮🇹", "ES": "🇪🇸",
    "CA": "🇨🇦", "AU": "🇦🇺", "NZ": "🇳🇿", "CH": "🇨🇭", "SE": "🇸🇪",
    "NO": "🇳🇴", "NL": "🇳🇱", "SG": "🇸🇬", "HK": "🇭🇰", "IN": "🇮🇳",
    "RU": "🇷🇺", "TR": "🇹🇷",
}

# 구글 번역이 자주 어색하게 뱉는 것들만 직접 지정 (제목 전체가 일치할 때)
TITLE_OVERRIDE = {
    "us interest rate decision": "미국 기준금리 결정",
    "fomc rate statement": "FOMC 성명",
    "fomc rate statement & sep": "FOMC 성명 및 점도표(SEP)",
    "boe bank rate": "영란은행 기준금리",
    "boe rate statement": "영란은행 성명",
    "boj rate decision": "일본은행 금리 결정",
    "boj rate statement": "일본은행 성명",
    "ecb rate decision": "ECB 금리 결정",
    "us treasury qra": "미국 재무부 국채발행계획(QRA)",
    "us treasury qra estimates": "미국 재무부 자금조달 추정",
}

# 번역 후에도 영어로 남는 축약어 정리
POST_FIX = [
    (r"\bMoM\b", "전월대비"), (r"\bMOM\b", "전월대비"),
    (r"\bYoY\b", "전년대비"), (r"\bYOY\b", "전년대비"),
    (r"\bQoQ\b", "전분기대비"), (r"\bQOQ\b", "전분기대비"),
    (r"\bPrelim\.?", "잠정"), (r"\bFlash\b", "속보치"),
    (r"\bAdvance\b", "예비"), (r"\bRev\.?\b", "수정"),
    (r"\bSA\b", "계절조정"), (r"\bNSA\b", "비조정"),
]


# ---------------------------------------------------------------- 통신

def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://www.financialjuice.com/",
        "Accept-Encoding": "gzip",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
            try:
                raw = gzip.decompress(raw)
            except Exception as e:
                print("압축 해제 실패, 원본으로 처리:", e)
    return raw.decode("utf-8", "replace")


def fetch_info():
    """홈페이지 HTML 안의  var info = '...'  (API 인증값) 추출"""
    html = http_get(HOME_URL)
    m = re.search(r"var\s+info\s*=\s*'([^']+)'", html)
    if not m:
        raise RuntimeError("info 값을 찾지 못함 (사이트 구조 변경 의심)")
    return m.group(1)


def unwrap(body):
    """ASMX 가 JSON 을 <string> 으로 한 번 감싸서 주기 때문에 벗겨냄"""
    m = re.search(r"<string[^>]*>(.*)</string>", body, re.DOTALL)
    inner = m.group(1) if m else body
    return (inner.replace("&lt;", "<").replace("&gt;", ">")
                 .replace("&quot;", '"').replace("&amp;", "&"))


def fetch_calendar():
    info = fetch_info()
    url = "%s?info=%s&TimeOffset=0" % (
        CAL_URL, urllib.parse.quote('"%s"' % info, safe=""))
    data = json.loads(unwrap(http_get(url, timeout=45)))
    if not isinstance(data, list):
        raise RuntimeError("일정 응답이 배열이 아님: %s" % type(data))
    return data


# ---------------------------------------------------------------- 파싱

_DT_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})")


def parse_utc(s):
    """RealDate 문자열 -> UTC datetime. 파이썬 기본 파서는 소수점 자릿수에
    까다로워서 예전에 조용히 틀린 시각을 쓴 적이 있음. 정규식으로 직접 자름."""
    m = _DT_RE.match((s or "").strip())
    if not m:
        return None
    try:
        return datetime(*(int(x) for x in m.groups()), tzinfo=timezone.utc)
    except Exception:
        return None


def pick_events(cal, start_utc, end_utc):
    out = []
    for e in cal:
        if not isinstance(e, dict):
            continue
        if str(e.get("ImpID")) not in WANT_IMP:
            continue
        if e.get("Active") is False:
            continue
        t = parse_utc(e.get("RealDate"))
        if t is None or not (start_utc <= t <= end_utc):
            continue
        out.append((t, e))
    out.sort(key=lambda x: (x[0], (x[1].get("Title") or "")))
    return out


# ---------------------------------------------------------------- 번역

_tr_cache = {}


def translate_to_ko(text):
    if text in _tr_cache:
        return _tr_cache[text]
    result = None
    try:
        params = urllib.parse.urlencode(
            {"client": "gtx", "sl": "en", "tl": "ko", "dt": "t", "q": text})
        req = urllib.request.Request(
            "%s?%s" % (GOOGLE_UNOFFICIAL_URL, params),
            headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        joined = "".join(seg[0] for seg in data[0] if seg and seg[0]).strip()
        result = joined or None
    except Exception as e:
        print("번역 실패(%s): %s" % (text[:40], e))
    _tr_cache[text] = result
    return result


def to_korean(title):
    """번역 실패해도 원문으로 계속 진행. 봇이 멈추면 안 됨."""
    raw = (title or "").strip()
    if not raw:
        return ""
    hit = TITLE_OVERRIDE.get(raw.lower())
    if hit:
        return hit
    ko = translate_to_ko(raw) or raw
    for pat, rep in POST_FIX:
        ko = re.sub(pat, rep, ko)
    return re.sub(r"\s+", " ", ko).strip()


# ---------------------------------------------------------------- 메시지

def val(x):
    s = str(x or "").strip()
    return "" if s in ("", "-", "N/A") else s


def build_message(events, now_utc, end_utc, tz_label):
    head = "📅 주요 경제일정 (최고중요도)\n%s ~ %s · 한국시간" % (
        now_utc.astimezone(KST).strftime("%m/%d %H:%M"),
        end_utc.astimezone(KST).strftime("%m/%d %H:%M"))

    if not events:
        return ["%s\n\n앞으로 24시간 안에 예정된 최고중요도 일정이 없습니다." % head]

    blocks, cur_day = [], None
    for t, e in events[:MAX_EVENTS]:
        k = t.astimezone(KST)
        day = k.date()
        if day != cur_day:
            cur_day = day
            blocks.append("\n── %d월 %d일 (%s)" % (
                day.month, day.day, WEEKDAY_KO[day.weekday()]))

        flag = FLAG.get((e.get("CountryCode") or "").upper(), "")
        if not flag and (e.get("Company") or {}).get("Name"):
            flag = "💼"
        bolt = "⚡" if e.get("Breaking") is True else ""

        line = "%s %s%s %s" % (k.strftime("%H:%M"), bolt, flag, to_korean(e.get("Title")))
        sp = val(e.get("Speaker"))
        if sp:
            line += " — %s" % sp
        blocks.append(line.replace("  ", " ").strip())

        f, p = val(e.get("Forecast")), val(e.get("Previous"))
        if f or p:
            bits = []
            if f:
                bits.append("예상 %s" % f)
            if p:
                bits.append("이전 %s" % p)
            blocks.append("      " + " · ".join(bits))

    if len(events) > MAX_EVENTS:
        blocks.append("\n(그 외 %d건 생략)" % (len(events) - MAX_EVENTS))

    # 텔레그램 길이 제한에 맞춰 필요하면 나눠 보냄
    msgs, cur = [], head
    for b in blocks:
        if len(cur) + len(b) + 1 > TG_LIMIT:
            msgs.append(cur)
            cur = "📅 (이어서)"
        cur += "\n" + b
    msgs.append(cur)
    return msgs


# ---------------------------------------------------------------- 전송

def send_telegram(bot_token, chat_id, msg, max_retry=2):
    """[원칙] 응답을 받았을 때만 전송 여부를 판단한다.
    타임아웃은 이미 전달됐을 수 있으므로 재시도하지 않는다(중복 폭탄 방지)."""
    data = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": msg, "disable_web_page_preview": "true"}).encode()
    url = "https://api.telegram.org/bot%s/sendMessage" % bot_token

    for attempt in range(max_retry + 1):
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(url, data=data), timeout=20) as r:
                r.read()
            return True
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            if e.code == 429:
                wait = 5
                try:
                    wait = int(json.loads(body)["parameters"]["retry_after"])
                except Exception:
                    pass
                print("속도 제한, %d초 대기" % wait)
                time.sleep(min(wait + 1, 60))
                continue
            print("텔레그램 거부(%s): %s" % (e.code, body))
            if 400 <= e.code < 500:
                return False
            if attempt < max_retry:
                time.sleep(2)
                continue
            return False
        except Exception as e:
            print("텔레그램 응답 없음(전달 여부 불명, 재시도 안 함):", e)
            return False
    return False


# ---------------------------------------------------------------- 슬롯 판단

def load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_state(state):
    os.makedirs(".state", exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)


def due_slot(now_utc, state):
    """지금 보내야 할 슬롯이 있으면 (키, 지역명, 현지날짜) 를 돌려줌"""
    for key, tzname, label in SLOTS:
        local = now_utc.astimezone(ZoneInfo(tzname))
        after = (local.hour, local.minute) >= (SEND_HOUR, SEND_MIN)
        not_too_late = local.hour < LATE_LIMIT_HOUR
        today = local.strftime("%Y-%m-%d")
        already = state.get(key) == today
        print("  %-3s %s 현지 %s | 시각도달=%s 유효시간=%s 오늘발송=%s"
              % (key, label, local.strftime("%m-%d %H:%M"),
                 after, not_too_late, already))
        if after and not_too_late and not already:
            return key, label, today
    return None, None, None


# ---------------------------------------------------------------- 본체

def main():
    mode = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token and mode != "dry":
        raise SystemExit("TELEGRAM_BOT_TOKEN 이 없음")

    now_utc = datetime.now(timezone.utc)
    print("지금 UTC %s / KST %s" % (
        now_utc.strftime("%m-%d %H:%M"),
        now_utc.astimezone(KST).strftime("%m-%d %H:%M")))

    state = load_state()
    if mode in ("test", "dry"):
        slot_key, label, today = "manual", "수동", None
        print("수동 실행 — 슬롯 판단 건너뜀")
    else:
        slot_key, label, today = due_slot(now_utc, state)
        if not slot_key:
            print("지금은 보낼 슬롯이 아님. 종료")
            return

    print("발송 슬롯: %s (%s)" % (slot_key, label))

    end_utc = now_utc + timedelta(hours=WINDOW_HOURS)
    cal = fetch_calendar()
    print("일정 %d건 수신" % len(cal))
    events = pick_events(cal, now_utc, end_utc)
    print("24시간 내 최고중요도 %d건" % len(events))

    msgs = build_message(events, now_utc, end_utc, label)

    if mode == "dry":
        for m in msgs:
            print("-" * 60)
            print(m)
        return

    sent_all = True
    for i, m in enumerate(msgs):
        if i:
            time.sleep(3.5)          # 채널 분당 20건 제한 회피
        if not send_telegram(token, CHAT_ID, m):
            sent_all = False

    # 전송을 시도했으면(설령 실패했더라도) 오늘치는 끝난 것으로 기록한다.
    # 실패했다고 다시 깨어날 때마다 재시도하면 중복이 나갈 위험이 더 크다.
    if today:
        state[slot_key] = today
        save_state(state)
        print("상태 기록: %s = %s" % (slot_key, today))

    print("전송 완료" if sent_all else "일부 전송 실패")


if __name__ == "__main__":
    main()
