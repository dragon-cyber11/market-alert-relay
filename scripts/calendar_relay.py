# 경제일정 알림 — 파이낸셜주스 GetCalendar 기반
#
# 보내는 시각:
#   한국시간   매일 06:25   -> 앞으로 24시간
#   미국동부   매일 06:25   -> 앞으로 24시간 (서머타임 자동 대응)
#   한국시간   월요일 06:00 -> 앞으로 7일 (주간 미리보기)
#
# [데이터 출처]
#   https://live.financialjuice.com/FJService.asmx/GetCalendar?info="..."&TimeOffset=0
#   -> JSON 배열. 필드: RealDate, Time, Title, CountryCode, ImpID, TypeID,
#      Forecast, Previous, Actual, Speaker, Breaking, Active, Company ...
#
# [검증된 사실 — 저장소의 fj_page.html(사이트 원본 캡처)과 대조해 확인]
#   * RealDate 는 UTC 다.
#       일본 서비스PPI 23:50 = JST 08:50 / 중국 공업이익 01:30 = CST 09:30
#       EIA 원유재고 14:30 = ET 10:30 / FOMC 18:00 = ET 14:00
#   * ImpID 는 1=High, 2=Medium, 3=Low (GetCalendarFilters 의 Imp 목록과 일치).
#       1 = FOMC, BoE/BoJ 금리결정, 미국 PCE, 유로존 GDP, 독일 CPI, 애플 실적 ...
#       3 = 모기지신청건수, 시추기 수, 국채 응찰배율, 휴장일 ...
#     "인베스팅 별 3개"에 해당하는 건 ImpID == 1 이다. 숫자가 거꾸로니 주의.
#
# [중복 발송 방지]
#   깃허브 예약(cron)은 수십 분씩 밀리는 일이 흔해서 슬롯마다 예비 시각을 여러 개 걸어둔다.
#   그래서 "그 날 그 슬롯을 이미 보냈는지"를 .state/cal_sent.json 에 기록하고,
#   현지시각이 발송시각~11시 사이일 때만 보낸다.
#   밀려도 늦게나마 한 번은 나가고, 여러 번 깨어나도 두 번 나가지 않는다.
#
# 사용법:
#   python3 scripts/calendar_relay.py               # 정상 (슬롯 판단)
#   python3 scripts/calendar_relay.py dry           # 하루치를 화면에만 출력
#   python3 scripts/calendar_relay.py test          # 하루치를 지금 즉시 전송
#   python3 scripts/calendar_relay.py weekly-dry    # 주간치를 화면에만 출력
#   python3 scripts/calendar_relay.py weekly-test   # 주간치를 지금 즉시 전송
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

HEADER_DAY = "📈 주요 경제일정"
HEADER_WEEK = "📈 이번주 주요 경제일정"

WANT_IMP = {"1"}          # 최고 중요도만. 2까지 넣으려면 {"1", "2"}
LATE_LIMIT_HOUR = 11      # 예약이 이 시각을 넘게 밀리면 그 날은 포기
MAX_EVENTS_DAY = 40
MAX_EVENTS_WEEK = 120
TG_LIMIT = 3800           # 텔레그램 한 통 한계(4096)보다 여유 있게
SEND_GAP_SECONDS = 3.5    # 여러 통을 이어 보낼 때 간격. 채널 분당 20건 제한 회피

