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
#   실업수당 : https://www.dol.gov/ui/data.pdf                  (목 08:30 ET, PDF)
#   ECB  : https://www.ecb.europa.eu/rss/press.html           (14:15 CET)
#   BOJ  : https://www.boj.or.jp/en/rss/whatsnew.xml          (시각 미정, 결정문 PDF)
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
import csv
import io
import json
import os
import re
import time
import urllib.request
import gzip
import html as html_mod

STATE_FILE = os.path.join(".state", "release_watch.json")

# 발표 몇 초 전부터 baseline(발표 전 원문 지문)을 떠 둘지.
# [중요] 넉넉히 잡아야 한다. 예전 120초는 너무 좁아서, 발표 순간 뉴스가 몰려
# 한 주기(main 의 뉴스 배치 + 429 대기)가 그 2분 창을 통째로 가로지르면 감시가
# 창 안에서 한 번도 안 돌아 baseline 을 못 잡고 조용히 유실됐다(실제 EIA 에서 발생).
# baseline 은 처음 무장 때 한 번만 받아오고(그 뒤 발표까지 재요청 안 함) 커밋돼
# 재시작에도 살아남으므로, 창을 크게 잡아도 요청은 발표당 1회뿐이다.
ARM_BEFORE_SECONDS = 1800     # 발표 30분 전부터 baseline 확보 시도
ARM_AFTER_SECONDS = 300       # 발표 후 몇 초까지 기다릴지
FAST_POLL_SECONDS = 1         # 발표 직후 이 간격으로 확인
SLOW_POLL_SECONDS = 5         # FAST_WINDOW 이후에는 이 간격으로 늦춤
FAST_WINDOW_SECONDS = 60
PRE_POLL_SECONDS = 20         # 발표 전 baseline 재시도 간격
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


def _signed_num(direction, num):
    """부호 없는 숫자에 방향(동사) 기준 부호를 붙인다. % 는 안 붙임."""
    sign = "-" if _DOWN_RE.search(direction or "") else "+"
    return "%s%s" % (sign, num)


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
    # "N percent" 는 선택적. 무변동 달은 "was unchanged in July" 처럼 percent 가 없다.
    mom = re.search(r"CPI-U\)?\s+(increased|declined|rose|fell|edged up|edged down|"
                    r"was unchanged|changed little)(?:\s+([\d.]+)\s+percent)?", t, re.I)
    month = ""
    if mom:
        month = _month_ko(t, mom.group(0))
        val = "0.0%" if mom.group(2) is None else _signed(mom.group(1), mom.group(2))
        items.append(_item("mom", "전월", val))
    core = re.search(r"less food and energy (rose|increased|declined|fell|was unchanged)"
                     r"(?:\s+([\d.]+)\s+percent)?", t, re.I)
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
    # NFP 는 달마다 표현이 갈린다:
    #   "employment (-23,000) and the unemployment rate ..."  (괄호에 부호 포함)
    #   "employment rose by 254,000 in September"             (동사+by, 부호는 동사로)
    m = re.search(r"nonfarm payroll employment\b", t, re.I)
    nfp_val = ""
    if m:
        seg = t[m.end():m.end() + 90]
        paren = re.match(r"\s*\(([+\-][\d,]+)\)", seg)
        if paren:
            nfp_val = paren.group(1)
        else:
            by = re.search(r"(increased|rose|declined|fell|edged up|edged down|"
                           r"was little changed|changed little)\s+by\s+([\d,]+)", seg, re.I)
            if by:
                nfp_val = _signed_num(by.group(1), by.group(2))
    month = _month_ko(t, "nonfarm payroll") if nfp_val else ""
    if nfp_val:
        items.append(_item("nfp", "비농업", nfp_val))
    # 실업률: "rate (4.1 percent)" / "rate held at 4.2 percent" / "rate rose to 4.3 percent"
    # 등 사이에 단어가 낀다. 마침표를 넘지 않는 40자 안에서 첫 숫자를 잡는다.
    unemp = re.search(r"unemployment rate[^.]{0,40}?([\d.]+)\s+percent", t, re.I)
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
        value = ""              # 파싱 성공 시 결정 요약
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
        # 파싱에 성공하면 결정 요약만 깔끔하게 보낸다(다른 지표처럼 숫자만).
        # 파싱에 실패해 요약이 없을 때만, 그거라도 있으라고 링크를 남긴다.
        items = [_item("plain", "", value or link)]
        return fingerprint, {"month": "", "items": items}
    return None


