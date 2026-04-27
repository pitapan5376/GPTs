#!/usr/bin/env python3
"""
気象庁 長期予報テキスト取得スクリプト
=====================================
対象予報: 1か月予報 / 3か月予報 / 暖候期予報（6〜8月）/ 寒候期予報（12〜2月）
対象地域: 関東甲信地方（千葉を含む）

必須パッケージ:
    pip install requests beautifulsoup4

任意パッケージ（PDFフォールバック用）:
    pip install pdfplumber

使用法:
    python jma_longfcst.py            # 全予報を取得
    python jma_longfcst.py --3m       # 3か月予報のみ
    python jma_longfcst.py --warm     # 暖候期予報のみ
    python jma_longfcst.py --pdf      # PDFから取得（pdfplumber必須）
    python jma_longfcst.py --debug    # デバッグ情報を表示

備考:
    ・暖候期（6〜8月）予報は2月下旬、寒候期（12〜2月）予報は9月下旬に発表。
      両方とも URL パラメータは term=P6M となるため、直近に発表された方が
      表示されます。取得済みの発表日時で暖候期か寒候期かを判断してください。
    ・気象庁サイトのページ構成が変わった場合は HTML パーサ部分の調整が必要
      です。その際は --debug フラグで取得内容を確認してください。
"""

import argparse
import io
import sys
import textwrap
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────────────────────────
# 設定
# ─────────────────────────────────────────────────────────────────
REGION_CODE = "010300"  # 関東甲信地方
REGION_NAME = "関東甲信地方（千葉含む）"
TIMEOUT = 30

# ブラウザを模倣するリクエストヘッダ
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Referer": "https://www.data.jma.go.jp/cpd/longfcst/",
}

# 各予報種別の定義
FORECAST_TYPES = {
    "1m": {
        "name": "1か月予報",
        "desc": "1か月先の天気傾向（毎週木曜14時発表）",
        "term": "P1M",
    },
    "3m": {
        "name": "3か月予報",
        "desc": "3か月先の気温・降水量（毎月25日頃14時発表）",
        "term": "P3M",
    },
    "warm": {
        "name": "暖候期予報",
        "desc": "6〜8月の気温・降水量（2月下旬発表）",
        "term": "P6M",
    },
    "cold": {
        "name": "寒候期予報",
        "desc": "12〜2月の気温・降雪量（9月下旬発表）",
        "term": "P6M",
    },
}

# URL テンプレート
KAISETSU_BASE = "https://www.data.jma.go.jp/cpd/longfcst/kaisetsu/"
BOSAI_BASE = "https://www.jma.go.jp/bosai/season"

# bosai JSON API の候補エンドポイント
BOSAI_JSON_CANDIDATES = [
    "{base}/data/season/{region}.json",
    "{base}/data/overview/{region}.json",
]


def kaisetsu_url(term: str, region: str = REGION_CODE) -> str:
    return f"{KAISETSU_BASE}?region={region}&term={term}"


def bosai_pdf_url(term: str, region: str = REGION_CODE) -> str:
    return f"{BOSAI_BASE}/data/pdf/{term}/{region}.pdf"


# ─────────────────────────────────────────────────────────────────
# HTTP セッション
# ─────────────────────────────────────────────────────────────────
def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


# ─────────────────────────────────────────────────────────────────
# HTML 取得・解析
# ─────────────────────────────────────────────────────────────────
def fetch_soup(
    session: requests.Session, url: str, debug: bool = False
) -> Optional[BeautifulSoup]:
    if debug:
        print(f"  [GET] {url}", file=sys.stderr)
    try:
        resp = session.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        # エンコーディング自動検出（Shift-JIS / EUC-JP ページにも対応）
        enc = resp.encoding or ""
        if enc.lower().replace("-", "") in ("utf8", "utf-8"):
            html = resp.text
        else:
            html = resp.content.decode(
                resp.apparent_encoding or "utf-8", errors="replace"
            )
        return BeautifulSoup(html, "html.parser")
    except requests.RequestException as e:
        if debug:
            print(f"  [FAIL] {e}", file=sys.stderr)
        return None


