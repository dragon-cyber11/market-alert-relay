# 주요 지표 원 출처 직접 감시 (CPI / 고용 / PCE / FOMC)
#
# [왜 만들었나]
#   파이낸셜주스 헤드라인을 거치면 발표부터 텔레그램까지 약 12초가 걸린다.
#   그런데 이 네 지표는 발표 시각이 초 단위로 정해져 있어서, 원 출처를 직접
#   때리면 1초 안에 잡을 수 있다. 남이 가공해 주기를 기다리는 구조가 아니기
#   때문에 헤드라인 지연이 통째로 빠진다.
#
# [출처와 확인된 사실]
#   CPI  : https://www.bls.gov/news.release/cpi.nr0.htm      (08:30 ET)
#   고용 : https://www.bls.gov/news.release/empsit.nr0.htm   (08:30 ET)
#   PCE  : https://apps.bea.gov/rss/rss.xml                  (08:30 ET)
#   FOMC : https://www.federalreserve.gov/feeds/press_monetary.xml (14:00 ET)
#
#   * BLS 는 User-Agent 에 이메일이 있어야 통과한다. 브라우저 UA 도, URL 이
#     들어간 UA 도 403 으로 막힌다(실측). 봇을 식별 가능하게 하라는 정책이라
#     CONTACT_EMAIL 환경변수로 받는다. 없으면 BLS 두 건은 그냥 건너뛴다
#     (저장소에 개인 이메일을 박아두지 않기 위함).
#   * BEA RSS 는 <item name="..."> 형태이고 수치가 구조화돼 있다.
#     <title> 에 대상 월이 들어가서 새 발표 판정에 그대로 쓸 수 있다.
#   * Fed 통화정책 RSS 에서 FOMC 결정문은 제목이 정확히
#     "Federal Reserve issues FOMC statement" 이다. 같은 피드에 의사록·할인율
#     같은 다른 발표도 섞여 있어서 제목으로 걸러야 한다.
#
# [판정 방식]
#   발표 시각 전에 '지금 올라와 있는 내용'의 지문을 떠 두고(baseline),
#   발표 시각 이후 지문이 바뀌면 새 발표로 본다. 지난달 내용을 새 발표로
#   오인하지 않으려면 이 방식이 필요하다.
#
# [속도를 위해 번역하지 않는다]
#   번역 호출이 0.5초 이상 걸린다. 이 모듈의 존재 이유가 그 시간을 줄이는
#   것이므로 원문 숫자를 그대로 즉시 보낸다. 한국어 해설은 뒤이어 오는
#   파이낸셜주스 헤드라인이 채워 준다.
import json
import os
import re
import time
import urllib.request
import gzip
import html as html_mod

STATE_FILE = os.path.join(".state", "release_watch.json")

ARM_BEFORE_SECONDS = 120      # 발표 몇 초 전부터 baseline 을 떠 둘지
ARM_AFTER_SECONDS = 300       # 발표 후 몇 초까지 기다릴지
FAST_POLL_SECONDS = 1         # 발표 직후 이 간격으로 확인
SLOW_POLL_SECONDS = 5         # FAST_WINDOW 이후에는 이 간격으로 늦춤
FAST_WINDOW_SECONDS = 60
HTTP_TIMEOUT = 8              # 느린 응답에 붙들리면 감시 자체가 늦어짐

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


_bls_notice_shown = False


def bls_ua():
    """BLS 는 이메일이 든 UA 만 통과시킨다. 없으면 None -> 해당 감시 비활성.

    설정 여부를 프로세스당 한 번 로그로 남긴다. 조용히 건너뛰면 CONTACT_EMAIL
    시크릿을 넣었는지 아닌지 Actions 로그만 보고는 알 수가 없다."""
    global _bls_notice_shown
    email = (os.environ.get("CONTACT_EMAIL") or "").strip()
    ok = "@" in email
    if not _bls_notice_shown:
        _bls_notice_shown = True
        if ok:
            # 로그에 주소 전체를 남기지 않는다. Actions 로그는 공개 저장소에서 볼 수 있음
            user, _, domain = email.partition("@")
            print("[지표감시] CPI/고용 감시 켜짐 (연락처 %s***@%s)" % (user[:2], domain))
        else:
            print("[지표감시] CONTACT_EMAIL 이 없어 CPI/고용 감시 꺼짐 "
                  "(PCE/FOMC 는 정상 동작). 저장소 Secret 에 CONTACT_EMAIL 을 넣으면 켜짐")
    return "market-alert-relay/1.0 (%s)" % email if ok else None


