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
import sys
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

# 새 뉴스 확인 간격(초). 프로세스를 계속 살려두고 안에서 도는 구조라 짧게 잡아도
# 파이썬 시작 비용이 다시 들지 않음.
# 1초 = 분당 60회. 이보다 더 줄이면 파이낸셜주스 앞단(Cloudflare)이 과한 요청으로 보고
# 차단할 위험이 커져서, 코드로 낼 수 있는 실질적 한계로 잡음.
POLL_INTERVAL_SECONDS = 1


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
    # 파이낸셜주스 정기 자동 게시물 (본문 없이 반복되는 요약)
    #   "Currency Strength Chart: Strongest: ... - Weakest" 처럼 통화 강도 순위만 나열
    "currency strength",
]
_NOISE_PATTERNS = [re.compile(re.escape(k), re.IGNORECASE) for k in NOISE_KEYWORDS]

# 본문 없이 제목만 오는 반복성 잡음. 정규식으로 단어 경계를 써서 정상 뉴스 오탐을 막는다.
#   MOC/MOO Imbalance : 장 마감/개장 주문 불균형. 실제 수치는 사이트 본문에 있어
#                       제목("MOC Imbalance")만 텔레그램으로 오면 알맹이가 없다.
#                       단순 키워드 "MOC" 로 넣으면 "Mocha port" 같은 정상 뉴스가
#                       걸리므로 뒤에 imbalance 가 붙은 경우만 \b 로 잡는다.
NOISE_REGEXES = [
    re.compile(r"\bMO[OC]\s+imbalance\b", re.IGNORECASE),
]


def is_noise(title):
    return (any(p.search(title) for p in _NOISE_PATTERNS)
            or any(p.search(title) for p in NOISE_REGEXES))


# 파이낸셜주스가 정기적으로 올리는 자체 게시글. 제목만 오고 본문은 사이트 안쪽에 있어서
# 텔레그램으로 받아봐야 알맹이가 없음.
#   - Morning Juice - Europe/US Session Prep  (하루 두 번, 세션 준비 글)
#   - Week Ahead: Economic Indicators         (주간 일정 안내)
#   - ... Market Wrap                         (장 마감 요약)
OWN_POST_PATTERNS = [
    re.compile(r"morning juice", re.IGNORECASE),
    re.compile(r"week ahead", re.IGNORECASE),
    re.compile(r"market wrap", re.IGNORECASE),
]


def is_link_only_post(n):
    """본문 없이 제목만 오는 파이낸셜주스 자체 게시글인지 판별.

    두 가지 방법으로 잡아냄:
      1) 제목이 자체 게시글 형식 (Morning Juice / Week Ahead / Market Wrap)
         - 마켓랩은 외부 매체 이름이 붙어 오는 경우가 있어서 구조만으로는 못 잡음
      2) 구조상 링크성 글인데 출처 이름이 비어 있음 (HasE=True + FCName 없음)
         - 파이낸셜주스가 직접 쓴 글이라는 뜻

    CNBC/FXStreet 같은 외부 언론 기사는 출처 이름이 붙어 있고 제목 자체에 정보가
    있으므로 통과시킴.
    """
    title = n.get("Title") or ""
    if any(p.search(title) for p in OWN_POST_PATTERNS):
        return True
    if not n.get("HasE"):
        return False
    return not (n.get("FCName") or "").strip()


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


