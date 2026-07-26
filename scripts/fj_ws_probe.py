# FinancialJuice 탐사 v7 (빨간 뉴스 = 어떤 필드인지 확정)
# - 사이트 CSS에서 빨간색으로 칠하는 클래스가 뭔지 확인
# - 뉴스 API를 과거로 거슬러 올라가며 훑어서, 실제 빨간 뉴스 사례를 찾아
#   그 뉴스의 Breaking / Level 값이 뭔지 확인
# - 텔레그램 전송 안 함. 결과는 .state/fj_api_probe.txt 에 저장
import json
import os
import re
import urllib.request
import urllib.error
import urllib.parse

OUT_FILE = ".state/fj_api_probe.txt"
HOME_URL = "https://www.financialjuice.com/home"
API = "https://live.financialjuice.com/FJService.asmx/Startup"
CSS_URLS = [
    "https://www.financialjuice.com/assets/css/custom.css?v=2.8.9",
    "https://www.financialjuice.com/assets/css/custom-dark.css?v=2.8.9",
]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

out_lines = []


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    out_lines.append(s)


def get(url, timeout=30):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Referer": "https://www.financialjuice.com/"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return -1, repr(e)[:300]


def check_css():
    """빨간색(#f00 계열) 배경을 쓰는 뉴스 클래스 찾기"""
    log("\n========== CSS에서 빨간색 규칙 찾기 ==========")
    for url in CSS_URLS:
        status, css = get(url)
        log("\n--- %s (HTTP %s, %d자) ---" % (url.split("/")[-1], status, len(css)))
        if status != 200:
            continue
        css1 = re.sub(r"\s+", " ", css)
        # 빨간색 값이 들어간 규칙 중 news/feed/break 관련만 추림
        for m in re.finditer(r"([^{}]{0,160})\{([^{}]{0,200})\}", css1):
            sel, body = m.group(1).strip(), m.group(2)
            if not re.search(r"(#(f|e|d|c|b)[0-9a-f]{2}[0-3][0-9a-f]{2}[0-3][0-9a-f]{2}|"
                             r"#(f|e|d|c)[0-3][0-3]\b|red|rgb\(\s*(1[6-9][0-9]|2[0-5][0-9])\s*,\s*[0-5]?[0-9]\s*,)",
                             body, re.IGNORECASE):
                continue
            if re.search(r"(news|feed|break|headline|imp)", sel, re.IGNORECASE):
                log("  %s { %s }" % (sel[:120], body[:150]))


def fetch_info():
    status, html = get(HOME_URL, timeout=40)
    if status != 200:
        log("홈페이지 실패:", status)
        return None
    m = re.search(r"var\s+info\s*=\s*'([^']+)'", html)
    if not m:
        log("info 못 찾음")
        return None
    log("info 추출 완료 (길이 %d)" % len(m.group(1)))
    return m.group(1)


def call_news(info, tab_id=0, old_id=0, search=""):
    params = {
        "info": '"%s"' % info, "TimeOffset": "0", "tabID": str(tab_id),
        "oldID": str(old_id), "TickerID": "0", "FeedCompanyID": "0",
        "strSearch": '"%s"' % search, "extraNID": "0",
    }
    url = API + "?" + "&".join(
        "%s=%s" % (k, urllib.parse.quote(v, safe="")) for k, v in params.items())
    status, body = get(url)
    if status != 200:
        return None
    m = re.search(r"<string[^>]*>(.*)</string>", body, re.DOTALL)
    inner = (m.group(1) if m else body)
    inner = (inner.replace("&lt;", "<").replace("&gt;", ">")
                  .replace("&quot;", '"').replace("&amp;", "&"))
    try:
        return json.loads(inner)
    except Exception:
        return None


def main():
    os.makedirs(".state", exist_ok=True)
    check_css()

    info = fetch_info()
    if not info:
        log("중단")
    else:
        # 1) 스크린샷에 있던 실제 빨간 뉴스를 검색해서 그 필드값 확인
        log("\n========== 알려진 빨간 뉴스 검색 ==========")
        for q in ["substantial tariff", "Section 301", "pay a very big price"]:
            data = call_news(info, search=q)
            news = (data or {}).get("News") or []
            log("\n검색어 %r -> %d건" % (q, len(news)))
            for n in news[:5]:
                log("  Breaking=%s Level=%r Upd=%r Type=%r STID=%s | [%s] %s" % (
                    n.get("Breaking"), n.get("Level"), n.get("Upd"), n.get("TypeID"),
                    n.get("STID"), n.get("PostedLong"), (n.get("Title") or "")[:80]))
            if news:
                log("  (첫 결과 전체 필드) " + json.dumps(news[0], ensure_ascii=False)[:900])

        # 2) 과거로 거슬러 올라가며 Breaking=true / 특이한 Level 이 있는지 통계
        log("\n========== 과거 뉴스 훑기 (Breaking/Level 통계) ==========")
        levels, breaks = {}, {}
        specials = []
        old_id = 0
        total = 0
        for page in range(25):
            data = call_news(info, tab_id=0, old_id=old_id)
            news = (data or {}).get("News") or []
            if not news:
                log("페이지 %d: 응답 없음, 중단" % page)
                break
            total += len(news)
            for n in news:
                lv, bk = n.get("Level"), n.get("Breaking")
                levels[lv] = levels.get(lv, 0) + 1
                breaks[bk] = breaks.get(bk, 0) + 1
                if bk or (lv not in ("", "news-general")):
                    specials.append(n)
            old_id = min(int(n.get("NewsID", 0)) for n in news)
            if page % 5 == 0:
                log("  ...%d페이지 (누적 %d건, oldID=%s)" % (page + 1, total, old_id))

        log("\n총 %d건 확인" % total)
        log("Level 분포:", levels)
        log("Breaking 분포:", breaks)
        log("특이 항목 %d건" % len(specials))
        for n in specials[:15]:
            log("  Breaking=%s Level=%r | [%s] %s" % (
                n.get("Breaking"), n.get("Level"), n.get("PostedLong"),
                (n.get("Title") or "")[:80]))

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines))
    log("\n탐사 종료")


if __name__ == "__main__":
    main()
