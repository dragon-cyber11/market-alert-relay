# FinancialJuice 웹소켓(SignalR) 탐사용 스크립트
# - 사이트가 실제로 쓰는 실시간 웹소켓에 익명으로 접속해서
#   지정한 시간 동안 들어오는 뉴스 메시지를 전부 .state/fj_ws_sample.jsonl 에 기록함
# - 여기서 Breaking / Level 필드가 실제로 어떻게 오는지 확인한 뒤,
#   본 릴레이를 RSS -> 웹소켓 방식으로 바꿀지 결정하는 용도 (텔레그램 전송 안 함)
import json
import os
import ssl
import sys
import time
import urllib.request
import urllib.parse

CAPTURE_SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 else 600  # 기본 10분
OUT_FILE = ".state/fj_ws_sample.jsonl"

CONNECTION_DATA = json.dumps([{"name": "newshub"}])
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def log(*a):
    print(*a, flush=True)


def http_get_json(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def short(d):
    # 로그용: 긴 토큰 값은 앞부분만 표시
    return {k: (v[:40] + "..." if isinstance(v, str) and len(v) > 40 else v) for k, v in d.items()}


def main():
    os.makedirs(".state", exist_ok=True)

    # 1단계: financialjuice.com 에서 negotiate -> Azure SignalR 리다이렉트 정보 + 토큰 받기
    qs = urllib.parse.urlencode({"clientProtocol": "1.5", "connectionData": CONNECTION_DATA})
    neg1 = http_get_json("https://www.financialjuice.com/signalr/negotiate?" + qs)
    log("negotiate(1):", short(neg1))

    redirect_url = neg1.get("RedirectUrl")
    access_token = neg1.get("AccessToken")
    if redirect_url:
        auth_headers = {"Authorization": "Bearer " + access_token}
        # 2단계: Azure SignalR 쪽에서 다시 negotiate -> ConnectionToken 받기
        neg2 = http_get_json(redirect_url + "/negotiate?" + qs, headers=auth_headers)
    else:
        # 리다이렉트가 없으면 자체 서버에 바로 붙는 구형 구조
        redirect_url = "https://www.financialjuice.com/signalr"
        access_token = None
        auth_headers = {}
        neg2 = neg1
    log("negotiate(2):", short(neg2))

    conn_token = neg2["ConnectionToken"]

    # 3단계: 웹소켓 접속
    import websocket  # websocket-client (워크플로에서 pip 설치)

    ws_base = redirect_url.replace("https://", "wss://").replace("http://", "ws://")
    ws_params = {
        "transport": "webSockets",
        "clientProtocol": "1.5",
        "connectionToken": conn_token,
        "connectionData": CONNECTION_DATA,
        "tid": "7",
    }
    if access_token:
        # 헤더를 못 쓰는 클라이언트를 위해 쿼리로도 토큰을 받는 서버가 많아서 둘 다 넣음
        ws_params["access_token"] = access_token
    ws_url = ws_base + "/connect?" + urllib.parse.urlencode(ws_params)
    log("connecting:", ws_url[:80] + "...")

    header = ["Authorization: Bearer " + access_token] if access_token else []
    ws = websocket.create_connection(
        ws_url, header=header, timeout=30,
        sslopt={"cert_reqs": ssl.CERT_REQUIRED},
        origin="https://www.financialjuice.com",
    )
    log("웹소켓 연결 성공")

    # 4단계: start 호출 (SignalR 핸드셰이크 마무리, 실패해도 수신은 되는 경우가 많음)
    try:
        start_qs = urllib.parse.urlencode({
            "transport": "webSockets",
            "clientProtocol": "1.5",
            "connectionToken": conn_token,
            "connectionData": CONNECTION_DATA,
        })
        start = http_get_json(redirect_url + "/start?" + start_qs, headers=auth_headers)
        log("start:", start)
    except Exception as e:
        log("start 호출 실패(치명적이지 않을 수 있음):", e)

    # 5단계: 수신 루프 - 들어오는 메시지를 전부 기록
    end = time.time() + CAPTURE_SECONDS
    count = 0
    with open(OUT_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"_probe_started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}) + "\n")
        f.flush()
        while time.time() < end:
            try:
                ws.settimeout(min(30, max(1, end - time.time())))
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except Exception as e:
                log("수신 오류, 5초 후 계속:", e)
                time.sleep(5)
                continue
            if not raw or raw == "{}":
                continue  # keep-alive 는 기록 안 함
            count += 1
            text = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
            f.write(text + "\n")
            f.flush()
            log("메시지 수신 #%d (길이 %d): %s" % (count, len(text), text[:300]))

    ws.close()
    log("수집 종료: 총 %d개 메시지를 %s 에 기록" % (count, OUT_FILE))


if __name__ == "__main__":
    main()
