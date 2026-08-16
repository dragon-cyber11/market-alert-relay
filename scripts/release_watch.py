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


# ---------------------------------------------------------------- 출처별 수집

def fetch_cpi():
    ua = bls_ua()
    if not ua:
        return None
    t = to_text(http_get("https://www.bls.gov/news.release/cpi.nr0.htm", ua))
    s = first_sentences(t, r"The Consumer Price Index for All Urban Consumers")
    return (s, s) if s else None


def fetch_empsit():
    ua = bls_ua()
    if not ua:
        return None
    t = to_text(http_get("https://www.bls.gov/news.release/empsit.nr0.htm", ua))
    payroll = first_sentences(t, r"Total nonfarm payroll employment", 1)
    unemp = first_sentences(t, r"The unemployment rate", 1)
    body = "\n".join(x for x in (payroll, unemp) if x)
    return (payroll or body, body) if body else None


def fetch_pce():
    body = http_get("https://apps.bea.gov/rss/rss.xml", BROWSER_UA)
    for it in _rss_items(body):
        title = _tag(it, "title")
        if "Personal Income and Outlays" not in title:
            continue
        desc = _tag(it, "description")
        desc = desc.split("Full Text")[0].strip()
        # 대상 월이 제목에 있어서 그것만으로 새 발표 판정이 된다
        return title, "%s\n\n%s" % (title, desc[:700])
    return None


def fetch_fomc():
    body = http_get("https://www.federalreserve.gov/feeds/press_monetary.xml", BROWSER_UA)
    for it in _rss_items(body):
        if _tag(it, "title") != "Federal Reserve issues FOMC statement":
            continue
        link = _tag(it, "link")
        text = ""
        try:
            t = to_text(http_get(link, BROWSER_UA))
            text = first_sentences(
                t, r"(?:decided to (?:maintain|lower|raise)|target range for the federal funds rate)") or ""
        except Exception as e:
            print("[지표감시] FOMC 본문 실패, 링크만 보냄:", repr(e)[:120])
        return link, (text + ("\n" + link if text else link))
    return None


WATCHERS = [
    {"key": "cpi",    "label": "🇺🇸 미국 CPI",
     "cc": "US", "pattern": r"\bCPI\b|consumer price", "fetch": fetch_cpi},
    {"key": "empsit", "label": "🇺🇸 미국 고용지표",
     "cc": "US", "pattern": r"non.?farm|payroll|employment situation", "fetch": fetch_empsit},
    {"key": "pce",    "label": "🇺🇸 미국 PCE",
     "cc": "US", "pattern": r"\bPCE\b|personal (income|consumption|spending)", "fetch": fetch_pce},
    {"key": "fomc",   "label": "🇺🇸 FOMC",
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


def armed_event(watcher, cal_events, now_epoch):
    """이 감시기가 지금 노려야 할 지표 일정을 찾는다. 없으면 None."""
    for t_utc, e in cal_events:
        if (e.get("CountryCode") or "") != watcher["cc"]:
            continue
        if not re.search(watcher["pattern"], e.get("Title") or "", re.I):
            continue
        t = t_utc.timestamp()
        if t - ARM_BEFORE_SECONDS <= now_epoch <= t + ARM_AFTER_SECONDS:
            return t, e
    return None


def due_to_poll(key, now_epoch, seconds_since_release):
    gap = FAST_POLL_SECONDS if seconds_since_release <= FAST_WINDOW_SECONDS else SLOW_POLL_SECONDS
    return now_epoch - _last_poll.get(key, 0) >= gap


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
        hit = armed_event(w, cal_events, now_epoch)
        if not hit:
            continue
        t_release, event = hit
        slot = state.get(w["key"]) or {}
        event_id = (event.get("ID") or "").strip() or event.get("RealDate") or ""

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
        fingerprint, body = got

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

        msg = "🚨 %s 발표\n\n%s" % (w["label"], body)
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
