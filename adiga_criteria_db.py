"""어디가 '평가기준(전형별 주요사항)' 크롤 JSON → SQLite DB.

  py adiga_criteria_db.py <크롤.json> [db경로]     # 적재 + 요약
  py adiga_criteria_db.py --선택 "면접"             # 내용 검색
  py adiga_criteria_db.py --selfcheck

한 행 = 대학 × 전형유형(종합/교과/수능) × 섹션 × 조각(문단 또는 표).
표는 TSV 문자열로 저장(종류='table'). 대학이 직접 쓴 자유서식이라 그 이상은 정규화하지 않는다.
"""
import json, sqlite3, sys
from pathlib import Path

HERE = Path(__file__).parent
DB = HERE / "adiga_criteria.db"
COLS = ["대학명", "unvCd", "학년도", "전형유형", "섹션", "순번", "종류", "내용"]
DDL = """
CREATE TABLE IF NOT EXISTS criteria (
  대학명 TEXT, unvCd TEXT, 학년도 TEXT, 전형유형 TEXT,
  섹션 TEXT, 순번 INTEGER, 종류 TEXT, 내용 TEXT,
  PRIMARY KEY (unvCd, 학년도, 전형유형, 섹션, 순번, 종류)
);
CREATE INDEX IF NOT EXISTS ix_uni ON criteria(대학명, 전형유형, 섹션);
"""

def load(json_path, db_path=DB):
    rows = json.loads(Path(json_path).read_text(encoding="utf-8"))
    seq = {}                       # 섹션명이 한 탭에 두 번 나오는 대학이 있어 순번은 탭 단위로 다시 매긴다
    for r in rows:
        k = (r["unvCd"], r["학년도"], r["전형유형"])
        seq[k] = r["순번"] = seq.get(k, 0) + 1
    con = sqlite3.connect(db_path)
    con.executescript(DDL)
    con.executemany(
        f"INSERT OR REPLACE INTO criteria ({','.join(COLS)}) VALUES ({','.join('?' * len(COLS))})",
        [[r[c] for c in COLS] for r in rows])
    con.commit()
    return con, rows

def summary(con, rows):
    q = lambda s: con.execute(s).fetchall()
    n_db, = q("SELECT COUNT(*) FROM criteria")[0],
    # 검산: JSON 행수 == DB 행수, 대학별 소계 합 == 전체
    per = q("SELECT 대학명, COUNT(*) FROM criteria GROUP BY 대학명")
    assert sum(n for _, n in per) == n_db[0] == len(rows), (per, n_db, len(rows))
    print(f"검산 통과 — JSON {len(rows)}행 = DB {n_db[0]}행 = 대학별 소계 합 ({len(per)}개 대학)")
    print("\n[전형유형별]")
    for k, n, t in q("""SELECT 전형유형, COUNT(*), SUM(종류='table') FROM criteria
                        GROUP BY 전형유형 ORDER BY 2 DESC"""):
        print(f"  {k:<14} {n:>4}조각 (표 {t})")
    print("\n[섹션 커버리지 — 몇 개 대학에 있나]")
    for 전형, 섹션, u in q("""SELECT 전형유형, 섹션, COUNT(DISTINCT 대학명) FROM criteria
                             GROUP BY 전형유형, 섹션 HAVING COUNT(DISTINCT 대학명) >= 3
                             ORDER BY 전형유형, 3 DESC"""):
        print(f"  {전형:<14} {섹션:<22} {u:>2}/16")

def search(kw, db_path=DB):
    con = sqlite3.connect(db_path)
    for 대, 전, 섹, 내 in con.execute(
            "SELECT 대학명, 전형유형, 섹션, 내용 FROM criteria WHERE 내용 LIKE ? LIMIT 20", (f"%{kw}%",)):
        print(f"[{대} / {전} / {섹}] {내[:110].replace(chr(9), ' ')}")

def _selfcheck():
    import tempfile, os
    tmp = Path(tempfile.gettempdir()) / "_crit_test.db"
    tmp.unlink(missing_ok=True)
    src = Path(tempfile.gettempdir()) / "_crit_test.json"
    src.write_text(json.dumps([
        {"대학명": "가대", "unvCd": "1", "학년도": "2027", "전형유형": "수능위주",
         "섹션": "전형별 특성", "순번": 1, "종류": "text", "내용": "가나다"},
        {"대학명": "가대", "unvCd": "1", "학년도": "2027", "전형유형": "수능위주",
         "섹션": "전형별 특성", "순번": 2, "종류": "table", "내용": "국어\t수학\n40\t40"},
    ], ensure_ascii=False), encoding="utf-8")
    con, rows = load(src, tmp)
    con2, rows2 = load(src, tmp)                    # 재적재해도 중복 안 생김(PK)
    n, = con2.execute("SELECT COUNT(*) FROM criteria").fetchone()
    assert n == 2, n
    assert con2.execute("SELECT COUNT(*) FROM criteria WHERE 종류='table'").fetchone()[0] == 1
    con.close(); con2.close()
    os.remove(tmp); os.remove(src)
    print("selfcheck OK — 적재·재적재(중복없음)·표 저장 정상")

if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] == "--help":
        print(__doc__)
    elif a[0] == "--selfcheck":
        _selfcheck()
    elif a[0] == "--검색":
        search(a[1])
    else:
        con, rows = load(a[0], a[1] if len(a) > 1 else DB)
        summary(con, rows)
        print(f"\nDB: {a[1] if len(a) > 1 else DB}")
