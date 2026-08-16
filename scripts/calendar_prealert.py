# 지표 발표 전 알림 (T-5분)
#
# [왜 릴레이 루프 안에 있는가]
#   깃허브 예약(cron)은 무료 티어에서 수십 분씩 밀린다. 이 저장소도 예약에만 맡겼다가
#   2~4시간 공백을 반복해서 겪었다(financialjuice-relay.yml 주석 참고).
#   "발표 5분 전"은 몇 분만 밀려도 의미가 없어지므로 예약에 걸 수 없다.
#   반면 financialjuice_relay.py 의 루프는 1초 간격으로 상시 떠 있으므로,
#   거기에 얹으면 새 인프라 없이 초 단위 정확도가 나온다.
#
# [중복 방지]
#   파이썬 프로세스는 10분마다 재시작하고 잡은 약 5.5시간마다 교대한다.
#   그래서 이미 보낸 알림은 .state/cal_prealert.json 에 남기고, 이 파일은
#   워크플로가 다른 상태 파일과 같이 커밋한다.
#
# [알림을 놓치는 경우]
#   발표 시각이 지났으면 보내지 않는다. 릴레이가 5분 창 내내 죽어 있었다면
#   그 지표는 건너뛴다. 발표 후에 "5분 후 발표" 알림이 가는 게 더 나쁘다.
import json
import os
import time
from datetime import datetime, timezone

import calendar_relay as cal

PREALERT_LEAD_SECONDS = 300        # 발표 몇 초 전에 알릴지 (5분)
CAL_REFRESH_SECONDS = 1800         # 일정을 다시 받아오는 주기 (30분)
STATE_FILE = os.path.join(".state", "cal_prealert.json")
STATE_TTL_SECONDS = 24 * 3600      # 보낸 기록 보관 기간
LOOKAHEAD_SECONDS = 6 * 3600       # 앞으로 이 시간 안의 일정만 들고 있음

_cal_cache = []                    # [(발표시각 UTC, 이벤트), ...]
_cal_fetched_at = 0.0
_cal_next_retry = 0.0


def event_key(e):
    """중복 판정용 키. ID(GUID)가 있으면 그걸 쓰고, 없으면 시각+제목+국가로 만든다."""
    eid = (e.get("ID") or "").strip()
    if eid:
        return eid
    return "%s|%s|%s" % ((e.get("RealDate") or "")[:16],
                         (e.get("Title") or "")[:60],
                         e.get("CountryCode") or "")


def load_state():
    try:
        with open(STATE_FILE) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_state(state, now_epoch):
    pruned = {k: v for k, v in state.items()
              if isinstance(v, (int, float)) and now_epoch - v < STATE_TTL_SECONDS}
    os.makedirs(".state", exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(pruned, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATE_FILE)
    return pruned


def refresh_calendar(now_epoch):
    """일정을 30분마다 새로 받아온다. 실패하면 이전 것을 계속 쓰고 5분 뒤 재시도."""
    global _cal_cache, _cal_fetched_at, _cal_next_retry
    if _cal_cache and now_epoch - _cal_fetched_at < CAL_REFRESH_SECONDS:
        return
    if now_epoch < _cal_next_retry:
        return
    try:
        start = datetime.fromtimestamp(now_epoch, timezone.utc)
        end = datetime.fromtimestamp(now_epoch + LOOKAHEAD_SECONDS, timezone.utc)
        raw = cal.fetch_calendar()
        _cal_cache = cal.pick_events(raw, start, end)
        _cal_fetched_at = now_epoch
        print("[프리알림] 일정 %d건 갱신 (앞으로 %d시간)"
              % (len(_cal_cache), LOOKAHEAD_SECONDS // 3600))
    except Exception as e:
        _cal_next_retry = now_epoch + 300
        print("[프리알림] 일정 갱신 실패, 5분 뒤 재시도:", repr(e)[:150])


def build_message(t_utc, e):
    kst = t_utc.astimezone(cal.KST)
    flag = cal.flag_of(e)
    title = cal.to_korean(e.get("Title"))
    lead_min = PREALERT_LEAD_SECONDS // 60

    line = "⏰ %d분 후 지표 발표\n\n%s %s %s" % (
        lead_min, kst.strftime("%H:%M"), flag, title)

    fc = cal.val(e.get("Forecast"))
    pv = cal.val(e.get("Previous"))
    if fc or pv:
        line += "\n예상 %s / 이전 %s" % (fc or "-", pv or "-")

    sp = cal.val(e.get("Speaker"))
    if sp:
        line += "\n%s" % sp
    return line


def tick(bot_token, now_epoch=None):
    """릴레이 루프가 매 주기 부르는 함수. 보낸 건수를 돌려준다.

    호출하는 쪽에서 예외를 삼켜야 한다. 지표 알림이 실패해도 속보 릴레이는
    계속 돌아야 하므로, 여기서 터진 예외가 루프를 멈추게 두면 안 된다."""
    now_epoch = time.time() if now_epoch is None else now_epoch
    refresh_calendar(now_epoch)
    if not _cal_cache:
        return 0

    state = load_state()
    due = []
    for t_utc, e in _cal_cache:
        t_epoch = t_utc.timestamp()
        # 발표 전 창 안에 있을 때만. 이미 발표됐으면 보내지 않는다.
        if not (t_epoch - PREALERT_LEAD_SECONDS <= now_epoch < t_epoch):
            continue
        k = event_key(e)
        if k in state:
            continue
        due.append((t_utc, e, k))

    if not due:
        return 0

    sent = 0
    for t_utc, e, k in due:
        try:
            msg = build_message(t_utc, e)
        except Exception as ex:
            print("[프리알림] 메시지 생성 실패:", repr(ex)[:150])
            continue
        ok, permanent = _send(bot_token, msg)
        # 보냈거나 '다시 보내면 안 되는' 경우에만 기록한다.
        # 일시적 실패는 기록하지 않아서 다음 주기에 창 안이면 재시도된다.
        if ok or permanent:
            state[k] = now_epoch
            sent += int(ok)
            print("[프리알림] %s 발송%s" % ((e.get("Title") or "")[:50],
                                            "" if ok else " (건너뜀)"))
    save_state(state, now_epoch)
    return sent


def _send(bot_token, msg):
    """속보 릴레이의 전송 함수를 그대로 쓴다. 텔레그램 재시도/중복 방지 규칙이
    이미 그 안에 정리돼 있어서 따로 만들면 규칙이 갈라진다."""
    import financialjuice_relay as fj
    return fj.send_telegram(bot_token, cal.CHAT_ID, msg)
