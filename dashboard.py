"""대학 전형요약 대시보드 HTML 생성 (수기 PPT 대체).

  py dashboard.py 이화여자대학교(서울)            # → <대학>_전형요약.html
  py dashboard.py 이화여자대학교(서울) out.html
  py dashboard.py --selfcheck

입력: cache_fact.json(구글시트 캐시) + adiga_criteria.db(어디가 평가기준).
집계 규칙 — 인원=합, 경쟁률=모집인원 가중평균, 합격선=단순평균. 표에 그대로 표기한다.
검산: 전형유형 소계 합 == 전체 합(독립 2경로)을 생성 시 확인하고 실패하면 만들지 않는다.
"""
import html, json, sqlite3, sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
CACHE = HERE / "cache_fact.json"
CRITDB = HERE / "adiga_criteria.db"
MIN_YEAR = "2027"      # 세부전형 라벨이 안정된 구간. 이전 자료는 인원 비교에 쓰지 않는다.

def num(v):
    try:
        return float(str(v).replace(",", ""))
    except (ValueError, AttributeError):
        return None

def load(uni):
    rows = json.loads(CACHE.read_text(encoding="utf-8"))
    hit = [r for r in rows if r["대학명"] == uni]
    if not hit:                                   # 부분일치 구제
        hit = [r for r in rows if uni in r["대학명"]]
    return hit

def pick(rows, 지표, 척도=None):
    return [r for r in rows if r["지표"] == 지표 and (척도 is None or r["척도"] == 척도)]

def agg(rows, key, how):
    """key(r) -> 그룹. how='sum'|'wavg'(모집인원 가중)|'avg'."""
    g = defaultdict(list)
    for r in rows:
        v = num(r["값"])
        if v is None:
            continue
        g[key(r)].append((v, r))
    out = {}
    for k, vs in g.items():
        if how == "sum":
            out[k] = round(sum(v for v, _ in vs), 2)
        elif how == "avg":
            out[k] = round(sum(v for v, _ in vs) / len(vs), 2)
    return out

# ---------- 블록별 표 만들기 ----------

def block_인원(rows, years):
    """모집기간 × 전형유형 × 세부전형 별 모집인원 + 증감."""
    m = pick(rows, "모집인원")
    tbl = agg(m, lambda r: (r["모집기간"], r["전형유형"], r["세부전형"], r["학년도"]), "sum")
    keys = sorted({k[:3] for k in tbl})
    body = [[*k, *[tbl.get((*k, y)) for y in years]] for k in keys]
    for r in body:                                 # 증감 = 최신 - 직전
        a, b = r[3], r[4]
        r.append(None if a is None or b is None else round(a - b, 1))
    total = {y: round(sum(v for k, v in tbl.items() if k[3] == y), 1) for y in years}
    # 검산: 전형유형 소계 합 == 전체 합
    sub = defaultdict(float)
    for k, v in tbl.items():
        sub[(k[0], k[1], k[3])] += v
    for y in years:
        s = round(sum(v for k, v in sub.items() if k[2] == y), 1)
        assert s == total[y], f"인원 검산 실패 {y}: 소계합 {s} != 전체 {total[y]}"
    return body, total

def block_입결(rows, 기간, years):
    """전형유형 × 지원군 × 연도별 경쟁률·합격선. 경쟁률은 모집인원 가중평균.

    과거(≤2026) 자료는 세부전형 라벨 표기가 해마다 달라(군 포함/분리, `_`/공백) 세부전형으로
    묶으면 연도 간 비교가 끊긴다. 그래서 입결은 **전형유형+지원군**으로 묶는다.
    """
    sel = [r for r in rows if r["모집기간"] == 기간]
    인원 = {(r["전형유형"], r["학년도"], r["모집단위"], r["지원군"]): num(r["값"])
            for r in pick(sel, "모집인원")}
    out = defaultdict(dict)
    for r in pick(sel, "경쟁률"):
        v = num(r["값"])
        w = 인원.get((r["전형유형"], r["학년도"], r["모집단위"], r["지원군"])) or 0
        if v is None or not w:
            continue
        d = out[(r["지원군"], r["전형유형"])].setdefault(r["학년도"], {"경쟁_wsum": 0, "w": 0})
        d["경쟁_wsum"] += v * w
        d["w"] += w
    # 합격선 척도는 대학마다 채워진 게 다르다(백분위만/대학제공만) → 가장 많이 채워진 하나로 통일
    cand = [r["척도"] for r in pick(sel, "합격선") if "70%" in r["척도"]]
    척도 = max(set(cand), key=cand.count) if cand else "-"
    for r in pick(sel, "합격선", 척도):
        v = num(r["값"])
        if v is None:
            continue
        d = out[(r["지원군"], r["전형유형"])].setdefault(r["학년도"], {})
        d.setdefault("컷들", []).append(v)
    body = []
    for k in sorted(out):
        row = [k[0], k[1]]
        for y in years:
            d = out[k].get(y, {})
            row.append(round(d["경쟁_wsum"] / d["w"], 2) if d.get("w") else None)
            row.append(round(sum(d["컷들"]) / len(d["컷들"]), 2) if d.get("컷들") else None)
        body.append(row)
    return body, 척도

