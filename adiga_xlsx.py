"""어디가 크롤 raw TSV → 수시/정시 엑셀 2개.

크롤 JS가 만든 결합 TSV(수시 블록 + `===JEONGSI===` + 정시 블록, 미제출·최초0 이미 제거됨)를
읽어 수시/정시 xlsx로 분리 저장한다. 숫자 컬럼은 숫자형으로 변환.

  py adiga_xlsx.py <raw.tsv> [출력폴더]
  py adiga_xlsx.py --selfcheck
"""
import csv, io, sys
from pathlib import Path
import openpyxl

SEP = "===JEONGSI==="
STRCOLS = {"대학", "전형유형", "세부전형", "구분", "모집단위"}  # 문자 유지(나머지는 숫자화)

def _num(v):
    if v in ("", None): return None
    try:
        f = float(v); return int(f) if f == int(f) else f
    except ValueError:
        return v

def split_blocks(text):
    text = text.lstrip("﻿")
    susi, jeongsi = text.split(SEP) if SEP in text else (text, "")
    su = [l for l in susi.splitlines() if l.strip()]
    je = [l for l in jeongsi.splitlines() if l.strip()]
    return su, je

def build(tsv_path, out_dir):
    text = Path(tsv_path).read_text(encoding="utf-8-sig")
    su, je = split_blocks(text)
    stem = Path(tsv_path).stem.replace("adiga_", "").replace("_raw", "")
    made = []
    for block, gigan in ((su, "수시"), (je, "정시")):
        if not block: continue
        wb = openpyxl.Workbook()
        rows = list(csv.reader(io.StringIO("\n".join(block)), delimiter="\t"))
        ws = wb.active; ws.title = gigan
        hdr = rows[0]; ws.append(hdr)
        for r in rows[1:]:
            ws.append([r[i] if hdr[i] in STRCOLS else _num(r[i]) for i in range(len(r))])
        out = Path(out_dir) / f"{stem}_{gigan}_2026.xlsx"
        try:
            wb.save(out)
        except PermissionError:                      # 대상이 엑셀에 열려 잠김 → 대체이름
            out = out.with_name(f"{stem}_{gigan}_2026_new.xlsx")
            wb.save(out)
        made.append((out, len(rows) - 1, len(hdr)))
    return made

def _selfcheck():
    sample = ("﻿대학\t세부전형\t구분\t모집단위\t최초\t경쟁률\n"
              "고려대\t일반\t수시\t철학과\t5\t3.2\n"
              "===JEONGSI===\n"
              "대학\t세부전형\t구분\t모집단위\t최초\t평균70\n"
              "고려대\t일반\t정시(가)\t철학과\t14\t93\n")
    su, je = split_blocks(sample)
    assert su[0].startswith("대학") and len(su) == 2, su
    assert je[0].startswith("대학") and len(je) == 2, je
    r = list(csv.reader(io.StringIO("\n".join(je)), delimiter="\t"))
    assert _num(r[1][4]) == 14 and _num(r[1][5]) == 93
    print("selfcheck OK — 수시/정시 분리·숫자화 정상")

if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        _selfcheck()
    elif len(sys.argv) >= 2:
        out_dir = sys.argv[2] if len(sys.argv) >= 3 else str(Path(sys.argv[1]).parent)
        for out, n, c in build(sys.argv[1], out_dir):
            print(f"{out.name}: {n}행 {c}열 → {out}")
    else:
        print("사용: py adiga_xlsx.py <raw.tsv> [출력폴더] | --selfcheck")