# EIA 주간 석유재고. 밸런스시트 CSV(키 불필요)를 직접 받아 주간 변화(백만 배럴)를 뽑는다.
# 헤더의 STUB_1 라벨과 열 위치는 실측으로 확인:
#   0=라벨, 1=이번주, 2=지난주, 3=Difference(주간 변화) ...
# FJ 캘린더 Previous(17.423M 등)가 이 Difference 열과 정확히 일치함을 확인했다.
_EIA_URL = "https://ir.eia.gov/wpsr/table1.csv"
_EIA_ROWS = [
    ("Commercial (Excluding SPR)", "crude", "원유"),
    ("Total Motor Gasoline", "gasoline", "휘발유"),
    ("Distillate Fuel Oil", "distillate", "증류유"),
]


def _mb(diff):
    """주간 변화(백만 배럴)를 부호 붙여 표기. +는 재고 증가(약세), -는 감소(강세).
    반올림해서 0 이면 부호 없이 0.0M (−0.0M 같은 표기 방지)."""
    r = round(diff, 1)
    if r == 0:
        return "0.0M"
    return "%s%.1fM" % ("+" if r > 0 else "-", abs(r))


def fetch_eia():
    body = http_get(_EIA_URL, BROWSER_UA)
    rows = [r for r in csv.reader(io.StringIO(body)) if r]
    if not rows:
        return None
    header = rows[0]
    week = (header[1].strip() if len(header) > 1 else "")   # 예: 8/7/26
    by_label = {r[0].strip(): r for r in rows}
    items = []
    for label, kind, ko in _EIA_ROWS:
        r = by_label.get(label)
        if not r or len(r) < 4:
            continue
        try:
            diff = float(r[3].replace(",", ""))
        except ValueError:
            continue
        items.append(_item(kind, ko, _mb(diff)))
    if not items:
        return None
    # 이번주 날짜가 릴리스마다 바뀌므로 지문에 넣으면 새 발표 판정이 확실해진다
    fingerprint = "%s|%s" % (week, "".join(i["value"] for i in items))
    week_label = week.rsplit("/", 1)[0] if "/" in week else week   # 8/7/26 -> 8/7
    return fingerprint, {"month": "%s 주간" % week_label if week_label else "", "items": items}


def fetch_ppi():
    # CPI 와 같은 BLS 인프라. 문장 구조만 다르다("for final demand ...").
    ua = bls_ua()
    if not ua:
        return None
    t = to_text(http_get("https://www.bls.gov/news.release/ppi.nr0.htm", ua))
    items = []
    # "N percent" 는 선택적. 무변동 달은 "was unchanged in July" 처럼 percent 가 없다.
    mom = re.search(r"Producer Price Index for final demand\s+(was unchanged|increased|"
                    r"declined|rose|fell|edged up|edged down)(?:\s+([\d.]+)\s+percent)?", t, re.I)
    month = ""
    if mom:
        month = _month_ko(t, mom.group(0))
        items.append(_item("mom", "전월", "0.0%" if mom.group(2) is None
                           else _signed(mom.group(1), mom.group(2))))
    core = re.search(r"final demand less foods,? energy,? and trade services\s+"
                     r"(increased|declined|rose|fell|was unchanged)(?:\s+([\d.]+)\s+percent)?", t, re.I)
    if core:
        items.append(_item("core", "근원", "0.0%" if core.group(2) is None
                           else _signed(core.group(1), core.group(2))))
    yoy = re.search(r"final demand (increased|advanced|rose|declined|fell)\s+([\d.]+)\s+percent"
                    r"[^.]{0,40}?12[ -]month", t, re.I)
    if yoy:
        items.append(_item("yoy", "전년", _signed(yoy.group(1), yoy.group(2))))
    if not items:
        s = first_sentences(t, r"The Producer Price Index for final demand")
        if not s:
            return None
        return s, {"month": "", "items": [_item("plain", "", s)]}
    return _fp(month, items), {"month": month, "items": items}