def block_평가(uni):
    if not CRITDB.exists():
        return {}
    con = sqlite3.connect(CRITDB)
    q = """SELECT 전형유형, 섹션, 종류, 내용 FROM criteria
           WHERE 대학명 LIKE ? ORDER BY 전형유형, 순번"""
    out = defaultdict(list)
    for 유형, 섹, 종, 내 in con.execute(q, (f"%{uni.split('(')[0]}%",)):
        out[유형].append((섹, 종, 내))
    con.close()
    return out

# ---------- HTML ----------

CSS = """
body{font-family:'Malgun Gothic',sans-serif;margin:0;background:#f4f6f8;color:#1c2530}
.wrap{max-width:1180px;margin:0 auto;padding:24px 16px 80px}
h1{background:#1f2d3d;color:#fff;padding:14px 18px;border-radius:8px;font-size:20px;margin:0 0 20px}
h2{background:#2f6f8f;color:#fff;padding:9px 14px;border-radius:6px;font-size:15px;margin:26px 0 10px}
table{border-collapse:collapse;width:100%;background:#fff;font-size:12.5px;
      box-shadow:0 1px 3px rgba(0,0,0,.08);border-radius:6px;overflow:hidden}
th,td{border:1px solid #dfe4ea;padding:5px 7px;text-align:center;vertical-align:middle}
th{background:#eef3f7;font-weight:600}
td.l,th.l{text-align:left}
tr:nth-child(even) td{background:#fafbfc}
.up{color:#c0392b;font-weight:600}.dn{color:#2471a3;font-weight:600}
.scroll{overflow-x:auto}
.note{font-size:11.5px;color:#5b6b7b;margin:6px 2px 0}
pre.txt{white-space:pre-wrap;background:#fff;border:1px solid #dfe4ea;border-radius:6px;
        padding:10px 12px;font-family:inherit;font-size:12.5px;line-height:1.5;margin:8px 0}
details{margin:8px 0}summary{cursor:pointer;font-weight:600;padding:6px 0;font-size:13px}
@media print{body{background:#fff}h2{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
"""

def esc(v):
    return "" if v is None else html.escape(str(v))

def cell(v):
    return "-" if v is None else (f"{v:g}" if isinstance(v, float) else str(v))

def delta(v):
    if v is None:
        return "-"
    cls = "up" if v > 0 else ("dn" if v < 0 else "")
    return f'<span class="{cls}">{v:+g}</span>' if v else "0"

def tsv_table(tsv):
    rows = [r.split("\t") for r in tsv.split("\n")]
    w = max(len(r) for r in rows)
    out = ["<div class='scroll'><table>"]
    for i, r in enumerate(rows):
        tag = "th" if i == 0 else "td"
        cells = "".join(f"<{tag} class='l'>{esc(c)}</{tag}>" for c in r)
        cells += f"<{tag}></{tag}>" * (w - len(r))
        out.append(f"<tr>{cells}</tr>")
    return "\n".join(out) + "</table></div>"

