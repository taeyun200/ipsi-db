# -*- coding: utf-8 -*-
"""입결 인원 집계·검산 출력기 (수시/정시 공용).

시트 캐시(cache_fact.json)에서 조건으로 걸러 그룹별 모집인원을 합산하고,
독립 2경로 검산까지 자동으로 붙여 표로 출력한다. (지표=모집인원 고정)

사용:
  py ipsi_report.py 대학명="동국대 서울" 학년도=2027,2028 모집기간=수시
  py ipsi_report.py 대학명="동국대 서울" 학년도=2028 모집기간=정시 --by=세부전형
  py ipsi_report.py 대학명=서울시립대 학년도=2028 모집기간=정시 --refresh

- 필터: key=value AND. 값은 부분일치(공백 여러 토큰이면 모두 포함). 학년도는 콤마로 여러 개(비교).
- --by=콤마열: 그룹 컬럼. 생략 시 수시=전형유형,세부전형 / 정시=지원군,세부전형.
- 학년도 2개면 두 해 + 증감 열. 첫 그룹컬럼별 소계 자동.
- 수시인데 세부전형에 '군' 표기가 있으면 정시 오분류로 보고 제외하고 경고.
"""
import json, sys, collections
from pathlib import Path
HERE = Path(__file__).parent
CACHE = HERE / "cache_fact.json"

def load(refresh):
    if refresh:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
        creds = Credentials.from_service_account_file(str(HERE/"credentials.json"),
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
        api = build("sheets","v4",credentials=creds).spreadsheets().values()
        v = api.get(spreadsheetId="1aNaoxwtETvqNP_FsAFMJn3kG8eljBkn7BvpFlXaAYf8",
                    range="01_fact_admissions!A1:P100000").execute()["values"]
        head=v[0]; rows=[dict(zip(head,r+[""]*(len(head)-len(r)))) for r in v[1:]]
        CACHE.write_text(json.dumps(rows,ensure_ascii=False),encoding="utf-8")
        return rows
    return json.loads(CACHE.read_text(encoding="utf-8"))

def main():
    argv = sys.argv[1:]
    refresh = "--refresh" in argv
    by = None
    fils = {}
    for a in argv:
        if a=="--refresh": continue
        if a.startswith("--by="): by = a[5:].split(","); continue
        if "=" in a:
            k,val = a.split("=",1); fils[k]=val
    years = fils.pop("학년도","").split(",") if fils.get("학년도") else []
    gi = fils.get("모집기간","")

    rows = load(refresh)
    def ok(r):
        if r.get("지표")!="모집인원": return False
        for k,val in fils.items():
            cell = r.get(k,"")
            if not all(t.lower() in cell.lower() for t in val.split()): return False
        if years and r.get("학년도") not in years: return False
        return True
    sel = [r for r in rows if ok(r)]

    # 정시 오분류(수시에 군 표기) 걸러내기
    mis = [r for r in sel if r.get("모집기간")=="수시" and "군" in r.get("세부전형","")]
    if mis:
        sel = [r for r in sel if not (r.get("모집기간")=="수시" and "군" in r.get("세부전형",""))]

    if not sel:
        print("조건에 맞는 행 없음:", fils, "학년도", years); return
    if not by:
        by = ["전형유형","세부전형"] if "수시" in gi else ["지원군","세부전형"]
    if not years:
        years = sorted({r["학년도"] for r in sel})

    bad = [r for r in sel if not str(r["값"]).strip().lstrip("-").isdigit()]
    agg = collections.defaultdict(int)          # (그룹키..., 연도) -> 합
    for r in sel:
        key = tuple(r.get(c,"") for c in by)
        agg[key+(r["학년도"],)] += int(r["값"])
    groupkeys = sorted({k[:-1] for k in agg},
                       key=lambda g:-agg.get(g+(years[-1],),0))

    w = [max(len(by[i]), *(len(k[i]) for k in groupkeys)) for i in range(len(by))]
    hdr = " | ".join(f"{by[i]:{w[i]}}" for i in range(len(by)))
    print(f"{hdr} | " + " | ".join(f"{y:>6}" for y in years) +
          (" | 증감" if len(years)==2 else ""))
    def line(vals, cells):
        s = " | ".join(f"{vals[i]:{w[i]}}" for i in range(len(by)))
        s += " | " + " | ".join(f"{c:6}" for c in cells)
        if len(years)==2: s += f" | {cells[1]-cells[0]:+5}"
        print(s)
    prev0 = None
    for g in groupkeys:
        if len(by)>=2 and g[0]!=prev0 and prev0 is not None: pass
        line(list(g), [agg.get(g+(y,),0) for y in years])
        prev0 = g[0]
    # 첫 컬럼 소계
    if len(by)>=2:
        print("-"*len(hdr))
        subs = collections.defaultdict(lambda: [0]*len(years))
        for g in groupkeys:
            for i,y in enumerate(years): subs[g[0]][i]+=agg.get(g+(y,),0)
        for k in sorted(subs, key=lambda k:-subs[k][-1]):
            c=subs[k]; extra=f"  ({c[1]-c[0]:+})" if len(years)==2 else ""
            print(f"  소계 {k}: " + " / ".join(f"{y} {c[i]}" for i,y in enumerate(years)) + extra)

    # 검산: 경로1(그룹합) vs 경로2(독립총합)
    print("\n[검산]")
    okall=True
    for y in years:
        p1 = sum(v for k,v in agg.items() if k[-1]==y)
        p2 = sum(int(r["값"]) for r in sel if r["학년도"]==y)
        mark = "통과" if p1==p2 else "★불일치★"
        if p1!=p2: okall=False
        print(f"  {y}: 그룹합 {p1} = 독립총합 {p2} → {mark}")
    print(f"  비정수 값: {len(bad)}건" + (f" {[ (r['모집단위'],r['값']) for r in bad[:3]]}" if bad else ""))
    if mis:
        print(f"  ⚠ 수시 오분류(군 표기) 제외 {len(mis)}건:",
              [(r['학년도'],r['모집단위'],r['세부전형'],r['값']) for r in mis])
    if not okall: print("  → 검산 불일치! 결과 신뢰 불가, 원인 규명 필요")

if __name__ == "__main__":
    main()
