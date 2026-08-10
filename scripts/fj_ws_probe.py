# 파이낸셜주스 경제일정(GetCalendar) 구조 확인용 탐사 스크립트
#
# 인베스팅 위젯이 GitHub 서버 IP를 403으로 막아서, 이미 뚫려 있는 파이낸셜주스 API의
# 경제일정 기능을 쓰기로 함. 사이트 HTML에서 일정 항목에 imp-1/imp-2/imp-3 (중요도)
# 표시가 있는 걸 확인했으므로, 별 2개 이상만 고르는 것도 가능할 것으로 봄.
#
# 하는 일:
#   1) 홈페이지에서 info(인증값) 추출
#   2) GetCalendar / GetCalendarFilters 를 파라미터 자동 탐색으로 호출 성공시키기
#   3) 응답 구조를 그대로 기록 (중요도 필드가 어떤 이름/값인지 확인)
#   4) Startup 응답의 Cal 필드도 같이 확인
#
# 텔레그램 전송 안 함. 결과는 .state/inv_probe.txt
import gzip
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

OUT_TXT = ".state/inv_probe.txt"
HOME_URL = "https://www.financialjuice.com/home"
BASE = "https://live.financialjuice.com/FJService.asmx"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# 파라미터 값을 모를 때 순서대로 시도해볼 후보들
CANDIDATES = ['""', "0", "false", "1", '"0"', "-1", '"en"']

lines = []


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    lines.append(s)


def http_get(url, timeout=40):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://www.financialjuice.com/",
        "Accept-Encoding": "gzip",
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


def get_info():
    st, html = http_get(HOME_URL)
    m = re.search(r"var\s+info\s*=\s*'([^']+)'", html)
    if not m:
        log("info 추출 실패 (HTTP %s)" % st)
        return None
    log("info 추출 완료 (길이 %d)" % len(m.group(1)))
    return m.group(1)


def unwrap(body):
    m = re.search(r"<string[^>]*>(.*)</string>", body, re.DOTALL)
    inner = m.group(1) if m else body
    return (inner.replace("&lt;", "<").replace("&gt;", ">")
                 .replace("&quot;", '"').replace("&amp;", "&"))


def call(method, params):
    url = BASE + "/" + method
    if params:
        url += "?" + "&".join("%s=%s" % (k, urllib.parse.quote(v, safe=""))
                              for k, v in params.items())
    return http_get(url)


def discover(method, seed):
    """서버가 알려주는 'Missing parameter: XXX' 를 이용해 필요한 값을 자동으로 채움"""
    log("\n\n########## %s 파라미터 자동 탐색 ##########" % method)
    params = dict(seed)
    tried = {}

    for step in range(40):
        status, body = call(method, params)
        short = body.strip()[:160].replace("\n", " ")
        log("[%02d] %s -> HTTP %s | %s" % (step, list(params), status, short))

        if status == 200:
            log("\n>>> 성공! 응답 길이 %d" % len(body))
            return params, body

        m = re.search(r"Missing parameter:\s*([A-Za-z_0-9]+)", body)
        if m:
            name = m.group(1)
            tried[name] = 0
            params[name] = CANDIDATES[0]
            continue

        target = None
        for name in reversed(list(params.keys())):
            if name in body:
                target = name
                break
        if target is None and params:
            target = list(params.keys())[-1]
        if target is None:
            log(">>> 더 진행 못 함")
            return None, body

        idx = tried.get(target, 0) + 1
        if idx >= len(CANDIDATES):
            log(">>> '%s' 후보값 소진" % target)
            return None, body
        tried[target] = idx
        params[target] = CANDIDATES[idx]

    return None, None


def show_calendar(body):
    """응답에서 일정 항목과 중요도 필드를 찾아 보여줌"""
    inner = unwrap(body)
    log("\n--- 응답 앞부분(원본) ---")
    log(inner[:1500])

    try:
        data = json.loads(inner)
    except Exception:
        log("\n(JSON 아님 - HTML 조각일 수 있음)")
        # HTML 이면 중요도 클래스 확인
        imps = re.findall(r'class="[^"]*imp-(\d)[^"]*"', inner)
        if imps:
            from collections import Counter
            log("중요도 클래스 분포: %s" % dict(Counter(imps)))
        rows = re.findall(r"<div[^>]*div-table-row[^>]*>.*?</div>\s*</div>", inner, re.DOTALL)
        log("행 후보 %d개" % len(rows))
        for r in rows[:5]:
            txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r)).strip()
            log("  " + txt[:200])
        return

    # JSON 인 경우
    log("\n최상위 키: %s" % (list(data) if isinstance(data, dict) else type(data)))
    for k in (data if isinstance(data, dict) else {}):
        v = data[k]
        if isinstance(v, list) and v:
            log("\n--- %s : %d건, 첫 항목 전체 필드 ---" % (k, len(v)))
            log(json.dumps(v[0], ensure_ascii=False)[:1200])
            imp_keys = [kk for kk in (v[0] if isinstance(v[0], dict) else {})
                        if "imp" in kk.lower()]
            if imp_keys:
                from collections import Counter
                for ik in imp_keys:
                    log("  중요도 필드 '%s' 값 분포: %s"
                        % (ik, dict(Counter(str(x.get(ik)) for x in v if isinstance(x, dict)))))
            log("\n  앞쪽 8건 요약:")
            for x in v[:8]:
                if isinstance(x, dict):
                    log("    " + json.dumps(
                        {kk: x[kk] for kk in list(x)[:8]}, ensure_ascii=False)[:200])


def main():
    os.makedirs(".state", exist_ok=True)
    info = get_info()
    if not info:
        with open(OUT_TXT, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return

    seed = {"info": '"%s"' % info, "TimeOffset": "0"}

    for method in ["GetCalendar", "GetCalendarFilters"]:
        params, body = discover(method, seed)
        if params and body:
            log("\n성공한 파라미터: %s" % {k: (v[:20] + "..." if len(v) > 20 else v)
                                          for k, v in params.items()})
            show_calendar(body)

    # Startup 응답의 Cal 필드도 확인
    log("\n\n########## Startup 의 Cal 필드 ##########")
    st, body = call("Startup", {
        "info": '"%s"' % info, "TimeOffset": "0", "tabID": "0", "oldID": "0",
        "TickerID": "0", "FeedCompanyID": "0", "strSearch": '""', "extraNID": "0"})
    if st == 200:
        try:
            data = json.loads(unwrap(body))
            for k in ("Cal", "CalFil", "CEvents"):
                v = data.get(k)
                log("%s: %s" % (k, (json.dumps(v, ensure_ascii=False)[:600]
                                    if v else "비어있음")))
        except Exception as e:
            log("파싱 실패: %s" % e)

    with open(OUT_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log("\n탐사 종료")


if __name__ == "__main__":
    main()
