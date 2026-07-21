"""어디가(adiga) 입시결과표 파서 — 수시(종합/교과) + 정시(수능) 공용.

입력: 대학정보 > 평가기준 및 입시결과 에서 각 탭(Ⅱ.학생부종합/Ⅲ.학생부교과/Ⅳ.수능위주)의
      "2026학년도 전형 결과" 아코디언을 펼쳐 get_page_text 로 추출한 텍스트(여러 탭 이어붙여도 됨).
출력: 와이드포맷 행 리스트(dict). adiga_reflect.py 가 모집기간별 매핑으로 시트 반영.

섹션 헤더: "<전형유형원문>(<수시/정시모집> <세부전형>)"
행: "수시 …" 또는 "정시(가/나/다) …"
레이아웃(모집단위 뒤 데이터 칸수):
  수능위주 = 29칸: 최초 이월 최종 경쟁 충원 환산50 환산70 [백분위50 11] [백분위70 11]
             (평균백분위50=idx15, 평균백분위70=idx26)
  학생부*  =  9칸: 최초 이월 최종 경쟁 충원 환산50 환산70 등급50 등급70
'미제출' 행·최초(A)=0 행은 제외.
"""
import re
import sys

# 섹션 헤더는 대학마다 형식이 다름:
#   서울대: "학생부종합전형(수시모집 일반전형)" / "수능위주전형(정시모집 일반전형)"
#   연세대: "학생부종합(활동우수형)" / "학생부교과(추천형)" / "수능(일반전형[일반계열])"
# → 전형유형 접두어(전형 접미·수시/정시모집 접두 optional) + 괄호 안 세부전형.
SEC = re.compile(r"^(학생부종합|학생부교과|수능위주|수능)(?:전형)?\((?:(?:수시|정시)모집\s*)?(.+?)\)\s*$")
GUN = re.compile(r"^(수시|정시\((.)\))\s")
HANGMOK = {"학생부종합": "학생부위주(종합)", "학생부교과": "학생부위주(교과)",
           "수능위주": "수능위주", "수능": "수능위주"}
DLEN = {"수능위주": 29, "학생부위주(종합)": 9, "학생부위주(교과)": 9}

def _n(v):
    return None if v == "-" else v

def parse(text, 대학명):
    rows, skipped = [], []
    hy = sebu = None
    for line in text.splitlines():
        line = line.strip()
        m = SEC.match(line)
        if m:
            hy = HANGMOK[m.group(1)]
            sebu = m.group(2).strip().replace(" 학생", "")
            continue
        g = GUN.match(line)
        if not g or hy is None:
            continue
        gun = g.group(2) or ""                   # 정시 군(가/나/다) 또는 '' (수시)
        gigan = "정시" if line.startswith("정시") else "수시"   # 모집기간은 행 구분열 기준
        toks = line.split()
        unit_all = toks[1:]
        if "미제출" in line:
            name = []
            for t in unit_all:
                if re.match(r"^-?\d", t): break
                name.append(t)
            skipped.append((gigan, hy, sebu, " ".join(name), "미제출")); continue
        n = DLEN[hy]
        if len(toks) < 2 + n:
            skipped.append((gigan, hy, sebu, line, "형식이상")); continue
        d = toks[-n:]
        unit = " ".join(toks[1:-n])
        if d[0] in ("0", "-"):
            skipped.append((gigan, hy, sebu, unit, "최초0")); continue
        row = {"대학명": 대학명, "모집기간": gigan, "전형유형": hy, "지원군": gun,
               "세부전형": sebu, "모집단위": unit,
               "최초": d[0], "이월": d[1], "최종": d[2],
               "경쟁률": _n(d[3]), "충원": _n(d[4]),
               "환산50": _n(d[5]), "환산70": _n(d[6])}
        if n == 29:                              # 정시(수능): 백분위 평균
            row["평균50"] = _n(d[15]); row["평균70"] = _n(d[26])
        else:                                    # 수시(학생부): 환산등급
            row["등급50"] = _n(d[7]); row["등급70"] = _n(d[8])
        rows.append(row)
    return rows, skipped


_SAMPLE = """수능위주전형(정시모집 일반전형)
정시(나) 컴퓨터공학부 36 2 38 3.76 10 406.9 403.9 98 99 - 89 - - 93 - 96 1 2 99 99 - 95 - - 74 - 94 1 2
정시(나) 교육학과 0 0 0 0 - - - 미제출 사유 : 모집인원0
학생부종합전형(수시모집 일반전형)
수시 경제학부 60 0 60 4.93 3 - - 1.77 2.11
수시 사회복지학과 1 0 1 17 - 미제출 사유 : 3명이하
학생부교과(추천형)
수시 의예과 15 0 15 6 5 99.04 99.03 1 1
수능(일반전형[일반계열])
정시(가) 경영학과 114 4 118 3.91 97 674.16 670.66 99 91 97 - - 95 - - 95 1 1 98 84 95 - - 92 - - 92 2 2
"""

def _selfcheck():
    rows, skipped = parse(_SAMPLE, "서울대학교(서울)")
    by = {(r["모집기간"], r["전형유형"], r["세부전형"], r["모집단위"]): r for r in rows}
    assert len(rows) == 4, [r["모집단위"] for r in rows]
    j = by[("정시", "수능위주", "일반전형", "컴퓨터공학부")]           # 서울대식 헤더
    assert (j["최초"], j["이월"], j["환산50"], j["평균70"]) == ("36","2","406.9","94"), j
    s = by[("수시", "학생부위주(종합)", "일반전형", "경제학부")]
    assert (s["등급50"], s["등급70"], s["충원"], s["지원군"]) == ("1.77","2.11","3",""), s
    g = by[("수시", "학생부위주(교과)", "추천형", "의예과")]           # 연세대식 헤더(교과)
    assert (g["최초"], g["환산50"], g["등급50"], g["등급70"]) == ("15","99.04","1","1"), g
    y = by[("정시", "수능위주", "일반전형[일반계열]", "경영학과")]     # 연세대식 헤더(수능)
    assert (y["지원군"], y["환산50"], y["평균50"], y["평균70"]) == ("가","674.16","95","92"), y
    assert any(x[3] == "사회복지학과" and x[4] == "미제출" for x in skipped), skipped
    print(f"selfcheck OK — 행 {len(rows)} (서울대·연세대 헤더 공용), 제외 {len(skipped)}")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        _selfcheck()
    elif len(sys.argv) >= 3:
        text = open(sys.argv[1], encoding="utf-8").read()
        rows, skipped = parse(text, sys.argv[2])
        import json
        print(json.dumps({"rows": rows, "skipped": skipped}, ensure_ascii=False, indent=1))
    else:
        print("사용: py adiga_parse.py --selfcheck | <덤프.txt> <대학명>")
