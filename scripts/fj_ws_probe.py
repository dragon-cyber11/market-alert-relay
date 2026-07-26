# FinancialJuice 탐사 v6 (최종 검증)
# 흐름: 홈페이지 HTML에서 info 값 추출 -> 뉴스 API 호출 -> Breaking/Level 확인
# - 이게 되면 빨간 뉴스(속보) 구분해서 텔레그램에 보낼 수 있음
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
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

out_lines = []


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    out_lines.append(s)


def get(url, timeout=30):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://www.financialjuice.com/",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return -1, repr(e)[:300]


def fetch_info():
    """홈페이지 HTML에서 var info = '...' 값 추출"""
    status, html = get(HOME_URL, timeout=40)
    log("홈페이지 HTTP", status, "| 길이", len(html))
    if status != 200:
        return None
    m = re.search(r"var\s+info\s*=\s*'([^']+)'", html)
    if not m:
        m = re.search(r'var\s+info\s*=\s*"([^"]+)"', html)
    if not m:
        log("info 값을 못 찾음")
        return None
    info = m.group(1)
    log("info 추출 성공 (길이 %d): %s..." % (len(info), info[:50]))
    return info


def call_news(info, tab_id=0, old_id=0):
    params = {
        "info": '"%s"' % info,
        "TimeOffset": "0",
        "tabID": str(tab_id),
        "oldID": str(old_id),
        "TickerID": "0",
        "FeedCompanyID": "0",
        "strSearch": '""',
        "extraNID": "0",
    }
    url = API + "?" + "&".join(
        "%s=%s" % (k, urllib.parse.quote(v, safe="")) for k, v in params.items()
    )
    status, body = get(url)
    log("\nAPI 호출 (tabID=%s, oldID=%s) -> HTTP %s | 길이 %d" % (tab_id, old_id, status, len(body)))
    if status != 200:
        log(body[:600])
        return None

    # <string xmlns="...">JSON</string> 형태라 안쪽 JSON만 꺼냄
    m = re.search(r"<string[^>]*>(.*)</string>", body, re.DOTALL)
    inner = m.group(1) if m else body
    inner = (inner.replace("&lt;", "<").replace("&gt;", ">")
                  .replace("&quot;", '"').replace("&amp;", "&"))
    try:
        data = json.loads(inner)
    except Exception as e:
        log("JSON 파싱 실패:", e)
        log(inner[:600])
        return None

    news = data.get("News") or []
    log("뉴스 %d건 수신" % len(news))
    if not news:
        log("(빈 응답) 원본 앞부분:", inner[:300])
        return data

    levels = {}
    breaking = {}
    for n in news:
        levels[n.get("Level")] = levels.get(n.get("Level"), 0) + 1
        breaking[n.get("Breaking")] = breaking.get(n.get("Breaking"), 0) + 1
    log("Level 값 분포:", levels)
    log("Breaking 값 분포:", breaking)

    log("\n--- 최근 뉴스 8건 ---")
    for n in news[:8]:
        log("  [%s] Breaking=%s Level=%r Type=%r | %s" % (
            n.get("PostedLong"), n.get("Breaking"), n.get("Level"),
            n.get("TypeID"), (n.get("Title") or "")[:90]))

    log("\n--- 첫 뉴스의 전체 필드 ---")
    log(json.dumps(news[0], ensure_ascii=False)[:1200])
    return data


def main():
    os.makedirs(".state", exist_ok=True)

    info = fetch_info()
    if not info:
        log("info 추출 실패로 중단")
    else:
        # 기본 탭
        data = call_news(info, tab_id=0)
        # 다른 탭들도 확인 (탭마다 뉴스 종류가 다를 수 있음)
        for t in [1, 2]:
            call_news(info, tab_id=t)

        # info 를 두 번째로 재사용해도 되는지 (매번 홈페이지 안 긁어도 되는지) 확인
        log("\n===== info 재사용 테스트 =====")
        call_news(info, tab_id=0)

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines))
    log("\n탐사 종료")


if __name__ == "__main__":
    main()