def extract_forecast(soup: BeautifulSoup, forecast_name: str) -> dict:
    """
    data.jma.go.jp/cpd/longfcst/kaisetsu/ のページから予報テキストを抽出する。
    ページ構成の変化に対応できるよう、優先順位付きで複数のセレクタを試みる。
    """
    result: dict = {
        "name": forecast_name,
        "issued": None,
        "period": None,
        "tables": [],
        "paragraphs": [],
    }

    # ── 発表日時を検索 ──────────────────────────────────
    for node in soup.find_all(string=True):
        text = node.strip()
        if "発表" in text and 5 < len(text) < 100:
            result["issued"] = text
            break

    # ── 予報期間を検索 ──────────────────────────────────
    for node in soup.find_all(string=True):
        text = node.strip()
        if "予報期間" in text or ("年" in text and "月" in text and "日" in text and "〜" in text):
            if len(text) < 80:
                result["period"] = text
                break

    # ── メインコンテンツ領域を特定 ──────────────────────
    main = (
        soup.find(id="main")
        or soup.find(id="contents")
        or soup.find(id="content")
        or soup.find(id="wrapper")
        or soup.find("main")
        or soup.find("article")
        or soup.find("body")
    )
    if not main:
        return result

    # ── 確率予報テーブルを抽出 ──────────────────────────
    for table in main.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [
                td.get_text(" ", strip=True)
                for td in tr.find_all(["th", "td"])
                if td.get_text(strip=True)
            ]
            if cells:
                rows.append("  │  ".join(cells))
        if rows:
            result["tables"].append("\n".join(rows))

    # ── 解説テキスト段落を抽出 ──────────────────────────
    seen: set = set()
    for tag in main.find_all(["p", "div", "li", "dd", "h2", "h3", "h4"]):
        # 子にブロック要素を含まないリーフノードのみ対象
        if tag.find(["p", "div", "table", "ul", "ol"]):
            continue
        text = tag.get_text(" ", strip=True)
        if len(text) >= 15 and text not in seen:
            seen.add(text)
            result["paragraphs"].append(text)

    return result


# ─────────────────────────────────────────────────────────────────
# bosai JSON API（試行）
# ─────────────────────────────────────────────────────────────────
def fetch_bosai_json(
    session: requests.Session, region: str = REGION_CODE, debug: bool = False
) -> Optional[dict]:
    """bosai/season の JSON API からデータ取得を試みる（成功すれば dict を返す）"""
    for tmpl in BOSAI_JSON_CANDIDATES:
        url = tmpl.format(base=BOSAI_BASE, region=region)
        if debug:
            print(f"  [JSON試行] {url}", file=sys.stderr)
        try:
            resp = session.get(url, timeout=TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                if debug:
                    print(f"  [JSON成功] キー: {list(data.keys())}", file=sys.stderr)
                return data
        except Exception:
            continue
    return None


def format_bosai_json(data: dict, forecast_name: str) -> dict:
    """bosai JSON レスポンスを共通辞書形式に変換する"""
    result: dict = {
        "name": forecast_name,
        "issued": data.get("reportDatetime") or data.get("publishingOffice"),
        "period": None,
        "tables": [],
        "paragraphs": [],
    }
    # テキストフィールドを探して paragraphs に追加
    def _walk(obj: object, depth: int = 0) -> None:
        if depth > 6:
            return
        if isinstance(obj, str) and len(obj) >= 15:
            result["paragraphs"].append(obj)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item, depth + 1)
        elif isinstance(obj, dict):
            for val in obj.values():
                _walk(val, depth + 1)

    _walk(data)
    # 重複除去
    seen: set = set()
    unique: list = []
    for p in result["paragraphs"]:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    result["paragraphs"] = unique
    return result


# ─────────────────────────────────────────────────────────────────
# PDF フォールバック（pdfplumber が必要）
# ─────────────────────────────────────────────────────────────────
def fetch_pdf_text(
    session: requests.Session, url: str, debug: bool = False
) -> Optional[str]:
    try:
        import pdfplumber  # noqa: PLC0415
    except ImportError:
        if debug:
            print("  [PDF] pdfplumber 未インストール（pip install pdfplumber）", file=sys.stderr)
        return None

    if debug:
        print(f"  [PDF] {url}", file=sys.stderr)
    try:
        resp = session.get(url, timeout=60)
        resp.raise_for_status()
        pages_text: list[str] = []
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages_text.append(text)
        return "\n\n".join(pages_text)
    except Exception as e:
        if debug:
            print(f"  [PDF失敗] {e}", file=sys.stderr)
        return None


# ─────────────────────────────────────────────────────────────────
# 出力フォーマット
# ─────────────────────────────────────────────────────────────────
SEP_WIDE = "=" * 65
SEP_THIN = "-" * 65