def atomic_write(path, text):
    """임시 파일에 다 쓴 뒤 한 번에 바꿔치기.

    잡이 시간 초과나 취소로 도중에 죽어도 상태 파일이 '반쯤 쓰인 상태'로 남지 않게 함.
    잘린 NewsID(예: 3421 -> 34)가 그대로 커밋되면, 다음 잡이 그 지점부터 되짚어서
    이미 보낸 뉴스를 최대 200건까지 다시 보내는 사고가 남."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


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
    atomic_write(SENT_TITLES_FILE, json.dumps(pruned))


def read_last_id():
    try:
        with open(LAST_ID_FILE) as f:
            content = f.read().strip()
        return int(content) if content else 0
    except Exception:
        return 0


def write_last_id(value):
    atomic_write(LAST_ID_FILE, str(int(value)))


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

# 🚨 를 움직이는 커스텀 이모지로 보내는 기능(현재 비활성 - 기본값 비어 있음).
#   [확인된 사실] 텔레그램은 봇이 custom_emoji 를 보내려면 Fragment 에서 사용자명을
#   산 봇이어야 한다. 자격이 없으면 400 이 아니라 200(ok)을 주면서 엔티티를 조용히
#   제거해 버린다(응답 entities=null 로 실측 확인). 즉 채널 부스트·이모지·ID 가 전부
#   정상이어도 봇 자격이 없으면 평범한 🚨 로만 나간다. 이 봇은 자격이 없어 비워 둔다.
#   봇이 Fragment 사용자명을 갖게 되면 SIREN_CUSTOM_EMOJI_ID 환경변수에 custom_emoji_id
#   를 넣으면 곧바로 동작한다. (전송 로직/폴백은 그대로 두어 언제든 재활성 가능)
SIREN_CHAR = "\U0001F6A8"
SIREN_CUSTOM_EMOJI_ID = os.environ.get("SIREN_CUSTOM_EMOJI_ID", "").strip()
_SIREN_DISABLED = False    # 이 프로세스에서 커스텀 이모지가 거부된 적이 있으면 True


def _siren_entities(msg):
    """메시지에 🚨 가 있으면 그 자리를 움직이는 커스텀 이모지로 바꾸는
    entities(JSON 문자열)를 만든다. 없거나 이미 거부된 적이 있으면 None.
    오프셋/길이는 텔레그램 규격대로 UTF-16 코드 단위로 센다(🚨 는 2 단위)."""
    if not SIREN_CUSTOM_EMOJI_ID or _SIREN_DISABLED:
        return None
    idx = msg.find(SIREN_CHAR)
    if idx < 0:
        return None
    offset = len(msg[:idx].encode("utf-16-le")) // 2
    length = len(SIREN_CHAR.encode("utf-16-le")) // 2
    return json.dumps([{
        "type": "custom_emoji", "offset": offset, "length": length,
        "custom_emoji_id": SIREN_CUSTOM_EMOJI_ID,
    }])


def send_telegram(bot_token, chat_id, msg, max_retry=2):
    """텔레그램 전송. (성공여부, 더이상재시도안함) 을 돌려줌.

    [핵심 원칙] 텔레그램에서 '응답을 받았을 때만' 전송 여부를 판단한다.
    응답을 못 받은 경우(타임아웃, 연결 끊김)는 실제로는 이미 전달됐을 수 있다.
    이때 재시도하면 같은 뉴스가 계속 다시 나가서, 5초 주기로 같은 메시지가
    수십 번 올라가는 사고가 난다(실제로 발생했음). 그래서 이 경우엔 재시도하지 않고
    '보낸 것으로' 간주하고 넘어간다. 가끔 하나 놓치는 편이 중복 폭탄보다 낫다.

    - 응답 받음 + 성공        -> (True,  False)  정상
    - 429 속도 제한           -> 서버가 알려준 만큼 쉬었다 재시도 (확실히 미전송이라 안전)
    - 401/403/404 인증/권한   -> (False, False)  확실히 미전송. 다음 주기에 재시도
    - 그 외 400번대           -> (False, True)   메시지 자체 문제. 이 건만 건너뜀
    - 500번대                 -> 한 번 재시도 후 (False, False). 서버가 거절한 것이라 안전
    - 응답 없음(타임아웃 등)  -> (False, True)   전달 여부 불명. 재시도 안 함(중복 방지)
    """
    url = "https://api.telegram.org/bot%s/sendMessage" % bot_token
    entities = _siren_entities(msg)   # None 이면 평범한 전송과 완전히 동일

    def build(with_entities):
        fields = {"chat_id": chat_id, "text": msg}
        if with_entities and entities:
            fields["entities"] = entities
        return urllib.parse.urlencode(fields).encode()

    for attempt in range(max_retry + 1):
        try:
            req = urllib.request.Request(url, data=build(entities is not None))
            with urllib.request.urlopen(req, timeout=20) as r:
                r.read()
            LAST_SEND_STATUS.append("ok")
            return True, False

        except urllib.error.HTTPError as e:
            # 여기까지 왔다는 건 텔레그램이 응답을 줬다는 뜻 = 전송 여부가 확실함
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
                print("텔레그램 인증/권한 오류(%s): %s" % (e.code, body))
                LAST_SEND_STATUS.append("auth_error_%s" % e.code)
                return False, False

            if 400 <= e.code < 500:
                # 커스텀 이모지 엔티티 때문에 거부됐을 수 있다(봇 자격 미달 등).
                # 엔티티를 빼고 평범한 🚨 로 딱 한 번 더 시도해서 알림이 사라지지
                # 않게 한다. 그래도 400 이면 진짜 메시지 문제라 건너뛴다.
                if entities is not None:
                    # 커스텀 이모지가 거부됐으니 이 프로세스 동안은 더 시도하지 않는다.
                    # (봇 자격 미달이면 매 알림마다 400 이 나므로 요청 낭비를 막음)
                    global _SIREN_DISABLED
                    _SIREN_DISABLED = True
                    print("텔레그램 400, 커스텀 이모지 빼고 재시도(이후 비활성): %s" % body)
                    try:
                        req = urllib.request.Request(url, data=build(False))
                        with urllib.request.urlopen(req, timeout=20) as r:
                            r.read()
                        LAST_SEND_STATUS.append("ok_plain")
                        return True, False
                    except Exception as e2:
                        print("커스텀 이모지 제거 후에도 실패:", repr(e2)[:150])
                print("텔레그램이 거부함(%s): %s" % (e.code, body))
                LAST_SEND_STATUS.append("rejected_%s" % e.code)
                return False, True

            print("텔레그램 서버 오류(%s)" % e.code)
            if attempt < max_retry:
                time.sleep(2)
                continue
            LAST_SEND_STATUS.append("server_error_%s" % e.code)
            return False, False

        except Exception as e:
            # 응답을 못 받음. 전달됐는지 알 수 없으므로 다시 보내지 않는다.
            print("텔레그램 응답 없음(전달 여부 불명, 재시도 안 함):", e)
            LAST_SEND_STATUS.append("no_response")
            return False, True

    LAST_SEND_STATUS.append("retry_exhausted")
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


# "새 뉴스를 처음 본 순간, 발행된 지 몇 초 지났는지" 최근 측정값 (피드 자체 지연 진단용)
LAST_FEED_LAG = []


def write_heartbeat(latest_id, count):
    """마지막 실행 시각 + 피드 상태 + 전송 결과 + 피드 지연을 한 줄로 기록.
    - send=... : 뉴스가 안 올 때 가져오기 문제인지 보내기 문제인지 구분
    - lag=...  : 뉴스가 발행되고 우리 눈에 보이기까지 걸린 시간(초).
                 이 값이 크면 파이낸셜주스 무료 피드 자체가 늦게 주는 것이라
                 우리 쪽을 아무리 조여도 소용없다는 뜻"""
    if LAST_SEND_STATUS:
        ok = LAST_SEND_STATUS.count("ok")
        bad = [x for x in LAST_SEND_STATUS if x != "ok"]
        send = "send=%d건성공" % ok + (" 실패=%s" % ",".join(sorted(set(bad))) if bad else "")
    else:
        send = "send=보낼것없음"
    lag = ""
    if LAST_FEED_LAG:
        recent = LAST_FEED_LAG[-20:]
        lag = " lag최근%d건=%d~%d초(중앙%d초)" % (
            len(recent), min(recent), max(recent),
            sorted(recent)[len(recent) // 2])
    atomic_write(HEARTBEAT_FILE, "%s latest_newsid=%s count=%s %s%s\n" % (
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), latest_id, count, send, lag))


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
    if not by_id:
        # 쓸 수 있는 NewsID가 하나도 없으면 되짚을 기준점이 없음 (min() 이 터짐)
        return items
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
    # 거르고 나서 비었는지 확인해야 함. 순서가 반대면 NewsID 없는 응답이 왔을 때
    # 아래 items[-1] 에서 IndexError 가 남
    items = [n for n in items if news_id_of(n) > 0]
    if not items:
        raise RuntimeError("뉴스 응답이 비어 있음")

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

    # 방금 처음 본 뉴스들의 "발행 후 경과 시간" 기록 (피드 지연 진단용)
    now_ts = int(time.time())
    for n in candidates:
        LAST_FEED_LAG.append(max(0, now_ts - parse_epoch(n.get("DatePublished"), default=now_ts)))
    del LAST_FEED_LAG[:-50]

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
        if is_link_only_post(n):
            # 모닝주스처럼 본문 없이 제목만 있는 자체 게시글은 보내지 않음
            print("자체 게시글 제외:", title[:60])
            sent_titles[normalize_title(title)] = now_epoch
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
            msg = "%s%s\n(원문: %s) (%s)" % (prefix, clip(translated), title, kst_time)
        else:
            msg = "%s%s (%s)" % (prefix, title, kst_time)

        ok, permanent = send_telegram(bot_token, CHAT_ID, msg)
        if ok:
            sent_count += 1
            last_ok_nid = item["nid"]
            print("전송%s 완료" % (" [중요]" if item["critical"] else ""))
        elif permanent:
            # 다시 보내면 안 되는 경우(메시지 자체 문제이거나, 전달 여부 불명).
            # 기록에는 보낸 것으로 남겨 두고 다음 뉴스로 넘어감.
            print("재전송 안 함(넘어감):", title[:60])
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


def run_loop(duration_seconds):
    """프로세스를 살려둔 채 POLL_INTERVAL_SECONDS 간격으로 계속 확인.
    매 주기 파이썬을 새로 띄우던 방식의 시작 비용(0.3~0.5초)이 사라져서 더 빠름.
    duration_seconds 가 지나면 종료 -> 워크플로가 상태를 커밋하고 다시 띄움."""
    # 지표 발표 5분 전 알림. 예약(cron)은 수십 분씩 밀려서 못 쓰고,
    # 이 루프가 유일하게 초 단위로 상시 떠 있는 곳이라 여기에 얹음.
    # 불러오기에 실패해도 속보 릴레이는 그대로 돌아야 함.
    try:
        import calendar_prealert
    except Exception as e:
        calendar_prealert = None
        print("지표 프리알림 사용 안 함:", repr(e)[:200])

    # 주요 지표(CPI/고용/PCE/FOMC)를 원 출처에서 직접 잡아 헤드라인보다 먼저 보냄.
    # 일정은 프리알림이 이미 30분마다 갱신해 둔 것을 그대로 쓴다(중복 호출 방지).
    try:
        import release_watch
        # 어떤 감시가 켜졌는지 시작할 때 한 번 알림. 발표 시각에만 로그가 찍히면
        # CONTACT_EMAIL 시크릿이 먹었는지 몇 주 뒤에나 알 수 있음
        release_watch.bls_ua()
    except Exception as e:
        release_watch = None
        print("지표 원출처 감시 사용 안 함:", repr(e)[:200])

    end = time.time() + duration_seconds
    while time.time() < end:
        cycle_start = time.time()
        # 주기마다 비움. 안 비우면 프로세스가 사는 10분 내내 값이 쌓여서,
        # 초반에 한 번 실패한 기록이 이후 모든 하트비트 줄에 계속 따라붙음
        del LAST_SEND_STATUS[:]
        try:
            main()
            note_success()
        except Exception as e:
            print("이번 주기 오류:", repr(e)[:300])
            note_failure(e)

        # 속보 릴레이와 분리. 지표 알림이 터져도 속보는 계속 나가야 함
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if token and calendar_prealert is not None:
            try:
                calendar_prealert.tick(token)
            except Exception as e:
                print("지표 프리알림 오류:", repr(e)[:200])

            if release_watch is not None:
                try:
                    release_watch.tick(token, calendar_prealert._cal_cache)
                except Exception as e:
                    print("지표 원출처 감시 오류:", repr(e)[:200])

        elapsed = time.time() - cycle_start
        remain = POLL_INTERVAL_SECONDS - elapsed
        if remain > 0:
            time.sleep(min(remain, max(0, end - time.time())))


if __name__ == "__main__":
    duration = 0
    if len(sys.argv) > 1:
        try:
            duration = int(sys.argv[1])
        except ValueError:
            duration = 0

    if duration > 0:
        run_loop(duration)
    else:
        # 인자 없이 실행하면 예전처럼 1회만 확인 (테스트/수동 실행용)
        try:
            main()
            note_success()
        except Exception as e:
            print("실행 오류:", repr(e)[:300])
            note_failure(e)
            raise SystemExit(1)