# (키, 시간대, (시,분), 요일제한(0=월, None=매일), 커버시간, 라벨)
SLOTS = [
    ("weekly", "Asia/Seoul", (6, 0), 0, 24 * 7, "주간 미리보기"),
    ("kst", "Asia/Seoul", (6, 25), None, 24, "한국 아침"),
    ("us", "America/New_York", (6, 25), None, 24, "미국 아침"),
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

# 구글 번역이 어색하게 뱉는 것들만 직접 지정 (제목 전체가 일치할 때)
TITLE_OVERRIDE = {
    "us interest rate decision": "미국 기준금리 결정",
    "fomc rate statement": "FOMC 성명",
    "fomc rate statement & sep": "FOMC 성명 및 점도표(SEP)",
    "boe bank rate": "영란은행 기준금리",
    "boe rate statement": "영란은행 성명",
    "boc rate statement": "캐나다중앙은행 성명",
    "boj rate decision": "일본은행 금리 결정",
    "boj rate statement": "일본은행 성명",
    "ecb rate decision": "ECB 금리 결정",
    "ecb rate statement": "ECB 성명",
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
    (r"\bNSA\b", "비조정"), (r"\bSA\b", "계절조정"),
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
    """ASMX 가 JSON 을 <string> 으로 감싸서 주기 때문에 벗겨냄"""
    m = re.search(r"<string[^>]*>(.*)</string>", body, re.DOTALL)
    inner = m.group(1) if m else body
    return (inner.replace("&lt;", "<").replace("&gt;", ">")
                 .replace("&quot;", '"').replace("&amp;", "&"))


def fetch_calendar():
    url = "%s?info=%s&TimeOffset=0" % (
        CAL_URL, urllib.parse.quote('"%s"' % fetch_info(), safe=""))
    data = json.loads(unwrap(http_get(url, timeout=45)))
    if not isinstance(data, list):
        raise RuntimeError("일정 응답이 배열이 아님: %s" % type(data))
    return data


# ---------------------------------------------------------------- 파싱

_DT_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})")


def parse_utc(s):
    """RealDate -> UTC datetime. 파이썬 기본 파서는 소수점 자릿수에 까다로워서
    예전에 조용히 틀린 시각을 쓴 적이 있음. 정규식으로 직접 자름."""
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

# 번역에 쓸 수 있는 총 시간. 이 시간을 넘기면 남은 제목은 원문 그대로 내보낸다.
# 주간 미리보기는 제목이 120개까지 나오는데, 묶음 번역이 통째로 실패해서 전부
# 개별 재시도로 넘어가면 잡의 10분 제한을 넘겨 다이제스트가 통째로 유실됨.
TRANSLATE_BUDGET_SECONDS = 240

# 캐시 값 규약: 성공하면 번역문, 실패하면 원문. None(=키 없음)은 '아직 시도 안 함'만 뜻한다.
# 예전에는 실패도 None 으로 넣어서 to_korean 이 매번 같은 제목을 다시 번역했다.
_tr_cache = {}
_tr_deadline = None


def _budget_left():
    global _tr_deadline
    if _tr_deadline is None:
        _tr_deadline = time.time() + TRANSLATE_BUDGET_SECONDS
    return _tr_deadline - time.time()