def http_get(url, ua, timeout=HTTP_TIMEOUT):
    req = urllib.request.Request(url, headers={
        "User-Agent": ua, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
            raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


def to_text(body):
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", body, flags=re.S | re.I)
    return html_mod.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))).strip()


# 소수점에서 끊기지 않는 문장 분리. '0.1 percent' 의 마침표를 문장 끝으로
# 보면 헤드라인이 "increased 0." 에서 잘린다(실제로 겪음).
_SENTENCE_END = r"(?<![0-9])\.(?=\s+[A-Z(]|\s*$)"


def first_sentences(text, start_pattern, count=2):
    m = re.search(start_pattern, text, re.I)
    if not m:
        return None
    seg = text[m.start():m.start() + 1200]
    parts = [p.strip() for p in re.split(_SENTENCE_END, seg) if p.strip()]
    if not parts:
        return None
    return ". ".join(parts[:count]) + "."


def _rss_items(body):
    """<item> 과 <item name="..."> 을 모두 잡는다. BEA 는 속성이 붙어 있어서
    태그를 정확히 매칭하면 한 건도 안 잡힌다(실측)."""
    return re.findall(r"<item\b[^>]*>(.*?)</item>", body, re.S)


def _tag(chunk, name):
    m = re.search(r"<%s\b[^>]*>(.*?)</%s>" % (name, name), chunk, re.S)
    if not m:
        return ""
    v = re.sub(r"<!\[CDATA\[|\]\]>", "", m.group(1))
    return html_mod.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", v))).strip()


# ---------------------------------------------------------------- 숫자 다듬기
#
# 원문 문단을 그대로 던지면 영어라 보기 불편하다. 지표는 4개로 고정이고
# 예상/이전치는 캘린더에 이미 있으므로, 원문에서 '실제 숫자'만 뽑아
# 한글 라벨과 조립한다. 숫자는 언어와 무관하니 번역을 거치지 않는다.

_EN_MONTH = {"january": "1월", "february": "2월", "march": "3월", "april": "4월",
             "may": "5월", "june": "6월", "july": "7월", "august": "8월",
             "september": "9월", "october": "10월", "november": "11월", "december": "12월"}

# 오르면 +, 내리면 -. 문장의 동사로 방향을 판단한다.
_DOWN_RE = re.compile(r"declin|decreas|fell|fall|drop|down|lower", re.I)


def _signed(direction, num):
    sign = "-" if _DOWN_RE.search(direction or "") else "+"
    return "%s%s%%" % (sign, num)


def _month_ko(text, after=""):
    """'in July' 같은 표현에서 월을 뽑아 '7월' 로. after 로 검색 시작 위치를 좁힘."""
    m = re.search(r"\bin\s+([A-Z][a-z]+)", text[text.find(after):] if after else text)
    return _EN_MONTH.get(m.group(1).lower(), "") if m else ""


def _fomc_range(text):
    """'3-1/2 to 3-3/4 percent' -> '3.50~3.75%'. 분수를 소수로 바꾼다."""
    def frac(s):
        m = re.match(r"(\d+)(?:-(\d+)/(\d+))?", s.strip())
        if not m:
            return None
        v = int(m.group(1))
        if m.group(2):
            v += int(m.group(2)) / int(m.group(3))
        return v
    m = re.search(r"federal funds rate at\s+([\d\-/]+)\s+to\s+([\d\-/]+)\s+percent", text, re.I)
    if not m:
        return ""
    lo, hi = frac(m.group(1)), frac(m.group(2))
    if lo is None or hi is None:
        return ""
    return "%.2f~%.2f%%" % (lo, hi)


# ---------------------------------------------------------------- 출처별 수집
#
# 각 수집기는 (지문, detail) 을 돌려준다.
#   지문   : 새 발표 판정용. 발표 전(지난달 값)과 후(이번달 값)가 달라야 함
#   detail : {"month": "7월", "items": [{"kind","label","value"}, ...]}
#     kind 는 캘린더 하위 항목과 짝짓기 위한 종류표.
#       mom  = 헤드라인 전월,  core = 근원 전월,  yoy = 헤드라인 전년
#       nfp  = 비농업 고용,    unemp = 실업률,    plain = 짝지을 것 없음
# 파싱에 실패하면 원문 첫 문장을 plain 항목으로 담아 그대로라도 내보낸다.

