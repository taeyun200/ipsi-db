"""어디가 파싱행 → 시트 반영기 (수시+정시 공용).

adiga_parse 로 얻은 와이드행을 기존 시트행에 매칭·검산해 롱포맷으로 append.
기본은 dry-run(리포트만). 실제 반영은 --commit.

  py adiga_reflect.py <덤프.txt> <대학명> [--commit]
  (덤프는 수시·정시 탭 텍스트를 이어붙여도 됨)

매핑(시트반영.md 확정):
  정시: 환산50→합격선/수능_대학제공점수_50%, 환산70→…_70%, 평균70→합격선/수능_백분위_70%, 충원
  수시: 등급50→합격선/내신_50%, 등급70→합격선/내신_70%, 충원
매칭: (모집기간·지원군·모집단위정규화·세부전형) → 안 맞으면 최초(A)+경쟁률 유일 후보.
      검증은 최초(A)==시트 모집인원 & 경쟁률 일치. 신규 모집단위/전형은 보류·보고.
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from adiga_parse import parse

HERE = Path(__file__).parent
CACHE = HERE / "cache_fact.json"
SHEET_ID = "1aNaoxwtETvqNP_FsAFMJn3kG8eljBkn7BvpFlXaAYf8"
TAB = "01_fact_admissions"
COLS = ["대학선호도","모집기간","지원군","대학명","계열","단과대","학과번호","모집단위",
        "전형유형","전형번호","세부전형","정원내외","학년도","지표","척도","값"]
# 모집기간 → (자료컬럼, 지표, 척도)
MAP = {
    "정시": [("환산50","합격선","수능_대학제공점수_50%"),
             ("환산70","합격선","수능_대학제공점수_70%"),
             ("평균70","합격선","수능_백분위_70%"),
             ("충원","충원","")],
    "수시": [("등급50","합격선","내신_50%"),
             ("등급70","합격선","내신_70%"),
             ("충원","충원","")],
}

def num(v):
    if v in (None,"","None","-"): return None
    try: return "%g"%float(v)
    except: return str(v)
def norm(s):
    s = str(s).replace(chr(0x30FB),"·").replace("(", " ").replace(")","")
    return " ".join(s.split())

def build_index(cache, 대학명, 학년도):
    """key=(모집기간, 지원군, 모집단위정규화, 세부전형) → 후보 리스트.
    같은 키에 시트행이 여럿(예: 진리자유학부 인문/자연)이면 리스트로 보존하고
    매칭 때 최초(A)로 특정한다(키에 계열을 넣지 않음 — 어디가가 계열을 안 주므로)."""
    IDCOLS = COLS[:13]
    groups = {}                                  # 전체 식별컬럼 → 후보
    idx = defaultdict(list)
    for r in cache:
        if r.get("대학명")!=대학명 or r.get("학년도")!=학년도: continue
        gid = tuple(r.get(c,"") for c in IDCOLS)
        c = groups.get(gid)
        if c is None:
            c = {"ident": r, "has": set()}
            groups[gid] = c
            k = (r.get("모집기간"), r.get("지원군"), norm(r.get("모집단위")), r.get("세부전형"))
            idx[k].append(c)
        if r.get("지표")=="모집인원": c["모집인원"]=num(r.get("값"))
        if r.get("지표")=="경쟁률":  c["경쟁률"]=num(r.get("값"))
        if r.get("지표") in ("합격선","충원"):
            c["has"].add((r.get("지표"), r.get("척도")))
    return idx

def _pick(cands, mo, gb):
    """후보 여럿이면 최초(A)로 특정, 최초도 겹치면 경쟁률로 최종 특정."""
    if len(cands) <= 1: return cands
    by_mo = [c for c in cands if c.get("모집인원")==mo]
    if len(by_mo) <= 1: return by_mo
    by_both = [c for c in by_mo if c.get("경쟁률")==gb and gb not in (None,"0")]
    return by_both if len(by_both)==1 else by_mo

def match_row(r, idx):
    gi, gun = r["모집기간"], r["지원군"]
    unit, se = norm(r["모집단위"]), r["세부전형"]
    mo, gb = num(r["최초"]), num(r["경쟁률"])
    cands = list(idx.get((gi, gun, unit, se), []))       # 1차: 이름키
    if not cands:                                        # 2차: 이름 불일치 → (모집기간·지원군·세부전형) 전체
        cands = [c for k, cl in idx.items() if k[0]==gi and k[1]==gun and k[3]==se for c in cl]
    cands = _pick(cands, mo, gb)
    if len(cands) != 1:
        return None, ("동일 최초 후보 %d개로 특정 실패" % len(cands) if cands
                      else "시트에 모집단위+세부전형 없음(신규/조인불가)")
    s = cands[0]
    if gb not in (None,"0") and s.get("경쟁률")!=gb:
        return None, f"경쟁률 불일치(시트 {s.get('경쟁률')} vs 자료 {gb})"
    if gb in (None,"0") and s.get("모집인원")!=mo:
        return None, f"경쟁률無+모집인원 불일치(시트 {s.get('모집인원')} vs 최초 {mo})"
    info = None if s.get("모집인원")==mo else "모집인원차:최초%s/시트%s"%(mo, s.get("모집인원"))
    return s, info

def reflect(rows, idx):
    out, held, moflag, recount = [], [], [], Counter()
    for r in rows:
        s, info = match_row(r, idx)
        if s is None:
            held.append((r["모집기간"],r["세부전형"],r["모집단위"],num(r["최초"]),num(r["경쟁률"]),info)); continue
        if info: moflag.append((r["모집기간"],r["모집단위"],r["세부전형"],info))
        ident = [s["ident"].get(c,"") for c in COLS[:13]]
        for col, ji, ch in MAP[r["모집기간"]]:
            v = num(r.get(col))
            if v in (None,"0"): continue
            if (ji,ch) in s.get("has",set()): continue
            out.append(ident+[ji,ch,v]); recount[(r["모집기간"],ji,ch)]+=1
    return out, held, moflag, recount

def verify(rows, idx, recount):
    chk = Counter()
    for r in rows:
        s, _ = match_row(r, idx)
        if s is None: continue
        for col, ji, ch in MAP[r["모집기간"]]:
            v = num(r.get(col))
            if v in (None,"0") or (ji,ch) in s.get("has",set()): continue
            chk[(r["모집기간"],ji,ch)]+=1
    return chk == recount

def main():
    args = sys.argv[1:]
    commit = "--commit" in args
    args = [a for a in args if not a.startswith("--")]
    if len(args) < 2:
        print("사용: py adiga_reflect.py <덤프.txt> <대학명> [--commit]"); return
    text = open(args[0], encoding="utf-8").read()
    대학명 = args[1]
    rows, skipped = parse(text, 대학명)
    cache = json.load(open(CACHE, encoding="utf-8"))
    idx = build_index(cache, 대학명, "2026")

    out, held, moflag, recount = reflect(rows, idx)
    ok = verify(rows, idx, recount)

    gi_cnt = Counter(r["모집기간"] for r in rows)
    print(f"파싱 {len(rows)}행 {dict(gi_cnt)} / 파서제외 {len(skipped)} / 매칭실패(보류) {len(held)}")
    print(f"생성 append {len(out)}행  {dict(recount)}")
    print(f"검산(독립2경로) {'통과' if ok else '★불일치★'}")
    if moflag:
        print(f"\n[모집인원 최초≠시트 {len(moflag)}건 — 기록만]")
        for gi,u,se,i in moflag[:60]: print(f"  [{gi}] {u}/{se} {i}")
    if held:
        print(f"\n[보류 {len(held)}건 — 반영 안 함]")
        for gi,se,u,mo,gb,why in held: print(f"  [{gi}] {u}[{se}] 최초{mo}경쟁{gb} → {why}")
    if not ok:
        print("\n검산 불일치 → 반영 중단"); return
    if commit and out:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
        creds = Credentials.from_service_account_file(str(HERE/"credentials.json"),
            scopes=["https://www.googleapis.com/auth/spreadsheets"])
        api = build("sheets","v4",credentials=creds).spreadsheets().values()
        res = api.append(spreadsheetId=SHEET_ID, range=f"{TAB}!A1", valueInputOption="RAW",
                         insertDataOption="INSERT_ROWS", body={"values": out}).execute()
        print("\n반영:", res["updates"]["updatedRange"], res["updates"]["updatedRows"], "행")
    elif out:
        print("\n(dry-run) --commit 붙이면 위 append 실행")

if __name__ == "__main__":
    main()
