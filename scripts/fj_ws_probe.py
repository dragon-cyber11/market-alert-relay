# FinancialJuice 탐사 v4 (뉴스 API 직접 호출 테스트)
# - 사이트가 쓰는 뉴스 JSON API(live.financialjuice.com/FJService.asmx)를
#   우리가 직접 호출할 수 있는지, 어떤 메서드가 있는지 확인
# - Breaking / Level 필드(빨간 뉴스 표시)를 가져올 수 있는지가 핵심
# - 텔레그램 전송 안 함. 결과는 .state/fj_api_probe.txt 에 저장
import json
import os
import urllib.request
import urllib.error
import urllib.parse

OUT_FILE = ".state/fj_api_probe.txt"
BASE = "https://live.financialjuice.com/FJService.asmx"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

out_lines = []


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    out_lines.append(s)


def probe(label, url, headers=None, max_show=3000):
    log("\n===== %s =====" % label)
    log("URL:", url[:200])
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://www.financialjuice.com/",
        "Origin": "https://www.financialjuice.com",
        **(headers or {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            body = r.read().decode("utf-8", "replace")
            log("HTTP", r.status, "| 길이", len(body))
            log(body[:max_show])
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        log("HTTPError", e.code, "| 길이", len(body))
        log(body[:1500])
    except Exception as e:
        log("실패:", repr(e)[:300])


def main():
    os.makedirs(".state", exist_ok=True)

    # 1) ASMX 서비스 설명 페이지 - 사용 가능한 메서드 목록이 나옴
    probe("서비스 메서드 목록", BASE, max_show=6000)

    # 2) Startup 을 파라미터 없이 / 빈 값으로 호출해보기
    probe("Startup (파라미터 없음)", BASE + "/Startup")
    probe("Startup (info 빈값)", BASE + "/Startup?info=%22%22")

    # 3) 흔히 쓰이는 이름들 찔러보기
    for m in ["GetNews", "News", "GetLatestNews", "LatestNews", "GetHeadlines"]:
        probe("메서드 시도: " + m, "%s/%s" % (BASE, m))

    # 4) 사이트 전체 자바스크립트에서 API 호출부 찾기 (잘리지 않은 원본)
    log("\n===== fj.js 에서 API 관련 부분 검색 =====")
    try:
        req = urllib.request.Request(
            "https://www.financialjuice.com/assets/js/fj.js?v=2.8.9",
            headers={"User-Agent": UA},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            js = r.read().decode("utf-8", "replace")
        log("fj.js 길이:", len(js))
        import re
        for kw in ["asmx", "Startup", "webpubsub", "negotiate", "Breaking", "news-", "info="]:
            hits = [m.start() for m in re.finditer(re.escape(kw), js)][:4]
            log("\n--- '%s' %d곳 ---" % (kw, len(hits)))
            for h in hits:
                log(repr(js[max(0, h - 200):h + 250]))
    except Exception as e:
        log("fj.js 가져오기 실패:", repr(e)[:200])

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines))
    log("\n탐사 종료")


if __name__ == "__main__":
    main()
