# 모닝주스(Morning Juice) 본문을 가져올 수 있는지 확인하는 탐사 스크립트
# 세 가지를 한 번에 확인함:
#   1) 뉴스 API에서 "Morning Juice" 를 검색해 그 항목의 전체 필드(특히 Description) 확인
#   2) GetNewsSummary 메서드가 본문을 주는지 확인
#   3) 기사 페이지 HTML을 직접 받아서 본문이 들어있는지 확인
# 텔레그램 전송 안 함. 결과는 .state/fj_mj_probe.txt 에 저장
import gzip
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

OUT_FILE = ".state/fj_mj_probe.txt"
HOME_URL = "https://www.financialjuice.com/home"
BASE = "https://live.financialjuice.com/FJService.asmx"
ARTICLE_URL = ("https://www.financialjuice.com/News/9694884/"
               "Morning-Juice---US-Session-Prep-27th-July.aspx")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

lines = []


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    lines.append(s)


def http_get(url, timeout=30, referer="https://www.financialjuice.com/"):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Referer": referer, "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                raw = gzip.decompress(raw)
            return r.status, raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception:
            return e.code, ""
    except Exception as e:
        return -1, repr(e)[:300]


def get_info():
    st, html = http_get(HOME_URL, timeout=40)
    m = re.search(r"var\s+info\s*=\s*'([^']+)'", html)
    if not m:
        log("info 값을 못 찾음 (HTTP %s)" % st)
        return None
    log("info 추출 완료 (길이 %d)" % len(m.group(1)))
    return m.group(1)


def unwrap(body):
    m = re.search(r"<string[^>]*>(.*)</string>", body, re.DOTALL)
    inner = m.group(1) if m else body
    return (inner.replace("&lt;", "<").replace("&gt;", ">")
                 .replace("&quot;", '"').replace("&amp;", "&"))


def call(method, params):
    url = BASE + "/" + method + "?" + "&".join(
        "%s=%s" % (k, urllib.parse.quote(v, safe="")) for k, v in params.items())
    return http_get(url)


def main():
    os.makedirs(".state", exist_ok=True)
    info = get_info()
    if not info:
        return

    # ---------- 1) 뉴스 API에서 Morning Juice 검색 ----------
    log("\n========== 1) 뉴스 API 검색: 'Morning Juice' ==========")
    st, body = call("Startup", {
        "info": '"%s"' % info, "TimeOffset": "0", "tabID": "0", "oldID": "0",
        "TickerID": "0", "FeedCompanyID": "0",
        "strSearch": '"Morning Juice"', "extraNID": "0"})
    log("HTTP %s" % st)
    if st == 200:
        try:
            news = json.loads(unwrap(body)).get("News") or []
            log("검색 결과 %d건" % len(news))
            for n in news[:3]:
                log("\n--- NewsID %s | %s ---" % (n.get("NewsID"), n.get("PostedLong")))
                log("Title: %s" % (n.get("Title") or "")[:200])
                desc = n.get("Description") or ""
                log("Description 길이: %d" % len(desc))
                log("Description 앞부분: %s" % desc[:1500])
                log("EURL: %s" % (n.get("EURL") or "")[:200])
                log("HasE=%s RID=%s FCName=%s Level=%r" % (
                    n.get("HasE"), n.get("RID"), n.get("FCName"), n.get("Level")))
        except Exception as e:
            log("파싱 실패: %s" % e)
            log(unwrap(body)[:800])

    # ---------- 2) GetNewsSummary ----------
    log("\n\n========== 2) GetNewsSummary (본문 요약 메서드) ==========")
    st, body = call("GetNewsSummary", {"info": '"%s"' % info, "TimeOffset": "0"})
    log("HTTP %s | 응답 길이 %d" % (st, len(body)))
    inner = unwrap(body)
    text = re.sub(r"<[^>]+>", " ", inner)
    text = re.sub(r"\s+", " ", text).strip()
    log("태그 제거 후 길이: %d" % len(text))
    log("내용 앞부분:\n%s" % text[:2000])

    # ---------- 3) 기사 페이지 직접 확인 ----------
    log("\n\n========== 3) 기사 페이지 HTML ==========")
    st, html = http_get(ARTICLE_URL, timeout=40)
    log("HTTP %s | 길이 %d" % (st, len(html)))
    if st == 200:
        # 본문이 들어있을 만한 영역 찾기
        for pat in [r'<meta name="description" content="([^"]{40,})"',
                    r'<meta property="og:description" content="([^"]{40,})"']:
            m = re.search(pat, html)
            log("%s -> %s" % (pat[:40], (m.group(1)[:400] + "...") if m else "없음"))
        # 본문처럼 보이는 긴 문단 추출
        paras = re.findall(r"<p[^>]*>(.*?)</p>", html, re.DOTALL)
        paras = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", p)).strip() for p in paras]
        paras = [p for p in paras if len(p) > 60]
        log("길이 60자 넘는 <p> 문단 %d개" % len(paras))
        for p in paras[:8]:
            log("  - %s" % p[:250])
        # 페이지에 본문이 JSON 으로 박혀 있는 경우도 확인
        for kw in ["NewsBody", "newsDetail", "ArticleBody", "articleBody", "NewsText"]:
            if kw in html:
                i = html.find(kw)
                log("\n'%s' 발견: %s" % (kw, html[max(0, i - 100):i + 400].replace("\n", " ")))

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log("\n탐사 종료")


if __name__ == "__main__":
    main()