# GDP 는 BEA RSS(PCE 와 같은 피드)에서. 단 RSS 엔 지역별 GDP(주/카운티/속령)가
# 잔뜩 섞여 있어, 전국 분기 GDP 발표만 골라야 한다("GDP (Advance/Second/Third
# Estimate), Nth Quarter" 형식 + 'at an annual rate' 문구).
_GDP_EST = [("advance", "속보"), ("second", "잠정"), ("third", "확정")]


def fetch_gdp():
    body = http_get("https://apps.bea.gov/rss/rss.xml", BROWSER_UA)
    for it in _rss_items(body):
        title = _tag(it, "title")
        if not re.match(r"GDP \(.*Estimate", title, re.I):     # 전국 분기 GDP 만
            continue
        desc = _tag(it, "description")
        m = re.search(r"(increased|decreased)\s+at an annual rate of\s+([\d.]+)\s+percent",
                      desc, re.I)
        if not m:
            continue
        q = re.search(r"(\d)(?:st|nd|rd|th)\s+Quarter", title, re.I)
        est = next((ko for k, ko in _GDP_EST if k in title.lower()), "")
        label = " ".join(x for x in ["%s분기" % q.group(1) if q else "", est] if x)
        return "%s|%s" % (title, m.group(2)), {
            "month": label, "items": [_item("gdp", "성장률(연율)", _signed(m.group(1), m.group(2)))]}
    return None


# ---------------------------------------------------------------- PDF 원문 (실업수당 / BOJ)
#
# DOL 실업수당과 BOJ 성명은 PDF 로만 나온다. BOJ 는 글자를 폰트 코드표로 감싸 둬서
# 직접 풀기엔 손이 많이 가므로 순수 파이썬 라이브러리 pypdf 를 쓴다(워크플로에서 설치).
# 없으면 이 두 감시만 꺼지고 나머지는 그대로 돈다.
_pdf_notice_shown = False


def pdf_text(raw):
    global _pdf_notice_shown
    try:
        import pypdf
    except ImportError:
        if not _pdf_notice_shown:
            _pdf_notice_shown = True
            print("[지표감시] pypdf 가 없어 실업수당/BOJ 감시 꺼짐")
        return None
    reader = pypdf.PdfReader(io.BytesIO(raw))
    return re.sub(r"\s+", " ", " ".join(p.extract_text() or "" for p in reader.pages[:2]))