def _item(kind, label, value):
    return {"kind": kind, "label": label, "value": value}


def _fp(month, items):
    return "%s|%s" % (month, "".join(i["value"] for i in items))


def fetch_cpi():
    ua = bls_ua()
    if not ua:
        return None
    t = to_text(http_get("https://www.bls.gov/news.release/cpi.nr0.htm", ua))
    items = []
    mom = re.search(r"CPI-U\)?\s+(increased|declined|rose|fell|edged up|edged down|"
                    r"was unchanged|changed little)\s*([\d.]+)?\s*percent", t, re.I)
    month = ""
    if mom:
        month = _month_ko(t, mom.group(0))
        val = "0.0%" if mom.group(2) is None else _signed(mom.group(1), mom.group(2))
        items.append(_item("mom", "전월", val))
    core = re.search(r"less food and energy (rose|increased|declined|fell|was unchanged)\s*"
                     r"([\d.]+)?\s*percent", t, re.I)
    if core:
        items.append(_item("core", "근원", "0.0%" if core.group(2) is None
                           else _signed(core.group(1), core.group(2))))
    yoy = re.search(r"all items index (rose|increased|declined|fell)\s+([\d.]+)\s+percent"
                    r"[^.]*?12 months", t, re.I)
    if yoy:
        items.append(_item("yoy", "전년", _signed(yoy.group(1), yoy.group(2))))
    if not items:
        s = first_sentences(t, r"The Consumer Price Index for All Urban Consumers")
        if not s:
            return None
        return s, {"month": "", "items": [_item("plain", "", s)]}
    return _fp(month, items), {"month": month, "items": items}


def fetch_empsit():
    ua = bls_ua()
    if not ua:
        return None
    t = to_text(http_get("https://www.bls.gov/news.release/empsit.nr0.htm", ua))
    items = []
    nfp = re.search(r"nonfarm payroll employment\s+(?:increased|rose|declined|fell|"
                    r"changed little|edged up|edged down)[^.(]*\(([\+\-][\d,]+)\)", t, re.I)
    month = _month_ko(t, "nonfarm payroll") if nfp else ""
    if nfp:
        items.append(_item("nfp", "비농업", nfp.group(1)))
    unemp = re.search(r"unemployment rate\s*\(?([\d.]+)\s+percent", t, re.I)
    if unemp:
        items.append(_item("unemp", "실업률", "%s%%" % unemp.group(1)))
    if not items:
        p = first_sentences(t, r"Total nonfarm payroll employment", 1)
        if not p:
            return None
        return p, {"month": "", "items": [_item("plain", "", p)]}
    return _fp(month, items), {"month": month, "items": items}


def fetch_pce():
    # RSS 설명에는 '지출'만 있고 시장이 보는 '물가지수'가 없다. 물가지수는
    # 링크된 본문 페이지에 있어서, 발표가 감지되면 그 페이지를 한 번 더 받는다.
    body = http_get("https://apps.bea.gov/rss/rss.xml", BROWSER_UA)
    for it in _rss_items(body):
        title = _tag(it, "title")
        if "Personal Income and Outlays" not in title:
            continue
        link = _tag(it, "link")
        mm = re.search(r"Personal Income and Outlays,\s*([A-Z][a-z]+)", title)
        month = _EN_MONTH.get(mm.group(1).lower(), "") if mm else ""
        items = []
        try:
            page = to_text(http_get(link, BROWSER_UA))
            # 헤드라인 전월: "From the preceding month, the PCE price index ... X percent"
            head = re.search(r"preceding month, the PCE price index[^.]*?"
                             r"(increased|decreased|rose|declined)\s+([\d.]+)\s+percent", page, re.I)
            if head:
                items.append(_item("mom", "전월", _signed(head.group(1), head.group(2))))
            # 근원(전월): "Excluding food and energy, the PCE price index ... X percent" 의 첫 등장
            core = re.search(r"[Ee]xcluding food and energy,? the PCE price index "
                             r"(increased|decreased|rose|declined)\s+([\d.]+)\s+percent", page, re.I)
            if core:
                items.append(_item("core", "근원", _signed(core.group(1), core.group(2))))
            # 헤드라인 전년: "From the same month one year ago, the PCE price index ... X percent"
            yoy = re.search(r"one year ago, the PCE price index[^.]*?"
                            r"(increased|decreased|rose|declined)\s+([\d.]+)\s+percent", page, re.I)
            if yoy:
                items.append(_item("yoy", "전년", _signed(yoy.group(1), yoy.group(2))))
        except Exception as e:
            print("[지표감시] PCE 본문 실패:", repr(e)[:120])
        if not items:
            # 물가지수를 못 뽑으면 RSS 설명(지출)이라도 정리해 보냄
            desc = _tag(it, "description")
            cut = len(desc)
            for marker in ("<!--", "Full Text"):
                i = desc.find(marker)
                if i != -1:
                    cut = min(cut, i)
            desc = desc[:cut].strip()
            return title, {"month": month,
                           "items": [_item("plain", "", desc[:500] if desc else title)]}
        return _fp(month, items), {"month": month, "items": items}
    return None


