# 커스텀 이모지 셋에서 custom_emoji_id 를 찾아주는 일회용 유틸.
#
# 왜 필요한가:
#   🚨 를 움직이는 커스텀 이모지로 보내려면 그 이모지의 custom_emoji_id(숫자)가
#   필요하다. 그림만으로는 알 수 없고, 텔레그램 getStickerSet 으로 셋을 받아와야
#   각 이모지의 id 가 나온다. 여기서 사이렌 후보를 골라 출력한다.
#
# 사용:
#   TELEGRAM_BOT_TOKEN 환경변수 + 셋 short_name 인자
#     python3 scripts/find_custom_emoji.py <셋_short_name>
#   셋 short_name 은 이모지 셋 공유 링크 t.me/addemoji/<여기> 의 마지막 부분이다.
#
# getStickerSet 은 읽기 전용이라 안전하다(프리미엄도 불필요). 출력된 id 를
# 저장소 Secret 의 SIREN_CUSTOM_EMOJI_ID 에 넣으면 릴레이가 그 이모지를 쓴다.
import json
import os
import sys
import urllib.parse
import urllib.request

# base 이모지가 이런 것들이면 '사이렌 후보'로 표시(셋 제작자가 어떤 base 로
# 등록했는지에 따라 다르므로 넉넉히 잡는다). 못 찾으면 전체 목록에서 고르면 된다.
SIREN_BASE = {"🚨", "🔴", "🚓", "🆘", "⚠️", "🛑", "📢", "🔺", "❗"}


def fetch_set(token, name):
    url = "https://api.telegram.org/bot%s/getStickerSet?name=%s" % (
        token, urllib.parse.quote(name))
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN 이 없음")
    name = (sys.argv[1] if len(sys.argv) > 1
            else os.environ.get("SET_NAME", "")).strip()
    if not name:
        raise SystemExit("셋 short_name 을 인자로 주세요. "
                         "(이모지 셋 공유 링크 t.me/addemoji/<여기>)")

    data = fetch_set(token, name)
    if not data.get("ok"):
        raise SystemExit("getStickerSet 실패: %s" % json.dumps(data)[:300])

    st = data["result"]
    stickers = st.get("stickers", [])
    print("셋: %s  (short_name=%s, type=%s, %d개)" % (
        st.get("title"), st.get("name"), st.get("sticker_type"), len(stickers)))

    candidates = []
    for i, s in enumerate(stickers):
        cid = s.get("custom_emoji_id") or ""
        base = s.get("emoji") or ""
        is_cand = base in SIREN_BASE
        print("[%3d] %s  id=%s%s" % (i, base, cid, "   <== 사이렌 후보" if is_cand else ""))
        if is_cand:
            candidates.append((i, base, cid))

    print("\n================ 사이렌 후보 ================")
    if candidates:
        for i, base, cid in candidates:
            print("  #%d  %s   SIREN_CUSTOM_EMOJI_ID = %s" % (i, base, cid))
        print("\n위 id 중 원하는 것을 저장소 Secret 'SIREN_CUSTOM_EMOJI_ID' 에 넣으세요.")
    else:
        print("  base 이모지로는 못 찾았습니다. 위 전체 목록에서 사이렌의 그리드")
        print("  순서(index)를 보고 그 줄의 id 를 쓰세요.")


if __name__ == "__main__":
    main()