def _google(text, timeout=20):
    params = urllib.parse.urlencode(
        {"client": "gtx", "sl": "en", "tl": "ko", "dt": "t", "q": text})
    req = urllib.request.Request(
        "%s?%s" % (GOOGLE_UNOFFICIAL_URL, params),
        headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    return "".join(seg[0] for seg in data[0] if seg and seg[0])


def warm_translations(titles, chunk=20):
    """제목들을 한 번에 묶어서 번역해 캐시에 채운다.
    주간 미리보기는 제목이 60개 넘게 나오는데 하나씩 부르면 요청이 너무 많아짐.
    줄 수가 안 맞으면 그 묶음만 하나씩 다시 부른다."""
    todo = [t for t in dict.fromkeys(titles)
            if t and t not in _tr_cache and t.lower() not in TITLE_OVERRIDE]
    for i in range(0, len(todo), chunk):
        part = todo[i:i + chunk]
        if _budget_left() <= 0:
            print("번역 시간 예산 소진, 남은 %d건은 원문으로 보냄" % len(todo[i:]))
            for src in todo[i:]:
                _tr_cache[src] = src
            return
        try:
            got = _google("\n".join(part)).split("\n")
            if len(got) == len(part):
                for src, ko in zip(part, got):
                    _tr_cache[src] = ko.strip() or src
                time.sleep(0.3)
                continue
            print("묶음 번역 줄 수 불일치(%d vs %d), 개별 재시도" % (len(got), len(part)))
        except Exception as e:
            print("묶음 번역 실패, 개별 재시도:", e)
        # 개별 재시도는 남은 예산 안에서만. 타임아웃도 짧게 잡아 한 건이 오래 붙들지 않게 함
        for j, src in enumerate(part):
            if _budget_left() <= 0:
                print("번역 시간 예산 소진, 남은 %d건은 원문으로 보냄" % len(part[j:]))
                for rest in part[j:]:
                    _tr_cache[rest] = rest
                break
            try:
                _tr_cache[src] = (_google(src, timeout=8) or "").strip() or src
            except Exception as e:
                print("번역 실패(%s): %s" % (src[:40], e))
                _tr_cache[src] = src      # 실패는 원문으로 확정. 다시 시도하지 않음
            time.sleep(0.2)


def to_korean(title):
    """번역이 실패해도 원문으로 계속 진행한다. 봇이 멈추면 안 됨."""
    raw = (title or "").strip()
    if not raw:
        return ""
    hit = TITLE_OVERRIDE.get(raw.lower())
    if hit:
        return hit
    ko = _tr_cache.get(raw)
    if ko is None:                         # 캐시에 아예 없을 때만 (실패는 원문으로 캐시됨)
        if _budget_left() <= 0:
            ko = raw
        else:
            try:
                ko = (_google(raw, timeout=8) or "").strip() or raw
            except Exception:
                ko = raw
        _tr_cache[raw] = ko
    for pat, rep in POST_FIX:
        ko = re.sub(pat, rep, ko)
    return re.sub(r"\s+", " ", ko).strip()


# ---------------------------------------------------------------- 메시지

def val(x):
    s = str(x or "").strip()
    return "" if s in ("", "-", "N/A") else s


def flag_of(e):
    f = FLAG.get((e.get("CountryCode") or "").upper(), "")
    if not f and (e.get("Company") or {}).get("Name"):
        f = "💼"
    return f


def day_line(day):
    return "\n── %d월 %d일 (%s)" % (day.month, day.day, WEEKDAY_KO[day.weekday()])


def pack(header, blocks):
    """텔레그램 길이 제한에 맞춰 필요하면 여러 통으로 나눔"""
    msgs, cur = [], header
    for b in blocks:
        if len(cur) + len(b) + 1 > TG_LIMIT:
            msgs.append(cur)
            cur = header + " (이어서)"
        cur += "\n" + b
    msgs.append(cur)
    return msgs


def build_daily(events):
    """하루치. 예상치·이전치까지 붙인다."""
    if not events:
        return ["%s\n\n앞으로 24시간 안에 예정된 일정이 없습니다." % HEADER_DAY]

    warm_translations([e.get("Title") for _, e in events[:MAX_EVENTS_DAY]])

    blocks, cur_day = [], None
    for t, e in events[:MAX_EVENTS_DAY]:
        k = t.astimezone(KST)
        if k.date() != cur_day:
            cur_day = k.date()
            blocks.append(day_line(cur_day))

        line = "%s %s %s" % (k.strftime("%H:%M"), flag_of(e), to_korean(e.get("Title")))
        sp = val(e.get("Speaker"))
        if sp:
            line += " — %s" % sp
        blocks.append(re.sub(r"\s+", " ", line).strip())

        f, p = val(e.get("Forecast")), val(e.get("Previous"))
        if f or p:
            bits = []
            if f:
                bits.append("예상 %s" % f)
            if p:
                bits.append("이전 %s" % p)
            blocks.append("      " + " · ".join(bits))

    if len(events) > MAX_EVENTS_DAY:
        blocks.append("\n(그 외 %d건 생략)" % (len(events) - MAX_EVENTS_DAY))
    return pack(HEADER_DAY, blocks)


def build_weekly(events, start_utc):
    """7일치. 한 주에 46건쯤 나오므로 예상치는 빼고,
    같은 시각·같은 나라에서 동시에 나오는 지표는 한 줄로 묶는다.
    (예: 호주 CPI 계열 5개가 전부 같은 시각에 나옴)"""
    end = start_utc + timedelta(days=7)
    s, e_ = start_utc.astimezone(KST), end.astimezone(KST)
    head = "%s\n%d월 %d일(%s) ~ %d월 %d일(%s)" % (
        HEADER_WEEK, s.month, s.day, WEEKDAY_KO[s.weekday()],
        e_.month, e_.day, WEEKDAY_KO[e_.weekday()])

    if not events:
        return ["%s\n\n이번주 예정된 일정이 없습니다." % head]

    use = events[:MAX_EVENTS_WEEK]
    warm_translations([e.get("Title") for _, e in use])

    groups, order = {}, []
    for t, e in use:
        k = t.astimezone(KST)
        key = (k.date(), k.strftime("%H:%M"),
               (e.get("CountryCode") or "").upper(), flag_of(e))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(to_korean(e.get("Title")))

    blocks, cur_day = [], None
    for key in order:
        day, hhmm, _, flag = key
        if day != cur_day:
            cur_day = day
            blocks.append(day_line(day))
        titles = groups[key]
        text = " / ".join(titles[:3])
        if len(titles) > 3:
            text += " 외 %d건" % (len(titles) - 3)
        blocks.append(re.sub(r"\s+", " ", "%s %s %s" % (hhmm, flag, text)).strip())

    if len(events) > MAX_EVENTS_WEEK:
        blocks.append("\n(그 외 %d건 생략)" % (len(events) - MAX_EVENTS_WEEK))
    return pack(head, blocks)


# ---------------------------------------------------------------- 전송

def send_telegram(bot_token, chat_id, msg, max_retry=2):
    """[원칙] 응답을 받았을 때만 전송 여부를 판단한다.
    타임아웃은 이미 전달됐을 수 있으므로 재시도하지 않는다(중복 폭탄 방지)."""
    data = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": msg,
         "disable_web_page_preview": "true"}).encode()
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


