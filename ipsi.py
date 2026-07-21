"""대입 DB 조회 도구.

시트(01_fact_admissions)를 로컬에 캐시하고, 원하는 조건으로 걸러 정리해 출력한다.

사용법:
  py ipsi.py                         # 조회 가능한 컬럼과 값 목록 보기
  py ipsi.py 대학명=서울대            # 서울대 전체
  py ipsi.py 대학명=서울대 지표=경쟁률 학년도=2028
  py ipsi.py 대학명=연세대 모집단위=컴퓨터   # 부분일치(대소문자 무시)
  py ipsi.py --refresh 대학명=고려대   # 캐시 새로고침 후 조회

필터는 여러 개 AND 조건, 값은 부분일치. 결과는 표로 정리해 출력한다.
"""
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
CACHE = HERE / "cache_fact.json"
SHEET_ID = "1aNaoxwtETvqNP_FsAFMJn3kG8eljBkn7BvpFlXaAYf8"
TAB = "01_fact_admissions"
# 정리해서 보여줄 기본 컬럼(값 없는 척도/번호 등은 상세보기에서만)
SHOW = ["학년도", "모집기간", "대학명", "계열", "단과대", "모집단위",
        "전형유형", "세부전형", "정원내외", "지표", "척도", "값"]


def fetch():
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    creds = Credentials.from_service_account_file(
        str(HERE / "credentials.json"),
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    api = build("sheets", "v4", credentials=creds).spreadsheets().values()
    values = api.get(spreadsheetId=SHEET_ID, range=f"{TAB}!A1:P100000").execute().get("values", [])
    head, rows = values[0], values[1:]
    data = [{head[i]: (r[i] if i < len(r) else "") for i in range(len(head))} for r in rows]
    CACHE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def load(refresh=False):
    if refresh or not CACHE.exists():
        return fetch()
    return json.loads(CACHE.read_text(encoding="utf-8"))


def parse_args(argv):
    refresh = "--refresh" in argv
    filters = {}
    for a in argv:
        if "=" in a and not a.startswith("--"):
            k, v = a.split("=", 1)
            filters[k.strip()] = v.strip()
    return refresh, filters


def apply_filters(data, filters):
    def match(row):
        return all(v.lower() in str(row.get(k, "")).lower() for k, v in filters.items())
    return [r for r in data if match(r)]


def print_table(rows, cols):
    if not rows:
        print("(조건에 맞는 데이터 없음)")
        return
    header = cols
    table = [header] + [[str(r.get(c, "")) for c in cols] for r in rows]
    widths = [max(len(row[i]) for row in table) for i in range(len(cols))]
    for i, row in enumerate(table):
        print(" | ".join(cell.ljust(widths[j]) for j, cell in enumerate(row)))
        if i == 0:
            print("-+-".join("-" * w for w in widths))


def show_options(data):
    """필터 없이 실행 시: 어떤 값으로 조회할 수 있는지 안내."""
    print(f"캐시 {len(data)}행. 아래 컬럼=값 으로 걸러서 조회하세요 (부분일치, AND).\n")
    for col in ["학년도", "모집기간", "대학명", "전형유형", "지표"]:
        vals = Counter(r.get(col, "") for r in data)
        top = ", ".join(f"{k}({n})" for k, n in vals.most_common(25) if k)
        print(f"[{col}] {top}")
    print('\n예) py ipsi.py 대학명=서울대 지표=경쟁률 학년도=2028')


def main():
    refresh, filters = parse_args(sys.argv[1:])
    data = load(refresh)
    if not filters:
        show_options(data)
        return
    hits = apply_filters(data, filters)
    print(f"조건 {filters} → {len(hits)}행\n")
    cap = 200
    print_table(hits[:cap], SHOW)
    if len(hits) > cap:
        print(f"\n...외 {len(hits)-cap}행 생략. 조건을 더 좁히세요.")


def _selfcheck():
    data = [
        {"대학명": "서울대", "학년도": "2028", "지표": "경쟁률", "값": "5.0"},
        {"대학명": "연세대", "학년도": "2028", "지표": "모집인원", "값": "10"},
        {"대학명": "서울대학교(서울)", "학년도": "2027", "지표": "경쟁률", "값": "4.2"},
    ]
    assert len(apply_filters(data, {"대학명": "서울대"})) == 2      # 부분일치
    assert len(apply_filters(data, {"대학명": "서울대", "학년도": "2028"})) == 1  # AND
    assert len(apply_filters(data, {"지표": "경쟁률"})) == 2
    assert apply_filters(data, {"대학명": "없는대학"}) == []
    print("selfcheck OK")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        _selfcheck()
    else:
        main()