def http_get_bytes(url, ua, timeout=HTTP_TIMEOUT, method="GET"):
    req = urllib.request.Request(url, method=method, headers={
        "User-Agent": ua, "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.headers


def _bust(url):
    """DOL 은 아카마이 캐시(max-age 약 7분)를 거친다. 발표 직후 옛 파일을 받지 않도록
    쿼리를 붙여 캐시를 비켜 간다."""
    return "%s?_=%d" % (url, int(time.time()))


# 실업수당: 파일이 0.5MB 라 매초 받기엔 무겁다. 헤더(HEAD)의 Last-Modified 만 매번
# 확인하고(발표 시각 12:30:00 GMT 로 정확히 찍힘, 실측), 바뀌었을 때만 본문을 받는다.
# DOL 은 브라우저 UA 와 연락처 없는 봇 UA 를 403 으로 막는다. BLS 처럼 이메일이 든 UA,
# 또는 파이썬 기본 UA 는 통과한다(실측). CONTACT_EMAIL 이 있으면 그걸 쓴다.
_DOL_URL = "https://www.dol.gov/ui/data.pdf"


def _dol_ua():
    return bls_ua() or "Python-urllib/3"
_claims_cache = {}      # Last-Modified -> (지문, detail)


def _k(n):
    """197,000 -> '197K', 1,716,000 -> '1,716K'"""
    return "{:,}K".format(int(n.replace(",", "")) // 1000)


def fetch_claims():
    _, hdr = http_get_bytes(_bust(_DOL_URL), _dol_ua(), method="HEAD")
    lm = hdr.get("Last-Modified") or ""
    if lm and lm in _claims_cache:
        return _claims_cache[lm]
    raw, _ = http_get_bytes(_bust(_DOL_URL), _dol_ua(), timeout=15)
    t = pdf_text(raw)
    if not t:
        return None
    items = []
    init = re.search(r"seasonally adjusted initial claims was ([\d,]+)", t, re.I)
    if init:
        items.append(_item("claims_init", "신규", _k(init.group(1))))
    cont = re.search(r"insured unemployment during the week ending [A-Z][a-z]+ \d+ was ([\d,]+)", t, re.I)
    if cont:
        items.append(_item("claims_cont", "연속", _k(cont.group(1))))
    if not items:
        return None
    wk = re.search(r"week ending ([A-Z][a-z]+) (\d+)", t)
    week = ""
    if wk and wk.group(1).lower() in _EN_MONTH:
        week = "%s/%s 주간" % (_EN_MONTH[wk.group(1).lower()][:-1], wk.group(2))
    got = ("%s|%s" % (week, "".join(i["value"] for i in items)), {"month": week, "items": items})
    if lm:
        _claims_cache.clear()
        _claims_cache[lm] = got
    return got


# 중앙은행은 RSS 에 새 결정문 링크가 뜨는 것으로 감지하고, 본문은 링크당 한 번만 받는다.
# RSS 에는 최근 15건 정도만 있어서 지난 회의 결정문이 이미 밀려나 있을 수 있다.
# 그럴 땐 "없음" 자체를 기준으로 삼는다(NO_DOC). 그래야 새 결정문이 뜨는 순간 바뀐 것으로 잡힌다.
NO_DOC = "<none>"
_doc_cache = {}         # 결정문 링크 -> detail


def _cached_doc(link, parse):
    if link not in _doc_cache:
        _doc_cache.clear()
        _doc_cache[link] = parse(link)
    return _doc_cache[link]


_ECB_ACTION = {"raise": "인상", "increase": "인상", "lower": "인하", "reduce": "인하",
               "cut": "인하", "keep": "동결", "leave": "동결", "maintain": "동결"}


def _parse_ecb(link):
    t = to_text(http_get(link, BROWSER_UA))
    head = ""
    m = re.search(r"decided to (raise|increase|lower|reduce|cut|keep|leave|maintain) "
                  r"the three key ECB interest rates(?: by (\d+)\s+basis points)?", t, re.I)   # 숫자 뒤가 nbsp 인 경우 있음(실측)
    if m:
        act = _ECB_ACTION[m.group(1).lower()]
        head = "%sbp %s" % (m.group(2), act) if m.group(2) else act
    items = []
    rates = re.search(r"deposit facility, the main refinancing operations and the marginal "
                      r"lending facility[^.]*?(-?[\d.]+)%,\s*(-?[\d.]+)%", t, re.I)
    if rates:
        items.append(dict(_item("deposit", "예금금리", rates.group(1) + "%"), num=float(rates.group(1))))
        items.append(dict(_item("refi", "기준금리(MRO)", rates.group(2) + "%"), num=float(rates.group(2))))
    if not items:
        items = [_item("plain", "", head or link)]
        head = ""
    return {"month": head, "items": items}


def fetch_ecb():
    body = http_get("https://www.ecb.europa.eu/rss/press.html", BROWSER_UA)
    for it in _rss_items(body):
        link = _tag(it, "link")
        if _tag(it, "title") == "Monetary policy decisions" or re.search(r"/ecb\.mp\d{6}", link):
            return link, _cached_doc(link, _parse_ecb)
    return NO_DOC, None


def _parse_boj(link):
    raw, _ = http_get_bytes(link, BROWSER_UA, timeout=15)
    t = pdf_text(raw) or ""
    vote = re.search(r"by (?:an? )?(unanimous|\d+-\d+ majority) vote", t, re.I)
    head = ""
    if vote:
        head = "만장일치" if vote.group(1).lower() == "unanimous" \
            else "%s 다수결" % vote.group(1).split()[0]
    rate = re.search(r"call rate to remain at around ([\d.]+)\s*percent", t, re.I)
    if rate:
        # 인상/인하/동결은 캘린더 이전치와 비교해 build_message 에서 붙인다(auto_action)
        items = [dict(_item("rate", "정책금리", rate.group(1) + "%"),
                      num=float(rate.group(1)), auto_action=True)]
    else:
        items = [_item("plain", "", link)]
    return {"month": head, "items": items}


def fetch_boj():
    body = http_get("https://www.boj.or.jp/en/rss/whatsnew.xml", BROWSER_UA)
    for it in _rss_items(body):
        link = _tag(it, "link").replace("http://", "https://", 1)
        # 결정문은 제목이 회의마다 달라서(동결/변경) 주소 형식으로 고른다: .../mpr_2026/k260918a.pdf
        if re.search(r"/mopo/mpmdeci/mpr_\d{4}/k\d{6}a\.pdf$", link):
            return link, _cached_doc(link, _parse_boj)
    return NO_DOC, None


# 캘린더 하위 항목 제목 -> 종류표. 추출 항목의 kind 와 짝지어 예상/이전치를 붙인다.
def calendar_kind(title):
    t = (title or "").lower()
    # 실업수당 / 중앙은행 금리. 아래 일반 규칙보다 먼저 걸러야 mom 으로 새지 않는다.
    if "claim" in t:
        if "continu" in t:
            return "claims_cont"
        return "claims_avg" if ("average" in t or "4-week" in t) else "claims_init"
    if "deposit" in t:
        return "deposit"
    if "refinanc" in t:
        return "refi"
    if "rate decision" in t or "interest rate" in t or "policy rate" in t:
        return "rate"
    # EIA 석유재고 (원유/휘발유/증류유). core/yoy 판정보다 먼저 걸러야 오분류가 없다.
    if "cushing" in t:
        return "cushing"
    if "gasoline" in t:
        return "gasoline"
    if "distillate" in t:
        return "distillate"
    if "crude" in t and ("invent" in t or "stock" in t):
        return "crude"
    # GDP 성장률(가격지수 GDP Price Index 는 제외 -> mom/yoy 로 흐르게)
    if ("gdp" in t or "gross domestic" in t) and "price" not in t:
        return "gdp"
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
     "cc": "US", "pattern": r"non.?farm|payroll|employment situation|unemployment",
     # ADP 민간고용(수요일)도 'nonfarm' 에 걸려 BLS 페이지를 헛되이 두드리게 된다
     "exclude": r"\bADP\b", "fetch": fetch_empsit},
    {"key": "pce",    "label": "🇺🇸 미국 PCE 물가",
     "cc": "US", "pattern": r"\bPCE\b|personal (income|consumption|spending)", "fetch": fetch_pce},
    {"key": "fomc",   "label": "🇺🇸 FOMC 금리결정",
     "cc": "US", "pattern": r"FOMC (rate|statement|interest)|fed(eral)? funds rate decision",
     "fetch": fetch_fomc},
    {"key": "eia",    "label": "🛢️ EIA 석유재고",
     "cc": "US", "pattern": r"EIA (Crude|Gasoline|Distillate)[^,]*Invent",
     "fetch": fetch_eia},
    {"key": "ppi",    "label": "🇺🇸 미국 PPI",
     "cc": "US", "pattern": r"\bPPI\b|producer price", "fetch": fetch_ppi},
    {"key": "gdp",    "label": "🇺🇸 미국 GDP",
     "cc": "US", "pattern": r"\bGDP\b|gross domestic", "fetch": fetch_gdp},
    {"key": "claims", "label": "🇺🇸 미국 실업수당 청구",
     "cc": "US", "pattern": r"jobless claims", "exclude": r"average|4-week",
     "fetch": fetch_claims},
    {"key": "ecb",    "label": "🇪🇺 ECB 금리결정",
     "cc": "EU", "pattern": r"\bECB\b.*(rate|deposit|refinanc)|deposit facility|refinancing rate",
     "exclude": r"speaks|press conference|accounts|minutes", "fetch": fetch_ecb},
    # BOJ 는 발표 시각이 정해져 있지 않다(캘린더 시각은 자리표시, 실제는 11:30~13:00 JST
    # 무렵 회의가 끝나는 대로). 그래서 2시간 전부터 기준을 잡고, 캘린더 시각 전이라도
    # 바뀌면 바로 보내며(early), 캘린더 시각 후 3시간까지 3초 간격으로 지켜본다.
    {"key": "boj",    "label": "🇯🇵 BOJ 금리결정",
     "cc": "JP", "pattern": r"\bBoJ\b.*(rate|policy)",
     "exclude": r"speaks|press conference|minutes|summary|outlook|statement",
     "fetch": fetch_boj, "arm_before": 7200, "arm_after": 3 * 3600,
     "early": True, "slow_poll": 3},
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
        if watcher.get("exclude") and re.search(watcher["exclude"], e.get("Title") or "", re.I):
            continue
        t = t_utc.timestamp()
        before = watcher.get("arm_before", ARM_BEFORE_SECONDS)
        after = watcher.get("arm_after", ARM_AFTER_SECONDS)
        if not (t - before <= now_epoch <= t + after):
            continue
        # 가장 이른 발표 시각의 묶음만. 같은 시각의 하위 항목들을 함께 모은다
        if best_t is None or t < best_t:
            best_t, group = t, [e]
        elif abs(t - best_t) < 1:
            group.append(e)
    return (best_t, group) if group else None


def due_to_poll(key, now_epoch, seconds_since_release, watcher=None, has_base=False):
    watcher = watcher or {}
    # 발표 전(baseline 확보 단계)에는 느리게. 1초로 두면 출처가 막혀 baseline 이
    # 계속 실패할 때 30분 창 동안 초당 1회(약 1800회)를 쏴서 차단을 더 굳힌다.
    # 단 early 감시(BOJ)는 기준을 잡은 뒤엔 발표 전이라도 slow_poll 로 계속 본다.
    if seconds_since_release < 0:
        gap = (watcher.get("slow_poll", SLOW_POLL_SECONDS)
               if has_base and watcher.get("early") else PRE_POLL_SECONDS)
    elif seconds_since_release <= FAST_WINDOW_SECONDS:
        gap = FAST_POLL_SECONDS
    else:
        gap = watcher.get("slow_poll", SLOW_POLL_SECONDS)
    return now_epoch - _last_poll.get(key, 0) >= gap


def _num(s):
    m = re.search(r"-?\d+(?:\.\d+)?", str(s or "").replace(",", ""))
    return float(m.group(0)) if m else None


def _thousands(s):
    return "{:,}K".format(int(s)) if s and s.isdigit() else s


def build_message(label, detail, events):
    """한글 라벨 + 실제 숫자 + 항목별 예상/이전(캘린더) 조립. 번역 없음.

    events 는 같은 시각의 캘린더 하위 항목들. 추출한 각 숫자(kind)를 제목으로
    분류한 캘린더 항목과 짝지어, 그 항목의 예상/이전치를 옆에 붙인다.
    짝이 없으면 숫자만 보여준다."""
    import calendar_relay as cal
    by_kind = {}
    for e in events or []:
        by_kind.setdefault(calendar_kind(e.get("Title")), e)

    items = detail.get("items", [])
    # 캘린더가 'ECB Rate Decision' 처럼 금리 하나만 주는데 결정문엔 금리가 둘(예금/MRO)
    # 이면, 그 예상치(없으면 이전치)에 값이 가장 가까운 쪽에만 붙인다.
    rate_e, rate_target = by_kind.get("rate"), None
    cands = [it for it in items if it.get("num") is not None and it["kind"] not in by_kind]
    if rate_e is not None and cands:
        ref = _num(rate_e.get("Forecast")) if _num(rate_e.get("Forecast")) is not None \
            else _num(rate_e.get("Previous"))
        rate_target = cands[0] if ref is None else min(cands, key=lambda it: abs(it["num"] - ref))

    month = detail.get("month") or ""
    out = ["🚨 %s%s" % (label, " (%s)" % month if month else "")]
    for it in items:
        line = ("%s %s" % (it["label"], it["value"])).strip()
        # kind 를 정확히 맞는 캘린더 항목하고만 짝짓는다. core(전월)에 core_yoy(전년)를
        # 붙이면 월간 값 옆에 연간 예상치가 붙어 오히려 오해를 준다.
        e = by_kind.get(it["kind"]) or (rate_e if it is rate_target else None)
        if e is not None:
            fc = cal.val(e.get("Forecast"))
            pv = cal.val(e.get("Previous"))
            # 실업수당은 캘린더가 천 단위 숫자만 준다("200") -> 우리 표기("197K")에 맞춤
            if it["kind"].startswith("claims_"):
                fc, pv = _thousands(fc), _thousands(pv)
            # BOJ 결정문은 인상/인하를 글로 안 쓰고 새 금리만 적는다 -> 이전치와 비교
            prev = _num(pv)
            if it.get("auto_action") and prev is not None:
                d = round(it["num"] - prev, 4)
                line += " %s" % ("인상" if d > 0 else "인하" if d < 0 else "동결")
            if fc or pv:
                line += " (예상 %s·이전 %s)" % (fc or "-", pv or "-")
        out.append(line)
    out.append("#속보")
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
        has_base = slot.get("baseline_for") == event_id
        pre = now_epoch < t_release

        # 발표 전에 기준을 이미 잡아 뒀으면 발표 시각까지는 더 받아올 필요가 없다.
        # 이 가드가 없으면 발표 2분 전부터 1초마다(FAST 창) 본문 페이지를 다시 받아
        # BEA/Fed 에 발표 직전 100여 회를 쏘게 된다(정작 잡아야 할 순간에 차단 위험).
        # 발표 시각이 정해지지 않은 early 감시(BOJ)만 예외로 계속 본다.
        if pre and has_base and not w.get("early"):
            continue

        if not due_to_poll(w["key"], now_epoch, since, w, has_base):
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
        if pre and not has_base:
            state[w["key"]] = {"baseline": fingerprint, "baseline_for": event_id}
            save_state(state)
            print("[지표감시] %s 기준 확보 (발표 %.0f초 전)" % (w["key"], t_release - now_epoch))
            continue

        # 기준을 못 잡은 채로 발표 시각을 넘겼으면(릴레이가 그 사이 죽어 있었음)
        # 지금 내용이 새 것인지 판단할 수 없다. 오발송보다 침묵이 낫다.
        if slot.get("baseline_for") != event_id:
            continue
        if fingerprint == slot.get("baseline") or detail is None:
            continue

        msg = build_message(w["label"], detail, events)
        ok, permanent = (send or _send)(bot_token, msg)
        if ok or permanent:
            slot["done_for"] = event_id
            state[w["key"]] = slot
            save_state(state)
            sent += int(ok)
            print("[지표감시] %s 발송 (일정 시각 대비 %+.1f초)" % (w["key"], since))

    return sent


def _send(bot_token, msg):
    import calendar_relay as cal
    import financialjuice_relay as fj
    return fj.send_telegram(bot_token, cal.CHAT_ID, msg)