def find_slot(key):
    for s in SLOTS:
        if s[0] == key:
            return s
    raise KeyError(key)


def due_slot(now_utc, state):
    """지금 보내야 할 슬롯을 돌려줌. 없으면 None."""
    for slot in SLOTS:
        key, tzname, (hh, mm), dow, _hours, label = slot
        local = now_utc.astimezone(ZoneInfo(tzname))
        day_ok = dow is None or local.weekday() == dow
        after = (local.hour, local.minute) >= (hh, mm)
        in_time = local.hour < LATE_LIMIT_HOUR
        today = local.strftime("%Y-%m-%d")
        already = state.get(key) == today
        print("  %-6s %-12s 현지 %s | 요일=%s 시각도달=%s 유효=%s 발송함=%s"
              % (key, label, local.strftime("%m-%d %H:%M %a"),
                 day_ok, after, in_time, already))
        if day_ok and after and in_time and not already:
            return slot, today
    return None, None


# ---------------------------------------------------------------- 본체

def main():
    mode = (sys.argv[1] if len(sys.argv) > 1 else "normal").lower()
    dry = mode.endswith("dry")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token and not dry:
        raise SystemExit("TELEGRAM_BOT_TOKEN 이 없음")

    now_utc = datetime.now(timezone.utc)
    print("지금 UTC %s / KST %s" % (
        now_utc.strftime("%m-%d %H:%M"),
        now_utc.astimezone(KST).strftime("%m-%d %H:%M")))

    state = load_state()
    if mode == "normal":
        slot, today = due_slot(now_utc, state)
        if slot is None:
            print("지금은 보낼 슬롯이 아님. 종료")
            return
    else:
        slot = find_slot("weekly" if mode.startswith("weekly") else "kst")
        today = None
        print("수동 실행(%s) — 슬롯 판단 건너뜀" % mode)

    key, _tz, _at, _dow, hours, label = slot
    print("발송 슬롯: %s (%s), 앞으로 %d시간" % (key, label, hours))

    cal = fetch_calendar()
    print("일정 %d건 수신" % len(cal))
    events = pick_events(cal, now_utc, now_utc + timedelta(hours=hours))
    print("대상 %d건" % len(events))

    msgs = (build_weekly(events, now_utc) if key == "weekly"
            else build_daily(events))

    if dry:
        for m in msgs:
            print("-" * 60)
            print(m)
        return

    ok = True
    for i, m in enumerate(msgs):
        if i:
            time.sleep(SEND_GAP_SECONDS)
        if not send_telegram(token, CHAT_ID, m):
            ok = False

    # 전송을 시도했으면 실패했더라도 오늘치는 끝난 것으로 기록한다.
    # 실패했다고 깨어날 때마다 재시도하면 중복이 나갈 위험이 더 크다.
    if today:
        state[key] = today
        save_state(state)
        print("상태 기록: %s = %s" % (key, today))

    print("전송 완료" if ok else "일부 전송 실패")


if __name__ == "__main__":
    main()
