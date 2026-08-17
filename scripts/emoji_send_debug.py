# 사이렌 커스텀 이모지 엔티티를 텔레그램이 '저장'하는지 '떼어내는지' 확인.
# sendMessage 응답의 result.entities 를 그대로 출력한다.
#   - custom_emoji 엔티티가 응답에 남아 있으면  -> 텔레그램이 수락(렌더링 조건 문제)
#   - 응답 entities 에서 사라졌으면            -> 봇이 Fragment 자격 미달이라 조용히 제거
import json
import os
import urllib.parse
import urllib.request

import financialjuice_relay as fj

token = os.environ["TELEGRAM_BOT_TOKEN"].strip()
msg = "🚨 엔티티 저장 확인 테스트 (삭제하세요)"
ent = fj._siren_entities(msg)
print("보낸 entities:", ent)

data = urllib.parse.urlencode(
    {"chat_id": fj.CHAT_ID, "text": msg, "entities": ent}).encode()
url = "https://api.telegram.org/bot%s/sendMessage" % token
resp = json.loads(urllib.request.urlopen(
    urllib.request.Request(url, data=data), timeout=20).read().decode())

print("ok:", resp.get("ok"))
returned = (resp.get("result") or {}).get("entities")
print("응답 entities:", json.dumps(returned, ensure_ascii=False))
has_custom = any((e or {}).get("type") == "custom_emoji" for e in (returned or []))
print("판정:", "🟢 텔레그램이 커스텀 이모지 수락(저장됨)" if has_custom
      else "🔴 커스텀 이모지 제거됨 -> 봇 Fragment 자격 미달")
