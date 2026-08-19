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
