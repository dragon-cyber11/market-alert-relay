# FinancialJuice -> 텔레그램 릴레이
#
# RSS 대신 사이트 본체가 쓰는 뉴스 API를 직접 호출함.
# 이유: RSS에는 제목/시간밖에 없지만, 이 API에는 사이트가 헤드라인을 빨간색으로
#       칠할 때 쓰는 Level 값이 같이 들어있어서 "진짜 중요한 속보"를 그대로 구분할 수 있음.
#       (사이트에서 빨갛게 표시되는 뉴스 = Level 에 'active-critical' 포함)
import gzip
import json
import os
import re
import time
import urllib.error
import urllib.request
import urllib.parse
from datetime import datetime, timezone

HOME_URL = "https://www.financialjuice.com/home"
API_URL = "https://live.financialjuice.com/FJService.asmx/Startup"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

CHAT_ID = "@haesunking"

STATE_DIR = ".state"
LAST_ID_FILE = os.path.join(STATE_DIR, "fj_last_newsid.txt")
HEARTBEAT_FILE = os.path.join(STATE_DIR, "fj_heartbeat.txt")
SENT_TITLES_FILE = os.path.join(STATE_DIR, "fj_sent_titles.json")
INFO_CACHE_FILE = os.path.join(STATE_DIR, "fj_info_cache.txt")  # 커밋 안 함(임시 캐시)
FAIL_FILE = os.path.join(STATE_DIR, "fj_fail.json")             # 커밋 안 함(장애 감시용)

SENT_TITLES_TTL_SECONDS = 24 * 3600   # 같은 제목은 24시간 안에는 다시 안 보냄
SENT_TITLES_MAX = 500                 # 상태 파일이 무한정 커지지 않도록 제한
INFO_TTL_SECONDS = 900                # info 값은 15분마다 새로 받아옴

# API는 한 번에 20건만 주기 때문에, 잡이 잠깐 끊겼다 돌아오면 그 사이 뉴스를 놓칠 수 있음.
# 그래서 마지막으로 보낸 뉴스에 닿을 때까지 과거 페이지를 되짚어 채워넣음(최대 10페이지 = 200건).
MAX_BACKFILL_PAGES = 10
# 한 주기에 보내는 최대 건수. 넘치면 나머지는 다음 주기에 이어서 보냄(누락 없음).
MAX_PER_CYCLE = 18

# 텔레그램은 한 채널에 분당 20건까지만 허용함. 3.5초 간격이면 분당 17건이라 안전 마진이 생김.
# (주기가 연달아 붙을 때 순간적으로 20건을 넘기지 않도록 딱 3.0초가 아니라 여유를 둠)
SEND_INTERVAL_SECONDS = 3.5
# 텔레그램 메시지는 4096자 제한. 트럼프 게시글 전문처럼 1000자 넘는 제목이 실제로 들어오는데,
# 번역문까지 붙으면 제한을 넘겨서 그 뉴스가 통째로 거부당함. 그래서 제목을 잘라서 보냄.
MAX_TITLE_CHARS = 1200
# 뉴스를 이 시간 이상 계속 못 가져오면 텔레그램으로 장애 알림을 한 번 보냄
FAIL_ALERT_AFTER_SECONDS = 300


def normalize_title(text):
    return re.sub(r"\s+", " ", text).strip().lower()


def clip(text, limit=MAX_TITLE_CHARS):
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


# 사이트가 빨간색으로 강조하는 뉴스의 표식.
# Level 예시: '' (일반), 'news-general' (일반 기사), 'active' (강조), 'active active-critical' (빨간색)
def is_critical(level):
    return "active-critical" in (level or "").lower()