def build(uni, out_path):
    rows = load(uni)
    assert rows, f"'{uni}' 데이터 없음"
    uni = rows[0]["대학명"]
    years = sorted({r["학년도"] for r in pick(rows, "모집인원") if r["학년도"] >= MIN_YEAR},
                   reverse=True)[:2]
    입결연도 = sorted({r["학년도"] for r in pick(rows, "경쟁률")}, reverse=True)[:3]

    인원, 총계 = block_인원(rows, years)
    h = [f"<h1>{esc(uni)} 전형요약 &nbsp;<span style='font-size:14px;opacity:.75'>"
         f"{esc(years[0])}학년도 기준</span></h1>"]

    # 1. 전형별 인원
    h.append("<h2>전형별 모집인원</h2><div class='scroll'><table><tr>"
             "<th>모집시기</th><th>전형유형</th><th class='l'>세부전형</th>"
             + "".join(f"<th>{esc(y)}</th>" for y in years) + "<th>증감</th></tr>")
    for r in 인원:
        h.append(f"<tr><td>{esc(r[0])}</td><td>{esc(r[1])}</td><td class='l'>{esc(r[2])}</td>"
                 f"<td>{cell(r[3])}</td><td>{cell(r[4])}</td><td>{delta(r[5])}</td></tr>")
    h.append(f"<tr><th colspan='3'>합계</th>"
             + "".join(f"<th>{cell(총계[y])}</th>" for y in years)
             + f"<th>{delta(round(총계[years[0]] - 총계[years[1]], 1))}</th></tr>")
    h.append("</table></div><p class='note'>인원=합계. 전형유형 소계 합과 전체 합이 일치함을 "
             "생성 시 검산함.</p>")

    # 2. 수시/정시 입결
    for 기간 in ("수시", "정시"):
        body, 척도 = block_입결(rows, 기간, 입결연도)
        if not body:
            continue
        h.append(f"<h2>{기간} 전형유형별 경쟁률 · 입결</h2><div class='scroll'><table><tr>"
                 "<th>군</th><th class='l'>전형유형</th>"
                 + "".join(f"<th colspan='2'>{esc(y)}</th>" for y in 입결연도) + "</tr><tr>"
                 "<th></th><th></th>"
                 + "".join("<th>경쟁률</th><th>합격선</th>" for _ in 입결연도) + "</tr>")
        for r in body:
            h.append(f"<tr><td>{esc(r[0])}</td><td class='l'>{esc(r[1])}</td>"
                     + "".join(f"<td>{cell(v)}</td>" for v in r[2:]) + "</tr>")
        h.append(f"</table></div><p class='note'>경쟁률=모집인원 가중평균, 합격선={esc(척도)} "
                 "단순평균. 원자료는 모집단위 단위. 과거 세부전형 라벨이 해마다 달라 "
                 "<b>전형유형+군</b>으로 묶었다.</p>")

    # 3. 평가기준(어디가 원문)
    crit = block_평가(uni)
    if crit:
        h.append("<h2>전형별 평가방법 · 변경사항 <span style='font-size:11px;opacity:.8'>"
                 "(어디가 원문)</span></h2>")
        for 유형 in ("공통", "학생부위주(종합)", "학생부위주(교과)", "수능위주"):
            frags = crit.get(유형)
            if not frags:
                continue
            h.append(f"<details><summary>{esc(유형)}</summary>")
            last = None
            for 섹, 종, 내 in frags:
                if 섹 != last:
                    h.append(f"<p class='note'><b>[{esc(섹)}]</b></p>")
                    last = 섹
                h.append(tsv_table(내) if 종 == "table" else f"<pre class='txt'>{esc(내)}</pre>")
            h.append("</details>")

    doc = (f"<!doctype html><html lang='ko'><meta charset='utf-8'>"
           f"<title>{esc(uni)} 전형요약</title><style>{CSS}</style>"
           f"<body><div class='wrap'>{''.join(h)}</div></body></html>")
    Path(out_path).write_text(doc, encoding="utf-8")
    return uni, len(인원), 총계

def _selfcheck():
    rows = [{"대학명": "가대", "모집기간": "수시", "지원군": "", "학년도": y, "전형유형": "학생부위주(교과)",
             "세부전형": s, "모집단위": u, "지표": i, "척도": sc, "값": v}
            for y, s, u, i, sc, v in [
                ("2027", "교과", "A", "모집인원", "", "10"), ("2027", "교과", "B", "모집인원", "", "5"),
                ("2026", "교과", "A", "모집인원", "", "8"),
                ("2026", "교과", "A", "경쟁률", "", "10"), ("2026", "교과", "A", "합격선", "내신_70%", "2.0")]]
    body, tot = block_인원(rows, ["2027", "2026"])
    assert tot == {"2027": 15, "2026": 8}, tot
    assert body[0][5] == 7, body                       # 증감 15-8
    ip, 척도 = block_입결(rows, "수시", ["2026"])
    assert ip[0][2] == 10 and ip[0][3] == 2.0, ip      # 가중평균·합격선
    assert 척도 == "내신_70%"
    print("selfcheck OK — 인원 합·증감·가중평균 경쟁률·합격선 정상")

if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        print(__doc__)
    elif a[0] == "--selfcheck":
        _selfcheck()
    else:
        uni, n, tot = build(a[0], a[1] if len(a) > 1 else None
                            or HERE / f"{a[0].split('(')[0]}_전형요약.html")
        print(f"{uni}: 전형 {n}행, 모집인원 {tot}")