_FOMC_ACTION = [(r"maintain", "동결"), (r"lower", "인하"), (r"raise", "인상")]


def fetch_fomc():
    body = http_get("https://www.federalreserve.gov/feeds/press_monetary.xml", BROWSER_UA)
    for it in _rss_items(body):
        if _tag(it, "title") != "Federal Reserve issues FOMC statement":
            continue
        link = _tag(it, "link")
        value = link            # 파싱 실패해도 링크는 준다
        fingerprint = link      # 성명 URL 은 회의마다 달라 지문으로 안전
        try:
            t = to_text(http_get(link, BROWSER_UA))
            rng = _fomc_range(t)
            action = ""
            m = re.search(r"decided to (maintain|lower|raise)", t, re.I)
            if m:
                for pat, ko in _FOMC_ACTION:
                    if re.match(pat, m.group(1), re.I):
                        action = ko
                        break
            if action and rng:
                value = "%s · 목표범위 %s" % (action, rng)
            elif rng:
                value = "목표범위 %s" % rng
            elif action:
                value = action
        except Exception as e:
            print("[지표감시] FOMC 본문 실패:", repr(e)[:120])
        return fingerprint, {"month": "", "items": [_item("plain", "", value)]}
    return None


# 캘린더 하위 항목 제목 -> 종류표. 추출 항목의 kind 와 짝지어 예상/이전치를 붙인다.
def calendar_kind(title):
    t = (title or "").lower()
    if "unemployment" in t:
        return "unemp"
    if "payroll" in t or "nonfarm" in t or "non-farm" in t:
        return "nfp"
    core = ("core" in t or "ex food" in t or "excluding" in t or "less food" in t)
    yoy = any(k in t for k in ("yoy", "y/y", "year over year", "year-over-year",
                               "annual", "12-month", "12 month"))
    if core:
        return "core_yoy" if yoy else "core"
    return "yoy" if yoy else "mom"


WATCHERS = [
    {"key": "cpi",    "label": "🇺🇸 미국 CPI",
     "cc": "US", "pattern": r"\bCPI\b|consumer price", "fetch": fetch_cpi},
    {"key": "empsit", "label": "🇺🇸 미국 고용",
     "cc": "US", "pattern": r"non.?farm|payroll|employment situation", "fetch": fetch_empsit},
    {"key": "pce",    "label": "🇺🇸 미국 PCE 물가",
     "cc": "US", "pattern": r"\bPCE\b|personal (income|consumption|spending)", "fetch": fetch_pce},
    {"key": "fomc",   "label": "🇺🇸 FOMC 금리결정",
     "cc": "US", "pattern": r"FOMC (rate|statement|interest)|fed(eral)? funds rate decision",
     "fetch": fetch_fomc},
]

_last_poll = {}     # key -> 마지막 확인 시각


def load_state():
    try:
        with open(STATE_FILE) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_state(state):
    os.makedirs(".state", exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATE_FILE)


def armed_events(watcher, cal_events, now_epoch):
    """지금 노려야 할 지표 일정을 찾는다. 같은 지표의 하위 항목(CPI 전월/근원/
    전년 등)이 같은 시각에 여러 건 있으므로 그 시각의 것을 모두 모아 돌려준다.
    없으면 None. 반환: (발표시각, [이벤트...])"""
    best_t = None
    group = []
    for t_utc, e in cal_events:
        if (e.get("CountryCode") or "") != watcher["cc"]:
            continue
        if not re.search(watcher["pattern"], e.get("Title") or "", re.I):
            continue
        t = t_utc.timestamp()
        if not (t - ARM_BEFORE_SECONDS <= now_epoch <= t + ARM_AFTER_SECONDS):
            continue
        # 가장 이른 발표 시각의 묶음만. 같은 시각의 하위 항목들을 함께 모은다
        if best_t is None or t < best_t:
            best_t, group = t, [e]
        elif abs(t - best_t) < 1:
            group.append(e)
    return (best_t, group) if group else None


