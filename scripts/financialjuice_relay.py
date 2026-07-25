import json
import os
import re
import time
import urllib.request
import urllib.parse
from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET

FEED_URL = "https://www.financialjuice.com/feed.ashx?xy=rss"
STATE_DIR = ".state"
LAST_FILE = os.path.join(STATE_DIR, "fj_last_epoch.txt")
HEARTBEAT_FILE = os.path.join(STATE_DIR, "fj_heartbeat.txt")
SENT_TITLES_FILE = os.path.join(STATE_DIR, "fj_sent_titles.json")
SENT_TITLES_TTL_SECONDS = 24 * 3600
SENT_TITLES_MAX = 500


def normalize_title(text):
    return re.sub(r"\s+", " ", text).strip().lower()


def load_sent_titles():
    if not os.path.exists(SENT_TITLES_FILE):
        return {}
    try:
        with open(SENT_TITLES_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_sent_titles(sent_titles, now_epoch):
    pruned = {
        t: e for t, e in sent_titles.items() if now_epoch - e < SENT_TITLES_TTL_SECONDS
    }
    if len(pruned) > SENT_TITLES_MAX:
        pruned = dict(sorted(pruned.items(), key=lambda kv: kv[1], reverse=True)[:SENT_TITLES_MAX])
    with open(SENT_TITLES_FILE, "w") as f:
        json.dump(pruned, f)


def translate_to_ko(text):
    """무료 MyMemory API로 영어 -> 한국어 번역. 실패하면 None을 반환(원문만 전송)."""
    try:
        params = urllib.parse.urlencode({"q": text, "langpair": "en|ko"})
        url = "https://api.mymemory.translated.net/get?%s" % params
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        translated = data.get("responseData", {}).get("translatedText")
        if translated and not translated.strip().startswith("QUERY LENGTH"):
            return translated.strip()
    except Exception as e:
        print("번역 실패:", e)
    return None


def main():
    os.makedirs(STATE_DIR, exist_ok=True)

    req = urllib.request.Request(FEED_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = resp.read()

    root = ET.fromstring(data)
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        if not pub:
            continue
        try:
            dt = parsedate_to_datetime(pub)
            epoch = int(dt.timestamp())
        except Exception:
            continue
        items.append((epoch, title, link))

    items.sort(key=lambda x: x[0])

    latest_epoch = items[-1][0] if items else 0

    with open(HEARTBEAT_FILE, "w") as f:
        f.write(
            "%s latest_epoch=%s count=%s\n"
            % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), latest_epoch, len(items))
        )

    if not os.path.exists(LAST_FILE):
        with open(LAST_FILE, "w") as f:
            f.write(str(latest_epoch))
        print("초기화: 기준 시각(epoch) %s 저장 (이번 실행은 발송 안 함)" % latest_epoch)
        return

    with open(LAST_FILE) as f:
        content = f.read().strip()
        last_epoch = int(content) if content else 0

    candidate_items = [it for it in items if it[0] > last_epoch]
    candidate_items = candidate_items[-30:]

    new_last = max(last_epoch, latest_epoch)

    if not candidate_items:
        print("새 속보 없음")
        with open(LAST_FILE, "w") as f:
            f.write(str(new_last))
        return

    sent_titles = load_sent_titles()

    to_send = []
    for epoch, title, link in candidate_items:
        clean_title = re.sub(r"^FinancialJuice:\s*", "", title)
        key = normalize_title(clean_title)
        if key in sent_titles:
            continue
        to_send.append((epoch, clean_title))
        sent_titles[key] = epoch

    to_send = to_send[-15:]

    if not to_send:
        print("새 속보 없음 (전부 이미 보낸 제목)")
        save_sent_titles(sent_titles, latest_epoch)
        with open(LAST_FILE, "w") as f:
            f.write(str(new_last))
        return

    bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = "@haesunking"

    for epoch, clean_title in to_send:
        translated = translate_to_ko(clean_title)

        kst_time = time.strftime("%H:%M", time.gmtime(epoch + 9 * 3600))  # 한국시간(UTC+9)

        if translated and translated.lower() != clean_title.lower():
            msg = "\U0001F6A8 %s\n(EN: %s)\n(%s)" % (translated, clean_title, kst_time)
        else:
            msg = "\U0001F6A8 %s\n(%s)" % (clean_title, kst_time)

        body = urllib.parse.urlencode({"chat_id": chat_id, "text": msg}).encode()
        req2 = urllib.request.Request(
            "https://api.telegram.org/bot%s/sendMessage" % bot_token,
            data=body,
        )
        try:
            with urllib.request.urlopen(req2, timeout=15) as r2:
                print("전송 결과 (%s):" % epoch, r2.read().decode()[:200])
        except Exception as e:
            print("전송 실패 (%s):" % epoch, e)
        time.sleep(1)

    save_sent_titles(sent_titles, latest_epoch)
    with open(LAST_FILE, "w") as f:
        f.write(str(new_last))

    print("새 속보 %s건 전송 완료, 기준 시각 갱신: %s" % (len(to_send), new_last))


if __name__ == "__main__":
    main()
