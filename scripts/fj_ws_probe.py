# FinancialJuice 웹소켓 탐사 v2 (브라우저 방식)
# - 헤드리스 크롬으로 financialjuice.com/home 을 실제로 열고,
#   브라우저가 웹소켓으로 주고받는 모든 데이터(접속 주소 포함)를 기록함
# - 브라우저가 서버에 뭘 보내는지 알아내서, 그걸 파이썬으로 그대로 재현하기 위한 용도
# - 텔레그램 전송 안 함. 결과는 .state/fj_ws_sample.jsonl 에 기록
import json
import os
import sys
import time

CAPTURE_SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 else 480  # 기본 8분
OUT_FILE = ".state/fj_ws_sample.jsonl"
MAX_FRAME_CHARS = 20000  # 너무 큰 프레임은 앞부분만 기록


def log(*a):
    print(*a, flush=True)


def main():
    os.makedirs(".state", exist_ok=True)
    from playwright.sync_api import sync_playwright

    out = open(OUT_FILE, "a", encoding="utf-8")

    def rec(kind, data):
        text = data if isinstance(data, str) else repr(data)
        if len(text) > MAX_FRAME_CHARS:
            text = text[:MAX_FRAME_CHARS] + "...(잘림)"
        out.write(json.dumps({
            "t": time.strftime("%H:%M:%S", time.gmtime()),
            "kind": kind,
            "data": text,
        }, ensure_ascii=False) + "\n")
        out.flush()
        log("[%s] %s" % (kind, text[:200]))

    def payload_of(x):
        # playwright 버전에 따라 payload가 dict로 오기도 해서 둘 다 처리
        if isinstance(x, dict):
            return x.get("payload", "")
        return x

    def on_ws(ws):
        rec("ws_open", ws.url)
        ws.on("framesent", lambda p: rec("sent", payload_of(p)))
        ws.on("framereceived", lambda p: (
            None if payload_of(p) in ("{}", "") else rec("recv", payload_of(p))
        ))
        ws.on("close", lambda w=None: rec("ws_close", ws.url))

    rec("probe_started", "v2 browser probe, capture %ds" % CAPTURE_SECONDS)

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )
        page.on("websocket", on_ws)

        log("페이지 로딩 중...")
        page.goto("https://www.financialjuice.com/home", timeout=90000, wait_until="domcontentloaded")
        log("페이지 로딩 완료, %d초 동안 웹소켓 데이터 수집" % CAPTURE_SECONDS)

        # 수집 시간 동안 대기 (그동안 이벤트 핸들러가 알아서 기록)
        remaining = CAPTURE_SECONDS
        while remaining > 0:
            step = min(30, remaining)
            page.wait_for_timeout(step * 1000)
            remaining -= step
            log("... 수집 중 (남은 시간 %d초)" % remaining)

        browser.close()

    rec("probe_finished", "done")
    out.close()
    log("수집 종료")


if __name__ == "__main__":
    main()
