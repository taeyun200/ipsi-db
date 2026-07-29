"""서울대 수시모집 안내 PDF(모집단위와 모집인원 표) → 단과대·모집단위별 전형 인원 엑셀.

  py snu_pdf_recruit.py <안내.pdf> [출력.xlsx]

좌표 기반 파싱(x=컬럼, y=행). 단과대 라벨은 블록 중간에 놓이므로 소계행을 기준으로 블록을 끊고,
**소계와 합이 맞는 뒷부분만 그 블록**으로 본다(앞부분은 소계 없는 단독 단과대 행).
검산: 행합=합계 · 블록합=소계 · (행 전체합)=(소계합+단독행합)=PDF 합계행.
"""
import collections, re, sys
from pathlib import Path
import pdfplumber, openpyxl

XCOL = [(250, 340, "지역균형"), (340, 410, "일반"), (410, 470, "기회균형"), (470, 999, "합계")]
KEYS = ("지역균형", "일반", "기회균형", "합계")
HDRS = {"모집단위", "정원", "내", "전형", "지역균형전형", "일반전형", "기회균형특별전형",
        "(사회통합)", "합계", "모집단위와", "모집인원"}

def _num(t):
    m = re.match(r"[\d,]+", t)            # '206(42))' 처럼 붙은 각주 번호 제거
    return int(m.group().replace(",", "")) if m else 0

def _clean(t):
    return re.sub(r"\d\)$", "", t)        # '인문대학1)' → '인문대학'

def _lines(page, tol=3):
    """같은 줄 묶기. 각주가 붙은 셀은 1~2px 위로 올라가므로 tol 이내는 한 줄로 본다."""
    out = []
    for w in sorted(page.extract_words(), key=lambda w: w["top"]):
        if out and w["top"] - out[-1][0] <= tol:
            out[-1][1].append((round(w["x0"]), w["text"]))
        else:
            out.append([w["top"], [(round(w["x0"]), w["text"])]])
    return [(round(y), sorted(c)) for y, c in out]

def parse(pdf_path):
    rows, subtot, grand = [], [], None
    for page in pdfplumber.open(pdf_path).pages:
        college, groups, buf = "", [], []

        def flush(sub=None):
            """소계가 있으면 합이 맞는 뒷부분만 그 블록, 앞부분은 단독 단과대 행."""
            nonlocal buf, college
            cut = 0
            if sub:
                for k in range(len(buf) + 1):          # 뒤에서 k개의 합 == 소계
                    if all(sum(r[c] for r in buf[len(buf) - k:]) == sub[c] for c in KEYS):
                        cut = len(buf) - k
                        break
                else:
                    raise AssertionError(f"소계 불일치: {sub} / {buf}")
            else:
                cut = len(buf)
            for r in buf[:cut]:                        # 단독 행(소계 없음)
                r["단과대"] = r["_own"] or r["모집단위"]
                r["_solo"] = True
            # 단과대 라벨이 블록 첫 행과 같은 줄에 놓이는 경우가 있어 블록 전체에 같은 값을 준다
            col = college or next((r["_own"] for r in buf[cut:] if r["_own"]), "")
            for r in buf[cut:]:
                r["단과대"] = col or r["모집단위"]
            if sub:
                subtot.append(sub)
            rows.extend(buf); buf = []; college = ""

        lines = _lines(page)
        # 학부 그룹라벨(전공 묶음)은 자식 행 사이에 놓여 뒤에 나올 수 있어 미리 모은다
        for y, cells in lines:
            if not any(re.match(r"[\d·]", t) for x, t in cells):
                groups += [(y, t) for x, t in cells if 110 <= x < 190 and t not in HDRS]

        for y, cells in lines:
            names = [(x, t) for x, t in cells if x < 260]
            nums = {c: _num(t) for x, t in cells if re.match(r"[\d·]", t)
                    for a, b, c in XCOL if a <= x < b}
            if nums:
                nums = {k: nums.get(k, 0) for k in KEYS}
            if not nums:                                        # 텍스트만 있는 행
                for x, t in names:
                    if t in HDRS: continue
                    if x < 110: college += _clean(t)             # 단과대(두 줄로 쪼개짐)
                continue
            label = " ".join(t for x, t in names if 110 <= x < 260)
            if not label: continue
            if label.startswith("소계"):
                flush(nums); continue
            if label.startswith("합계"):
                flush(); grand = nums; break        # 합계행 아래는 각주 — 표 끝
            if groups and any(x >= 190 for x, t in names):       # 전공 → 학부(전공)
                label = f"{min(groups, key=lambda g: abs(g[0] - y))[1]}({label})"
            if sum(nums[k] for k in KEYS[:3]) == 0:
                continue                                         # 광역 등 전 항목 '·'
            own = next((_clean(t) for x, t in names if x < 110), "")
            buf.append({"모집단위": label, "_own": own, "_solo": False, **nums})
        flush()
    return rows, subtot, grand

def verify(rows, subtot, grand):
    bad = [r for r in rows if sum(r[k] for k in KEYS[:3]) != r["합계"]]
    assert not bad, f"행 합계 불일치: {bad}"
    p1 = {k: sum(r[k] for r in rows) for k in KEYS}                       # 경로1: 전 행 직합
    p2 = {k: sum(s[k] for s in subtot) + sum(r[k] for r in rows if r["_solo"])
          for k in KEYS}                                                  # 경로2: 소계+단독
    assert p1 == p2, f"경로1 {p1} != 경로2 {p2}"
    assert p1["지역균형"] + p1["일반"] + p1["기회균형"] == p1["합계"], p1
    assert grand is None or all(p1[k] == grand[k] for k in grand), f"{p1} != PDF합계행 {grand}"
    return p1, p2

def main(pdf_path, out_path):
    rows, subtot, grand = parse(pdf_path)
    p1, p2 = verify(rows, subtot, grand)
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "수시모집인원"
    cols = ["단과대", "모집단위", "지역균형", "일반", "기회균형", "합계"]
    ws.append(cols)
    for r in rows:
        ws.append([r[c] for c in cols])
    ws.append(["합계", "", p1["지역균형"], p1["일반"], p1["기회균형"], p1["합계"]])
    wb.save(out_path)
    print(f"{out_path}: {len(rows)}행 (단과대 {len({r['단과대'] for r in rows})}개)")
    print(f"검산 통과 — 경로1(행 직합) {p1} == 경로2(소계+단독) {p2} == PDF 합계행 {grand}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); raise SystemExit(1)
    src = sys.argv[1]
    main(src, sys.argv[2] if len(sys.argv) > 2 else str(Path(src).with_suffix(".xlsx")))