# 금융 매체라 대부분 관련 있는 뉴스지만, 가끔 섞여 들어오는 이런 건 걸러냄
NOISE_KEYWORDS = [
    # 민방위/공습경보 (반복되는 정기 경보 문구는 시장과 무관)
    "civil defence", "civil defense", "early warning system",
    "national early warning", "air raid siren", "danger has passed", "danger over",
    "early alert issued",
    # 날씨/자연재해 알림성
    "weather alert", "storm warning", "flood warning", "wildfire warning",
    "hurricane warning", "tornado warning", "heat advisory",
    # 스포츠
    "football match", "soccer match", "basketball game", "world cup", "olympic",
    "grand slam", "tournament final",
    # 부고/의전성
    "obituary", "dies at age", "passes away", "royal wedding", "coronation ceremony",
]
_NOISE_PATTERNS = [re.compile(re.escape(k), re.IGNORECASE) for k in NOISE_KEYWORDS]


def is_noise(title):
    return any(p.search(title) for p in _NOISE_PATTERNS)


def http_get(url, timeout=30):
    """압축(gzip)을 요청해서 받아옴. 뉴스 API 응답이 145KB쯤 되는데 압축하면 80~90% 줄어듦.
    서버가 압축을 안 해주면 그냥 원본 그대로 처리."""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://www.financialjuice.com/",
        "Accept-Encoding": "gzip",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
            try:
                raw = gzip.decompress(raw)
            except Exception as e:
                print("압축 해제 실패, 원본으로 처리:", e)
    return raw.decode("utf-8", "replace")


def get_info(force=False):
    """뉴스 API 호출에 필요한 info 값을 홈페이지에서 뽑아옴 (15분 캐시)"""
    if not force and os.path.exists(INFO_CACHE_FILE):
        try:
            with open(INFO_CACHE_FILE) as f:
                ts, cached = f.read().split("\n", 1)
            if time.time() - float(ts) < INFO_TTL_SECONDS and cached.strip():
                return cached.strip()
        except Exception:
            pass

    html = http_get(HOME_URL, timeout=40)
    m = re.search(r"var\s+info\s*=\s*'([^']+)'", html)
    if not m:
        raise RuntimeError("홈페이지에서 info 값을 찾지 못함")
    info = m.group(1)
    try:
        with open(INFO_CACHE_FILE, "w") as f:
            f.write("%f\n%s" % (time.time(), info))
    except Exception:
        pass
    return info


def fetch_news(info, old_id=0):
    """뉴스 API 호출 -> 뉴스 목록(list) 반환. old_id를 주면 그보다 과거 뉴스를 받아옴"""
    params = {
        "info": '"%s"' % info,
        "TimeOffset": "0",     # UTC 기준으로 받아서 아래에서 한국시간으로 변환
        "tabID": "0",          # 메인 피드 (사이트 첫 화면과 동일)
        "oldID": str(int(old_id)),
        "TickerID": "0",
        "FeedCompanyID": "0",
        "strSearch": '""',
        "extraNID": "0",
    }
    url = API_URL + "?" + "&".join(
        "%s=%s" % (k, urllib.parse.quote(v, safe="")) for k, v in params.items())
    body = http_get(url)

    # 응답이 <string xmlns="...">{JSON}</string> 형태라 안쪽만 꺼냄
    m = re.search(r"<string[^>]*>(.*)</string>", body, re.DOTALL)
    inner = m.group(1) if m else body
    inner = (inner.replace("&lt;", "<").replace("&gt;", ">")
                  .replace("&quot;", '"').replace("&amp;", "&"))
    data = json.loads(inner)
    return data.get("News") or []


# '2026-07-26T03:50:07.153' / '2026-07-25T21:39:19.36' / '2026-07-25T21:39:19' 전부 처리.
# (파이썬 3.11 미만의 fromisoformat 은 소수점 자릿수가 3 또는 6이 아니면 에러를 내기 때문에
#  소수점 이하를 아예 떼고 파싱함)
_DT_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})")