def print_result(ftype: dict, data: dict) -> None:
    print(SEP_WIDE)
    print(f"  【{data['name']}】")
    print(f"  {ftype['desc']}")
    print(SEP_THIN)

    if data.get("issued"):
        print(f"  発表: {data['issued']}")
    if data.get("period"):
        print(f"  期間: {data['period']}")

    has_content = data.get("tables") or data.get("paragraphs")
    if not has_content:
        print()
        print("  ※ データを取得できませんでした。")
        print(f"  参照URL: {kaisetsu_url(ftype['term'])}")
        print()
        return

    # 確率予報テーブル
    for tbl in data.get("tables", []):
        print()
        for line in tbl.splitlines():
            print(f"  {line}")

    # 解説テキスト
    if data.get("paragraphs"):
        print()
        for para in data["paragraphs"]:
            wrapped = textwrap.fill(
                para, width=68, initial_indent="  ", subsequent_indent="  "
            )
            print(wrapped)

    print()


def print_raw(ftype: dict, text: str) -> None:
    print(SEP_WIDE)
    print(f"  【{ftype['name']}】")
    print(f"  {ftype['desc']}")
    print(SEP_THIN)
    for line in text.splitlines():
        print(f"  {line}")
    print()


# ─────────────────────────────────────────────────────────────────
# メイン
# ─────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="気象庁 長期予報テキスト取得（関東甲信地方）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            例:
              python jma_longfcst.py           # 1か月・3か月・暖候期・寒候期すべて取得
              python jma_longfcst.py --1m --3m # 1か月と3か月のみ
              python jma_longfcst.py --warm    # 暖候期予報のみ
              python jma_longfcst.py --pdf     # PDFから取得 (pdfplumber 必須)
              python jma_longfcst.py --debug   # デバッグ情報も表示
            """
        ),
    )
    parser.add_argument("--1m",    dest="one",   action="store_true", help="1か月予報")
    parser.add_argument("--3m",    dest="three", action="store_true", help="3か月予報")
    parser.add_argument("--warm",  action="store_true",               help="暖候期予報")
    parser.add_argument("--cold",  action="store_true",               help="寒候期予報")
    parser.add_argument("--pdf",   action="store_true",               help="PDFフォールバック優先")
    parser.add_argument("--debug", action="store_true",               help="デバッグ出力")
    args = parser.parse_args()

    # 種別が未指定なら全取得
    all_selected = not any([args.one, args.three, args.warm, args.cold])
    keys: list[str] = []
    if all_selected or args.one:   keys.append("1m")
    if all_selected or args.three: keys.append("3m")
    if all_selected or args.warm:  keys.append("warm")
    if all_selected or args.cold:  keys.append("cold")

    print(f"\n気象庁 長期予報  ―  {REGION_NAME}")
    print(f"取得日時: {datetime.now().strftime('%Y年%m月%d日 %H:%M')}\n")

    session = make_session()

    # bosai JSON API を一度だけ試みる
    bosai_json: Optional[dict] = None
    if not args.pdf:
        bosai_json = fetch_bosai_json(session, debug=args.debug)

    # 同一 term の HTML は一度だけ取得してキャッシュ
    soup_cache: dict[str, Optional[BeautifulSoup]] = {}

    for key in keys:
        ftype = FORECAST_TYPES[key]
        term = ftype["term"]

        print(f">>> {ftype['name']} を取得中 ...", file=sys.stderr)

        # ── PDF 優先モード ──────────────────────────────
        if args.pdf:
            text = fetch_pdf_text(session, bosai_pdf_url(term), debug=args.debug)
            if text:
                print_raw(ftype, text)
                continue
            # PDF 失敗 → HTML へフォールバック

        # ── bosai JSON ──────────────────────────────────
        if bosai_json:
            data = format_bosai_json(bosai_json, ftype["name"])
            if data["paragraphs"] or data["tables"]:
                print_result(ftype, data)
                continue

        # ── HTML スクレイピング ─────────────────────────
        if term not in soup_cache:
            soup_cache[term] = fetch_soup(session, kaisetsu_url(term), debug=args.debug)
        soup = soup_cache[term]
        if soup:
            data = extract_forecast(soup, ftype["name"])
            if data["paragraphs"] or data["tables"]:
                print_result(ftype, data)
                continue

        # ── PDF フォールバック ──────────────────────────
        text = fetch_pdf_text(session, bosai_pdf_url(term), debug=args.debug)
        if text:
            print_raw(ftype, text)
            continue

        # ── すべて失敗 ──────────────────────────────────
        print_result(ftype, {"name": ftype["name"], "tables": [], "paragraphs": []})


if __name__ == "__main__":
    main()
