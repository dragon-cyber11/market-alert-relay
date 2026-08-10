# 인베스팅닷컴 경제일정 위젯 구조 확인용 탐사 스크립트
#
# 인베스팅은 다른 사이트에 갖다 붙이라고 공개 임베드 위젯을 제공함(sslecal2.investing.com).
# 이 위젯 HTML 안에 시간/국가/중요도(별)/지표명/예상치/이전치가 들어있는지,
# 별 개수가 어떤 형태로 표시되는지 확인하는 게 목적.
#
# 텔레그램 전송 안 함. 결과는 .state/inv_probe.txt (요약) 과
# .state/inv_widget.html (원본 HTML) 로 저장.
import gzip
import os
import re
import urllib.error
import urllib.parse
import urllib.request

OUT_TXT = ".state/inv_probe.txt"
OUT_HTML = ".state/inv_widget.html"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# 위젯 파라미터
#   columns    : 표시할 열
#   importance : 1,2,3 = 별 1~3개 (일단 전부 받아서 어떻게 구분되는지 확인)
#   countries  : 국가 코드 (미국5, 유로존72, 중국37, 일본35, 영국4, 한국11 등으로 알려져 있음)
#   timeZone   : 88 = 서울
#   lang       : 1 = 영어
WIDGET_URL = (
    "https://sslecal2.investing.com/"
    "?columns=exc_flags,exc_currency,exc_importance,exc_actual,exc_forecast,exc_previous"
    "&features=datepicker,timezone"
    "&countries=5,72,37,35,4,11"
    "&importance=1,2,3"
    "&calType=day"
    "&timeZone=88"
    "&lang=1"
)

lines = []


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    lines.append(s)


def http_get(url, timeout=40):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://www.investing.com/",
        "Accept-Encoding": "gzip",
        "Accept-Language": "en-US,en;q=0.9",
    })
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


def main():
    os.makedirs(".state", exist_ok=True)

    log("=== 위젯 요청 ===")
    log(WIDGET_URL)
    status, html = http_get(WIDGET_URL)
    log("HTTP %s | 길이 %d" % (status, len(html)))

    if status != 200 or len(html) < 500:
        log("가져오기 실패. 앞부분:")
        log(html[:1000])
        with open(OUT_TXT, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return

    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html[:600000])
    log("원본 HTML 저장 완료")

    # ---------- 표 구조 파악 ----------
    log("\n=== 이벤트 행(tr) 개수 ===")
    rows = re.findall(r"<tr[^>]*id=[\"']eventRowId[^>]*>.*?</tr>", html, re.DOTALL)
    if not rows:
        rows = re.findall(r"<tr[^>]*class=[\"'][^\"']*js-event-item[^\"']*[^>]*>.*?</tr>",
                          html, re.DOTALL)
    if not rows:
        rows = re.findall(r"<tr[^>]*>.*?</tr>", html, re.DOTALL)
        log("(전용 패턴 실패, 모든 tr 사용)")
    log("행 %d개" % len(rows))

    log("\n=== 중요도(별)를 나타내는 클래스 후보 ===")
    icons = re.findall(r'class="([^"]*(?:[Bb]ull|[Ss]tar|importance)[^"]*)"', html)
    from collections import Counter
    for cls, cnt in Counter(icons).most_common(12):
        log("  %-45s %d회" % (cls, cnt))

    log("\n=== 앞쪽 행 5개 원본 그대로 ===")
    for r in rows[:5]:
        compact = re.sub(r"\s+", " ", r)
        log("\n---- 행 ----")
        log(compact[:1600])

    log("\n=== 행에서 텍스트만 뽑아본 것 (10개) ===")
    for r in rows[:10]:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", r, re.DOTALL)
        txt = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip() for c in cells]
        txt = [t for t in txt if t]
        if txt:
            log("  " + " | ".join(txt)[:220])

    with open(OUT_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log("\n탐사 종료")


if __name__ == "__main__":
    main()