def parse_epoch(date_published, default=None):
    """뉴스의 발행 시각(UTC 문자열) -> epoch 초. 실패하면 default(기본: 현재 시각)"""
    m = _DT_RE.match((date_published or "").strip())
    if m:
        try:
            dt = datetime(*(int(x) for x in m.groups()), tzinfo=timezone.utc)
            return int(dt.timestamp())
        except Exception:
            pass
    return int(time.time()) if default is None else default


def load_sent_titles():
    if not os.path.exists(SENT_TITLES_FILE):
        return {}
    try:
        with open(SENT_TITLES_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_sent_titles(sent_titles, now_epoch):
    pruned = {t: e for t, e in sent_titles.items()
              if now_epoch - e < SENT_TITLES_TTL_SECONDS}
    if len(pruned) > SENT_TITLES_MAX:
        pruned = dict(sorted(pruned.items(), key=lambda kv: kv[1], reverse=True)[:SENT_TITLES_MAX])
    with open(SENT_TITLES_FILE, "w") as f:
        json.dump(pruned, f)


def read_last_id():
    try:
        with open(LAST_ID_FILE) as f:
            content = f.read().strip()
        return int(content) if content else 0
    except Exception:
        return 0


def write_last_id(value):
    with open(LAST_ID_FILE, "w") as f:
        f.write(str(int(value)))


# 가입/키 없이 쓸 수 있는 비공식 구글 번역 엔드포인트 (브라우저 구글 번역이 쓰는 것과 동일).
# 막히거나 실패하면 원문만 전송되고 봇은 계속 돎.
GOOGLE_UNOFFICIAL_URL = "https://translate.googleapis.com/translate_a/single"


def translate_to_ko(text):
    try:
        params = urllib.parse.urlencode(
            {"client": "gtx", "sl": "en", "tl": "ko", "dt": "t", "q": text})
        req = urllib.request.Request(
            "%s?%s" % (GOOGLE_UNOFFICIAL_URL, params),
            headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        translated = "".join(seg[0] for seg in data[0] if seg and seg[0])
        if translated:
            return translated.strip()
    except Exception as e:
        print("번역 실패:", e)
    return None


# 마지막 전송 결과를 하트비트에 남기기 위한 기록용 (뉴스가 안 올 때 원인 파악용)
LAST_SEND_STATUS = []


def send_telegram(bot_token, chat_id, msg, max_retry=2):
    """텔레그램 전송. (성공여부, 영구실패여부) 를 돌려줌.
    - 429(분당 20건 속도 제한)를 맞으면 서버가 알려준 시간만큼 쉬었다가 재시도
    - 400번대(메시지 길이 초과 등)는 재시도해도 소용없으므로 영구실패로 표시하고 건너뜀
    - 그 외(네트워크/서버 오류)는 일시적 실패로 보고, 호출한 쪽에서 다음 주기에 재시도"""
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": msg}).encode()
    url = "https://api.telegram.org/bot%s/sendMessage" % bot_token

    for _ in range(max_retry + 1):
        try:
            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req, timeout=20) as r:
                r.read()
            LAST_SEND_STATUS.append("ok")
            return True, False
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            if e.code == 429:
                wait = 5
                try:
                    wait = int(json.loads(body)["parameters"]["retry_after"])
                except Exception:
                    pass
                print("텔레그램 속도 제한, %d초 대기 후 재시도" % wait)
                time.sleep(min(wait + 1, 60))
                continue
            if e.code in (401, 403, 404):
                # 토큰이 틀렸거나 채널에 못 들어가는 상태. 메시지를 바꿔도 소용없고,
                # 건너뛰면 뉴스가 조용히 사라지므로 여기서 멈추고 다음 주기에 다시 시도함.
                print("텔레그램 인증/권한 오류(%s): %s" % (e.code, body))
                LAST_SEND_STATUS.append("auth_error_%s" % e.code)
                return False, False
            if 400 <= e.code < 500:
                # 메시지 자체 문제(길이 초과 등) -> 이 건만 건너뜀
                print("텔레그램이 거부함(%s): %s" % (e.code, body))
                LAST_SEND_STATUS.append("rejected_%s" % e.code)
                return False, True
            print("텔레그램 서버 오류(%s), 재시도" % e.code)
            time.sleep(2)
        except Exception as e:
            print("텔레그램 전송 오류:", e)
            LAST_SEND_STATUS.append("net_error")
            time.sleep(2)
    return False, False


# ---- 장애 감시: 뉴스를 계속 못 가져오면 텔레그램으로 한 번 알려주고, 복구되면 알려줌 ----
def load_fail():
    try:
        with open(FAIL_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_fail(state):
    try:
        with open(FAIL_FILE, "w") as f:
            json.dump(state, f)
    except Exception:
        pass


def note_failure(err):
    st = load_fail()
    now = int(time.time())
    st.setdefault("since", now)
    st["last_error"] = str(err)[:200]
    st["count"] = st.get("count", 0) + 1

    if not st.get("alerted") and now - st["since"] >= FAIL_ALERT_AFTER_SECONDS:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if token:
            send_telegram(token, CHAT_ID,
                          "⚠️ 뉴스 릴레이 오류\n%d분째 뉴스를 가져오지 못하고 있습니다.\n원인: %s"
                          % ((now - st["since"]) // 60, st["last_error"]))
        st["alerted"] = True
    save_fail(st)


def note_success():
    st = load_fail()
    if not st:
        return
    if st.get("alerted"):
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if token:
            send_telegram(token, CHAT_ID, "✅ 뉴스 릴레이 정상화")
    save_fail({})


def write_heartbeat(latest_id, count):
    """마지막 실행 시각 + 피드 상태 + 마지막 전송 결과를 한 줄로 기록.
    뉴스가 텔레그램에 안 올 때, 가져오기가 문제인지 보내기가 문제인지 여기서 구분됨."""
    if LAST_SEND_STATUS:
        ok = LAST_SEND_STATUS.count("ok")
        bad = [x for x in LAST_SEND_STATUS if x != "ok"]
        send = "send=%d건성공" % ok + (" 실패=%s" % ",".join(sorted(set(bad))) if bad else "")
    else:
        send = "send=보낼것없음"
    with open(HEARTBEAT_FILE, "w") as f:
        f.write("%s latest_newsid=%s count=%s %s\n" % (
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), latest_id, count, send))


def news_id_of(n):
    try:
        return int(n.get("NewsID") or 0)
    except Exception:
        return 0


def collect_items(info, last_id, do_backfill):
    """최신 페이지부터 받아오되, 필요하면 last_id 에 닿을 때까지 과거 페이지도 이어붙임"""
    items = fetch_news(info)
    if not items:
        # info 가 만료됐을 수 있으니 새로 받아서 한 번 더
        info = get_info(force=True)
        items = fetch_news(info)
    if not items or not do_backfill:
        return items

    by_id = {news_id_of(n): n for n in items if news_id_of(n) > 0}
    for _ in range(MAX_BACKFILL_PAGES):
        oldest = min(by_id)
        if oldest <= last_id:      # 이미 지난번 지점까지 다 덮었음
            break
        page = fetch_news(info, old_id=oldest)
        new_ones = {news_id_of(n): n for n in page if news_id_of(n) > 0}
        new_ones = {k: v for k, v in new_ones.items() if k not in by_id}
        if not new_ones:           # 더 과거가 없거나 같은 것만 옴
            break
        by_id.update(new_ones)
    return list(by_id.values())


def main():
    os.makedirs(STATE_DIR, exist_ok=True)

    info = get_info()
    first_run = not os.path.exists(LAST_ID_FILE)
    last_id = 0 if first_run else read_last_id()

    items = collect_items(info, last_id, do_backfill=not first_run)
    if not items:
        raise RuntimeError("뉴스 응답이 비어 있음")

    items = [n for n in items if news_id_of(n) > 0]
    items.sort(key=news_id_of)                 # 오래된 것 -> 최신
    latest_id = news_id_of(items[-1])

    write_heartbeat(latest_id, len(items))

    if first_run:
        write_last_id(latest_id)
        print("초기화: 기준 NewsID %s 저장 (이번 실행은 발송 안 함)" % latest_id)
        return

    candidates = [n for n in items if news_id_of(n) > last_id]
    if not candidates:
        print("새 속보 없음")
        write_last_id(max(last_id, latest_id))
        return

    # 한 주기 상한을 넘으면 오래된 것부터 처리하고, 기준점도 처리한 데까지만 올림.
    # (나머지는 다음 주기에 그대로 이어서 나가므로 누락되지 않음)
    batch = candidates[:MAX_PER_CYCLE]
    if len(candidates) > len(batch):
        print("이번 주기 %d건 처리, %d건은 다음 주기로" % (len(batch), len(candidates) - len(batch)))

    sent_titles = load_sent_titles()
    now_epoch = int(time.time())

    to_send = []
    for n in batch:
        title = clip(n.get("Title"))
        if not title:
            continue
        key = normalize_title(title)
        if key in sent_titles:      # 같은 기사가 시간만 바뀌어 다시 올라오는 경우 제외
            continue
        sent_titles[key] = now_epoch
        if is_noise(title):
            continue
        to_send.append({
            "nid": news_id_of(n),
            "key": key,
            "title": title,
            "epoch": parse_epoch(n.get("DatePublished")),
            "critical": is_critical(n.get("Level")),
        })

    if not to_send:
        print("새 속보 없음 (전부 이미 보냈거나 걸러짐)")
        save_sent_titles(sent_titles, now_epoch)
        write_last_id(news_id_of(batch[-1]))
        return

    bot_token = os.environ["TELEGRAM_BOT_TOKEN"]

    # 전송에 실패하면 기준점을 그 앞에서 멈춰서, 다음 주기에 그 뉴스부터 다시 시도함(유실 방지)
    last_ok_nid = last_id
    sent_count = 0

    for idx, item in enumerate(to_send):
        title = item["title"]
        translated = translate_to_ko(title)
        kst_time = time.strftime("%H:%M", time.gmtime(item["epoch"] + 9 * 3600))
        prefix = "\U0001F6A8 " if item["critical"] else ""

        if translated and translated.lower() != title.lower():
            msg = "%s%s\n(원문: %s)\n(%s)" % (prefix, clip(translated), title, kst_time)
        else:
            msg = "%s%s\n(%s)" % (prefix, title, kst_time)

        ok, permanent = send_telegram(bot_token, CHAT_ID, msg)
        if ok:
            sent_count += 1
            last_ok_nid = item["nid"]
            print("전송%s 완료" % (" [중요]" if item["critical"] else ""))
        elif permanent:
            # 이 메시지 자체가 문제라 다시 보내도 안 됨 -> 건너뛰고 진행
            print("건너뜀:", title[:60])
            last_ok_nid = item["nid"]
        else:
            # 일시적 실패 -> 여기서 멈추고, 아직 못 보낸 것들은 기록에서 지워 다음 주기에 재시도
            for rest in to_send[idx:]:
                sent_titles.pop(rest["key"], None)
            print("일시적 전송 실패, 다음 주기에 이어서 재시도")
            break

        # 마지막 메시지 뒤에는 쉬지 않음 (뉴스가 1건일 때 괜히 기다리지 않도록)
        if idx < len(to_send) - 1:
            time.sleep(SEND_INTERVAL_SECONDS)

    save_sent_titles(sent_titles, now_epoch)
    write_last_id(last_ok_nid)
    write_heartbeat(latest_id, len(items))   # 전송 결과까지 반영해서 다시 기록
    print("%s건 전송 완료, 기준 NewsID: %s" % (sent_count, last_ok_nid))


if __name__ == "__main__":
    try:
        main()
        note_success()
    except Exception as e:
        print("실행 오류:", repr(e)[:300])
        note_failure(e)
        raise SystemExit(1)