def due_to_poll(key, now_epoch, seconds_since_release):
    gap = FAST_POLL_SECONDS if seconds_since_release <= FAST_WINDOW_SECONDS else SLOW_POLL_SECONDS
    return now_epoch - _last_poll.get(key, 0) >= gap


def build_message(label, detail, events):
    """한글 라벨 + 실제 숫자 + 항목별 예상/이전(캘린더) 조립. 번역 없음.

    events 는 같은 시각의 캘린더 하위 항목들. 추출한 각 숫자(kind)를 제목으로
    분류한 캘린더 항목과 짝지어, 그 항목의 예상/이전치를 옆에 붙인다.
    짝이 없으면 숫자만 보여준다."""
    import calendar_relay as cal
    by_kind = {}
    for e in events or []:
        by_kind.setdefault(calendar_kind(e.get("Title")), e)

    month = detail.get("month") or ""
    out = ["🚨 %s%s" % (label, " (%s)" % month if month else "")]
    for it in detail.get("items", []):
        line = ("%s %s" % (it["label"], it["value"])).strip()
        e = by_kind.get(it["kind"])
        # core 하위에 전월/전년 구분이 없을 때 core_yoy 로도 시도
        if e is None and it["kind"] == "core":
            e = by_kind.get("core_yoy")
        if e is not None:
            fc = cal.val(e.get("Forecast"))
            pv = cal.val(e.get("Previous"))
            if fc or pv:
                line += " (예상 %s·이전 %s)" % (fc or "-", pv or "-")
        out.append(line)
    return "\n".join(out)


def tick(bot_token, cal_events, now_epoch=None, send=None):
    """릴레이 루프가 매 주기 부른다. 보낸 건수를 돌려준다.

    cal_events 는 calendar_prealert 가 이미 30분마다 갱신해 두는
    [(발표시각 UTC, 이벤트), ...]. 여기서 또 받아오면 중복 호출이 된다.
    호출하는 쪽에서 예외를 삼켜야 한다."""
    now_epoch = time.time() if now_epoch is None else now_epoch
    if not cal_events:
        return 0
    state = load_state()
    sent = 0

    for w in WATCHERS:
        hit = armed_events(w, cal_events, now_epoch)
        if not hit:
            continue
        t_release, events = hit
        slot = state.get(w["key"]) or {}
        # 하위 항목이 여럿이라 개별 ID 대신 발표 시각을 묶음 키로 쓴다
        event_id = "%.0f" % t_release

        # 이번 발표는 이미 처리했음
        if slot.get("done_for") == event_id:
            continue

        since = now_epoch - t_release
        if not due_to_poll(w["key"], now_epoch, since):
            continue
        _last_poll[w["key"]] = now_epoch

        try:
            got = w["fetch"]()
        except Exception as e:
            print("[지표감시] %s 수집 실패: %s" % (w["key"], repr(e)[:120]))
            continue
        if not got:
            continue
        fingerprint, detail = got

        # 발표 전이면 기준만 잡아 둔다. 지난달 내용을 새 발표로 오인하지 않기 위함
        if now_epoch < t_release:
            if slot.get("baseline_for") != event_id:
                state[w["key"]] = {"baseline": fingerprint, "baseline_for": event_id}
                save_state(state)
                print("[지표감시] %s 기준 확보 (발표 %.0f초 전)" % (w["key"], t_release - now_epoch))
            continue

        # 기준을 못 잡은 채로 발표 시각을 넘겼으면(릴레이가 그 사이 죽어 있었음)
        # 지금 내용이 새 것인지 판단할 수 없다. 오발송보다 침묵이 낫다.
        if slot.get("baseline_for") != event_id:
            continue
        if fingerprint == slot.get("baseline"):
            continue

        msg = build_message(w["label"], detail, events)
        ok, permanent = (send or _send)(bot_token, msg)
        if ok or permanent:
            slot["done_for"] = event_id
            state[w["key"]] = slot
            save_state(state)
            sent += int(ok)
            print("[지표감시] %s 발송 (발표 %.1f초 후)" % (w["key"], since))

    return sent


def _send(bot_token, msg):
    import calendar_relay as cal
    import financialjuice_relay as fj
    return fj.send_telegram(bot_token, cal.CHAT_ID, msg)
