# FinancialJuice 탐사 v5 (뉴스 API 파라미터 자동 발견)
# - 서버가 "Missing parameter: XXX" 라고 알려주는 걸 이용해서
#   필요한 파라미터를 하나씩 자동으로 채워가며 호출 성공시키기
# - 목표: Breaking / Level (빨간 뉴스 표시)이 담긴 뉴스 JSON을 우리가 직접 받아오기
# - 텔레그램 전송 안 함. 결과는 .state/fj_api_probe.txt 에 저장
import os
import re
import urllib.request
import urllib.error
import urllib.parse

OUT_FILE = ".state/fj_api_probe.txt"
BASE = "https://live.financialjuice.com/FJService.asmx"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# 파라미터 값을 모를 때 순서대로 시도해볼 후보들
CANDIDATES = ['""', "0", "false", "null", '"0"', "-1", '"en"']

out_lines = []


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    out_lines.append(s)


def call(method, params):
    url = "%s/%s" % (BASE, method)
    if params:
        url += "?" + "&".join("%s=%s" % (k, urllib.parse.quote(v, safe="")) for k, v in params.items())
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://www.financialjuice.com/",
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return -1, repr(e)[:200]


def discover(method, seed=None):
    """서버 에러 메시지를 보고 필요한 파라미터를 자동으로 채워가며 호출 성공을 시도"""
    log("\n\n############ %s 파라미터 자동 탐색 ############" % method)
    params = dict(seed or {})
    tried = {}  # 파라미터별로 몇 번째 후보까지 써봤는지

    for step in range(40):
        status, body = call(method, params)
        short = body.strip()[:200].replace("\n", " ")
        log("[%02d] params=%s -> HTTP %s | %s" % (step, params, status, short))

        if status == 200:
            log("\n>>> 성공! 응답 길이 %d" % len(body))
            log(body[:5000])
            return params, body

        m = re.search(r"Missing parameter:\s*([A-Za-z_0-9]+)", body)
        if m:
            name = m.group(1)
            tried[name] = 0
            params[name] = CANDIDATES[0]
            continue

        # 값 타입이 안 맞는 경우: 문제가 된 파라미터의 다음 후보값으로 교체
        m2 = re.search(r"(?:parameter|argument|value)[^A-Za-z]*([A-Za-z_0-9]+)", body)
        target = None
        for name in reversed(list(params.keys())):
            if name in body:
                target = name
                break
        if target is None and m2:
            target = m2.group(1) if m2.group(1) in params else None
        if target is None and params:
            target = list(params.keys())[-1]

        if target is None:
            log(">>> 더 진행 못 함 (파라미터 단서 없음)")
            return None, body

        idx = tried.get(target, 0) + 1
        if idx >= len(CANDIDATES):
            log(">>> '%s' 후보값 다 써봤지만 실패" % target)
            return None, body
        tried[target] = idx
        params[target] = CANDIDATES[idx]

    log(">>> 40회 시도 후 종료")
    return None, None


def fetch_signature(method):
    """ASMX 문서 페이지에서 파라미터 이름/타입 확인"""
    log("\n===== %s 시그니처 =====" % method)
    status, body = call_raw("%s?op=%s" % (BASE, method))
    if status != 200:
        log("문서 페이지 실패:", status)
        return
    # HTTP GET 예시 블록에서 파라미터 이름 뽑기
    m = re.search(r"HTTP GET.*?</pre>", body, re.DOTALL)
    block = m.group(0) if m else body
    names = re.findall(r"([A-Za-z_0-9]+)=string|([A-Za-z_0-9]+)=", block[:3000])
    log("문서에서 찾은 파라미터 후보:", sorted(set([a or b for a, b in names]))[:20])
    # 파라미터 표 부분도 일부 출력
    tbl = re.search(r"<h3>.*?Parameters.*?</table>", body, re.DOTALL)
    if tbl:
        txt = re.sub(r"<[^>]+>", " ", tbl.group(0))
        log(re.sub(r"\s+", " ", txt)[:600])


def call_raw(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return -1, repr(e)[:200]


def main():
    os.makedirs(".state", exist_ok=True)

    for m in ["Startup", "GetPreviousNews", "GetNewsSummary"]:
        fetch_signature(m)

    discover("Startup")
    discover("GetPreviousNews")
    discover("GetNewsSummary")

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines))
    log("\n탐사 종료")


if __name__ == "__main__":
    main()
