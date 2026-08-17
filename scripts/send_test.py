# 텔레그램 테스트 발송 (수동). 사이렌 커스텀 이모지가 실제로 먹는지 확인용.
# send_telegram 을 그대로 쓰므로 커스텀 이모지 엔티티 + 400 폴백까지 실경로로 검증된다.
#   - 마지막 상태가 "ok"       -> 엔티티 수락(봇 자격 OK) -> 채널에서 움직일 것
#   - 마지막 상태가 "ok_plain" -> 엔티티 거부(자격 미달)  -> 평범한 🚨 로 전송됨
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import financialjuice_relay as fj

token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
if not token:
    raise SystemExit("TELEGRAM_BOT_TOKEN 이 없음")

# 워크플로가 빈 입력이면 빈 문자열 인자를 넘기므로, strip 후 비면 기본 문구를 쓴다.
arg = sys.argv[1].strip() if len(sys.argv) > 1 else ""
msg = arg or "🚨 테스트 메시지\n사이렌 커스텀 이모지 확인용입니다. 확인 후 삭제하세요."

ok, permanent = fj.send_telegram(token, fj.CHAT_ID, msg)
status = fj.LAST_SEND_STATUS[-1] if fj.LAST_SEND_STATUS else "(없음)"
print("send_telegram -> ok=%s permanent=%s status=%s" % (ok, permanent, status))
if status == "ok":
    print("결과: 커스텀 이모지 엔티티 수락됨 -> 채널에서 '움직이는 사이렌'으로 보일 것")
elif status == "ok_plain":
    print("결과: 커스텀 이모지 거부(봇 Fragment 자격 미달) -> 평범한 🚨 로 전송됨")
else:
    print("결과: 전송 실패/보류 -> status 확인")
