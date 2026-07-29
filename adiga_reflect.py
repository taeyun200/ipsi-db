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
    s = str(s).replace(chr(0x30FB),"").replace("·","").replace("(", " ").replace(")","")
    return " ".join(s.split())     # 가운뎃점은 표기차(아동・청소년학과 ↔ 아동청소년학과)라 제거
# 어디가 모집단위명 ↔ 시트 모집단위명(개명 등). 1차 키 실패 시에만 보조로 사용.
ALIAS = {"의예과": "의과대학"}
def nse(s):
    """세부전형 정규화 — 자료 '학교추천' ↔ 시트 '학교추천전형' 같은 접미차 흡수."""
    s = str(s or "")
    return s[:-2] if s.endswith("전형") else s


def xlsx_rows(엑셀대학명, dirpath=None):
    """4년제전체_{수시,정시}_2026.xlsx 에서 한 대학 행을 parse() 출력 형식으로 뽑는다.
    (엑셀은 이미 미제출·최초0 제거된 크롤 결과 — 재크롤 불필요)"""
    import openpyxl
    d = Path(dirpath or HERE)
    out = []
    for gigan in ("수시", "정시"):
        f = d / f"4년제전체_{gigan}_2026.xlsx"
        if not f.exists(): continue
        it = openpyxl.load_workbook(f, read_only=True).active.iter_rows(values_only=True)
        head = list(next(it))
        for r in it:
            row = dict(zip(head, r))
            if row.get("대학") != 엑셀대학명: continue
            gu = str(row.get("구분") or "")
            se = row.get("세부전형") or ""
            if not se:                            # 크롤이 헤더를 못 쪼갠 대학: '학생부종합(일반)' → '일반'
                hy = str(row.get("전형유형") or "")
                if "(" in hy and hy.endswith(")"):
                    se = hy[hy.index("(") + 1:-1]
            row.update(모집기간=gigan, 지원군=(gu[3] if gu.startswith("정시(") else ""), 세부전형=se)
            out.append(row)
    return out

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
            k = (r.get("모집기간"), r.get("지원군"), norm(r.get("모집단위")), nse(r.get("세부전형")))
            idx[k].append(c)
        if r.get("지표")=="모집인원": c["모집인원"]=num(r.get("값"))
        if r.get("지표")=="경쟁률":  c["경쟁률"]=num(r.get("값"))
        if r.get("지표") in ("합격선","충원"):
            c["has"].add((r.get("지표"), r.get("척도")))
    return idx

def _rounded(sheet, data):
    """자료 경쟁률이 시트값을 소수 1자리 이하로 줄인 같은 값인가(46.17→46.1 식 절사 포함)."""
    try:
        d = len(data.split(".")[1]) if "." in data else 0
        return d <= 1 and len(sheet.split(".")[-1]) > d and abs(float(sheet) - float(data)) < 0.1
    except (ValueError, IndexError):
        return False

def _pick(cands, mo, gb):
    """후보 여럿이면 최초(A)로 특정, 최초도 겹치면 경쟁률로 최종 특정."""
    if len(cands) <= 1: return cands
    by_mo = [c for c in cands if c.get("모집인원")==mo]
    if len(by_mo) <= 1: return by_mo
    by_both = [c for c in by_mo if c.get("경쟁률")==gb and gb not in (None,"0")]
    return by_both if len(by_both)==1 else by_mo

def match_row(r, idx, strict=True):
    """strict=False면 경쟁률 불일치도 매칭으로 보고 사유를 info로 돌려준다(정정 대상 추출용)."""
    gi, gun = r["모집기간"], r["지원군"]
    unit, se = norm(r["모집단위"]), nse(r["세부전형"])
    mo, gb = num(r["최초"]), num(r["경쟁률"])
    # 구체적인 키부터 순서대로 시도해, 최초(A)+경쟁률로 하나만 남는 첫 경로를 채택한다.
    same = [(k, cl) for k, cl in idx.items() if k[0] == gi and k[1] == gun]
    routes = [
        lambda: idx.get((gi, gun, unit, se), []),                          # 모집단위+세부전형
        lambda: idx.get((gi, gun, norm(ALIAS.get(r["모집단위"], "")), se), []),  # 개명 별칭
        lambda: [c for k, cl in same if k[2] == unit for c in cl],         # 모집단위명만(세부전형 라벨 불일치)
        lambda: [c for k, cl in same if k[3] == se for c in cl],           # 세부전형만(모집단위명 불일치)
        lambda: [c for _, cl in same for c in cl],                         # 값으로만
    ]
    cands = []
    for route in routes:
        cands = _pick(list(route()), mo, gb)
        if len(cands) == 1: break
    if len(cands) != 1:
        return None, ("동일 최초 후보 %d개로 특정 실패" % len(cands) if cands
                      else "시트에 모집단위+세부전형 없음(신규/조인불가)")
    s = cands[0]
    sg = s.get("경쟁률")
    note = None
    if gb in (None,"0") or sg is None:                   # 대조할 경쟁률이 없으면 모집인원으로 검증
        if s.get("모집인원") != mo:
            return None, f"경쟁률無+모집인원 불일치(시트 {s.get('모집인원')} vs 최초 {mo})"
        if sg is None: note = f"시트 경쟁률無 — 모집인원 {mo} 일치로 반영"
    elif sg != gb:
        if _rounded(sg, gb):                             # 자료가 반올림된 같은 값 → 시트값(정밀) 유지
            note = f"경쟁률 반올림차(시트 {sg} 유지 / 자료 {gb})"
        else:
            diff = f"경쟁률 불일치(시트 {sg} vs 자료 {gb})"
            if strict: return None, diff
            return s, diff
    if s.get("모집인원") != mo:
        note = (note + " / " if note else "") + "모집인원차:최초%s/시트%s" % (mo, s.get("모집인원"))
    return s, note

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

