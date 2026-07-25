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


def translate_to_ko(text):
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

    new_items = [it for it in items if it[0] > last_epoch]
    new_items = new_items[-15:]

    if not new_items:
        print("새 속보 없음")
        return

    bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = "@haesunking"

    for epoch, title, link in new_items:
        clean_title = re.sub(r"^FinancialJuice:\s*", "", title)
        translated = translate_to_ko(clean_title)

        kst_time = time.strftime("%H:%M", time.gmtime(epoch + 9 * 3600))  # 한국시간(UTC+9)

        if translated and translated.lower() != clean_title.lower():
            msg = "\U0001F6A8 속보 (%s)\n%s\n(EN: %s)" % (kst_time, translated, clean_title)
        else:
            msg = "\U0001F6A8 속보 (%s)\n%s" % (kst_time, clean_title)

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

    new_last = max(last_epoch, latest_epoch)
    with open(LAST_FILE, "w") as f:
        f.write(str(new_last))

    print("새 속보 %s건 전송 완료, 기준 시각 갱신: %s" % (len(new_items), new_last))


if __name__ == "__main__":
    main()
