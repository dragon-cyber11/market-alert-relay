# FinancialJuice 탐사 v3 (네트워크 + DOM)
# - 헤드리스 크롬으로 사이트를 열고
#   (1) 페이지가 주고받는 모든 네트워크 요청 목록 (뉴스 데이터를 어디서 가져오는지 찾기)
#   (2) JSON 응답 본문 (뉴스 데이터에 Breaking/빨간색 정보가 있는지 확인)
#   (3) 화면에 그려진 뉴스 목록의 HTML 구조 (빨간 뉴스에 어떤 표시가 붙는지 확인)
#   를 전부 기록함
# - 텔레그램 전송 안 함. 결과는 .state/ 아래 파일로 저장
import json
import os
import re
import sys
import time

CAPTURE_SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 else 120
NET_FILE = ".state/fj_net.jsonl"
HTML_FILE = ".state/fj_page.html"
ROWS_FILE = ".state/fj_rows.txt"
MAX_BODY = 8000

SKIP_PAT = re.compile(
    r"(google-analytics|googletagmanager|doubleclick|facebook|hotjar|"
    r"tradingview|\.png|\.jpg|\.jpeg|\.gif|\.svg|\.woff|\.ttf|\.css)",
    re.IGNORECASE,
)


def log(*a):
    print(*a, flush=True)


def main():
    os.makedirs(".state", exist_ok=True)
    from playwright.sync_api import sync_playwright

    net = open(NET_FILE, "w", encoding="utf-8")

    def rec(obj):
        net.write(json.dumps(obj, ensure_ascii=False) + "\n")
        net.flush()

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )

        def on_response(resp):
            url = resp.url
            if SKIP_PAT.search(url):
                return
            ctype = (resp.headers or {}).get("content-type", "")
            entry = {"url": url[:300], "status": resp.status, "ctype": ctype[:60]}
            # JSON 이나 텍스트 응답이면 본문 앞부분도 기록 (뉴스 데이터 찾기용)
            if "json" in ctype or "javascript" in ctype or "xml" in ctype:
                try:
                    body = resp.text()
                    if body and len(body.strip()) > 2:
                        entry["body"] = body[:MAX_BODY]
                except Exception as e:
                    entry["body_err"] = str(e)[:100]
            rec(entry)
            log("[net] %s %s %s" % (resp.status, ctype[:30], url[:120]))

        page.on("response", on_response)
        page.on("websocket", lambda ws: (rec({"ws_open": ws.url[:300]}), log("[ws] " + ws.url[:150])))

        log("페이지 로딩 중...")
        page.goto("https://www.financialjuice.com/home", timeout=90000, wait_until="domcontentloaded")
        log("로딩 완료, %d초 동안 네트워크 관찰" % CAPTURE_SECONDS)
        page.wait_for_timeout(CAPTURE_SECONDS * 1000)

        # 렌더링된 전체 HTML 저장 (빨간 뉴스에 붙는 class/style 확인용)
        html = page.content()
        with open(HTML_FILE, "w", encoding="utf-8") as f:
            f.write(html[:3000000])
        log("HTML 저장: %d자" % len(html))

        # 뉴스 줄로 보이는 요소들의 class/style/텍스트 추출
        rows = page.evaluate("""() => {
            const out = [];
            const links = Array.from(document.querySelectorAll('a[href*="/News/"]')).slice(0, 60);
            for (const a of links) {
                let el = a, chain = [];
                for (let i = 0; i < 5 && el; i++) {
                    chain.push({
                        tag: el.tagName,
                        cls: el.className && el.className.toString ? el.className.toString().slice(0,200) : '',
                        style: el.getAttribute ? (el.getAttribute('style') || '').slice(0,200) : '',
                        bg: getComputedStyle(el).backgroundColor,
                        color: getComputedStyle(el).color,
                    });
                    el = el.parentElement;
                }
                out.push({
                    href: a.getAttribute('href'),
                    text: (a.innerText || '').trim().slice(0, 120),
                    chain: chain,
                });
            }
            return out;
        }""")

        with open(ROWS_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps(rows, ensure_ascii=False, indent=1))
        log("뉴스 줄 %d개 추출" % len(rows))

        browser.close()

    net.close()
    log("탐사 종료")


if __name__ == "__main__":
    main()