def _api():
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    creds = Credentials.from_service_account_file(str(HERE/"credentials.json"),
        scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets","v4",credentials=creds).spreadsheets().values()


def rate_fixes(rows, cache, 대학명):
    """매칭은 되는데 경쟁률만 다른 행 → 어디가 값으로 정정할 (시트행번호, 새값) 목록.
    캐시 인덱스 i ↔ 시트 행 i+2 (ipsi.py fetch가 A1부터 순서대로 읽어 저장)."""
    rowno = {}                                   # 식별컬럼 → 경쟁률 셀 행번호
    for i, r in enumerate(cache):
        if r.get("대학명") == 대학명 and r.get("지표") == "경쟁률":
            rowno[tuple(r.get(c, "") for c in COLS[:13])] = i + 2
    idx = build_index(cache, 대학명, "2026")
    out = []
    for r in rows:
        s, info = match_row(r, idx, strict=False)
        if s is None or not (info or "").startswith("경쟁률 불일치"): continue
        ident = s["ident"]
        rn = rowno.get(tuple(ident.get(x, "") for x in COLS[:13]))
        if rn is not None:
            out.append((rn, ident, s.get("경쟁률"), num(r["경쟁률"])))
    return out


def main():
    args = sys.argv[1:]
    commit = "--commit" in args
    args = [a for a in args if not a.startswith("--")]
    if len(args) < 2:
        print("사용: py adiga_reflect.py <덤프.txt> <대학명> [--commit]\n"
              "      py adiga_reflect.py --xlsx <엑셀대학명> <시트대학명> [--commit]"); return
    대학명 = args[1]
    if "--xlsx" in sys.argv:                      # 산출된 4년제전체 엑셀에서 바로 반영
        rows, skipped = xlsx_rows(args[0]), []
    else:
        rows, skipped = parse(open(args[0], encoding="utf-8").read(), 대학명)
    cache = json.load(open(CACHE, encoding="utf-8"))

    if "--fix-rate" in sys.argv:                  # 경쟁률만 다른 행을 어디가 값으로 정정(반영은 안 함)
        fixes = rate_fixes(rows, cache, 대학명)
        print(f"경쟁률 정정 대상 {len(fixes)}건")
        for rn, ident, old, new in fixes:
            print(f"  P{rn}  [{ident['모집기간']}{ident['지원군']}] {ident['모집단위']}/{ident['세부전형']}  {old} → {new}")
        if commit and fixes:
            api = _api()
            res = api.batchUpdate(spreadsheetId=SHEET_ID, body={"valueInputOption":"RAW",
                "data":[{"range": f"{TAB}!P{rn}", "values":[[new]]} for rn,_,_,new in fixes]}).execute()
            print("정정:", res["totalUpdatedCells"], "셀 — 캐시 새로고침 후 반영을 다시 실행하세요")
        elif fixes:
            print("\n(dry-run) --commit 붙이면 정정 실행")
        return

    idx = build_index(cache, 대학명, "2026")
    out, held, moflag, recount = reflect(rows, idx)
    ok = verify(rows, idx, recount)

    gi_cnt = Counter(r["모집기간"] for r in rows)
    print(f"파싱 {len(rows)}행 {dict(gi_cnt)} / 파서제외 {len(skipped)} / 매칭실패(보류) {len(held)}")
    print(f"생성 append {len(out)}행  {dict(recount)}")
    print(f"검산(독립2경로) {'통과' if ok else '★불일치★'}")
    if moflag:
        print(f"\n[기록만 {len(moflag)}건 — 시트값 유지]")
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
