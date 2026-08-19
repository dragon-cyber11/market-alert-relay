# GitHub Actions 러너에서 원출처(release_watch 소스)에 닿는지 진단.
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("CONTACT_EMAIL", os.environ.get("CONTACT_EMAIL",""))
import release_watch as rw

srcs = [
    ("EIA(ir.eia.gov)", rw.fetch_eia),
    ("CPI(bls)",       rw.fetch_cpi),
    ("PPI(bls)",       rw.fetch_ppi),
    ("PCE/GDP(bea)",   rw.fetch_pce),
    ("FOMC(fed)",      rw.fetch_fomc),
]
for name, fn in srcs:
    t=time.time()
    try:
        got=fn()
        dt=time.time()-t
        print("  OK   %-16s %.2fs  %s" % (name, dt, "결과있음" if got else "None(비활성/미발견)"))
    except Exception as e:
        print("  FAIL %-16s %s" % (name, repr(e)[:90]))

# --- 러너 환경에서 tick() 전체 경로 자가진단 (합성 EIA 무장) ---
def selftest_tick():
    import time as _t, datetime as _d
    try:
        import release_watch as _rw
        now=_t.time(); t=now+60
        tu=_d.datetime.fromtimestamp(t,_d.timezone.utc)
        ev=(tu,{"ID":"EIACrudeOilInventories","Title":"EIA Crude Oil Inventories",
                "CountryCode":"US","ImpID":"2","Forecast":"0.2M","Previous":"-6M",
                "RealDate":tu.strftime("%Y-%m-%dT%H:%M:%S")})
        import os as _o
        _o.makedirs(".state",exist_ok=True)
        n=_rw.tick("dummy",[ev])          # 발표 전 -> baseline 포착돼야 함
        st=_rw.load_state().get("eia")
        print("  SELFTEST tick(): 반환=%s baseline포착=%s state=%s" % (n, bool(st), st))
    except Exception as e:
        import traceback; print("  SELFTEST 예외:"); traceback.print_exc()

selftest_tick()
