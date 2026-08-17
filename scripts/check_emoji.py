# custom_emoji_id 들이 유효한지, 움직이는지(애니메이션/비디오) 조회하는 진단 유틸.
# getCustomEmojiStickers 는 읽기 전용. 각 id 의 is_animated/is_video 로 '움직임' 여부를 안다.
#   is_animated=True (TGS) 또는 is_video=True (WEBM) 여야 실제로 움직인다.
#   둘 다 False 면 정지형 커스텀 이모지(이미지)라 안 움직인다.
#   결과에 없으면 그 id 는 유효하지 않음.
import json
import os
import sys
import urllib.parse
import urllib.request

token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
if not token:
    raise SystemExit("TELEGRAM_BOT_TOKEN 이 없음")

ids = [a.strip() for a in sys.argv[1:] if a.strip()]
if not ids:
    raise SystemExit("조회할 custom_emoji_id 를 인자로 주세요")

url = "https://api.telegram.org/bot%s/getCustomEmojiStickers?custom_emoji_ids=%s" % (
    token, urllib.parse.quote(json.dumps(ids)))
data = json.loads(urllib.request.urlopen(url, timeout=30).read().decode())
if not data.get("ok"):
    raise SystemExit("getCustomEmojiStickers 실패: %s" % json.dumps(data)[:300])

res = data["result"]
found = {s.get("custom_emoji_id"): s for s in res}
print("요청 %d개 / 응답 %d개\n" % (len(ids), len(res)))
for cid in ids:
    s = found.get(cid)
    if not s:
        print("  %s  -> ❌ 유효하지 않은 id (셋에 없음)" % cid)
        continue
    moves = s.get("is_animated") or s.get("is_video")
    print("  %s  base=%s set=%s  animated=%s video=%s  -> %s" % (
        cid, s.get("emoji"), s.get("set_name"),
        s.get("is_animated"), s.get("is_video"),
        "🟢 움직임" if moves else "⚪ 정지(안 움직임)"))
