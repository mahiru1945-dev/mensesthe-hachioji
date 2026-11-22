"""タグ付け・口コミ要約・注意表示(摘発報道/トラブル口コミ)。update.py から呼ぶ。"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup

# ---------------------------------------------------------------- タグ
# 衣装と施術スタイルだけ。性的な行為を指す言葉は扱わない。
TAGS: dict[str, dict] = {
    "マイクロビキニ": {"group": "衣装", "words": ["マイクロビキニ"]},
    "ベビードール": {"group": "衣装", "words": ["ベビードール"]},
    "ノーブラ": {"group": "衣装", "words": ["ノーブラ"]},
    "ノーパン": {"group": "衣装", "words": ["ノーパン"]},
    "紙パンツ": {"group": "衣装", "words": ["紙パンツ", "紙パン", "横スカ"]},
    "コスプレ": {"group": "衣装", "words": ["コスプレ", "衣装チェンジ", "衣装変更"]},
    "鼠径部": {"group": "施術", "words": ["鼠径部", "そけい部", "鼡径部"]},
    "ディープリンパ": {"group": "施術", "words": ["ディープリンパ"]},
    "密着": {"group": "施術", "words": ["密着"]},
    "マーメイド": {"group": "施術", "words": ["マーメイド", "マーメード"]},
    "カエル足": {"group": "施術", "words": ["カエル足", "カエル脚", "かえる足"]},
    "四つん這い": {"group": "施術", "words": ["四つん這い", "四つんばい"]},
    "フェザータッチ": {"group": "施術", "words": ["フェザータッチ"]},
    "ホイップ": {"group": "施術", "words": ["ホイップ"]},
    "パウダー": {"group": "施術", "words": ["パウダー"]},
    "洗体": {"group": "施術", "words": ["洗体"]},
    "Wセラピ": {"group": "施術", "words": ["Wセラピ", "ダブルセラピ", "W施術"]},
}

SUBPAGE_HINT = re.compile(r"system|price|course|menu|option|料金|コース|システム|オプション", re.I)


def tag_text(text: str) -> list[str]:
    return [name for name, t in TAGS.items() if any(w in text for w in t["words"])]


def collect_hp_text(get, hp_url: str, first_html: str) -> str:
    """トップページと、料金・コースらしきページ1つ分のテキストを返す。"""
    soup = BeautifulSoup(first_html or "", "html.parser")
    texts = [soup.get_text(" ", strip=True)]
    for a in soup.find_all("a", href=True):
        if SUBPAGE_HINT.search(a["href"]) or SUBPAGE_HINT.search(a.get_text()):
            try:
                r = get(urljoin(hp_url, a["href"]), timeout=20)
                texts.append(BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True))
            except Exception:  # noqa: BLE001
                pass
            break
    return " ".join(texts)


# ---------------------------------------------------------------- 口コミ
# 抜粋に出さない言葉(性的サービスの話題を含む口コミは本文を表示しない)
BLOCK = re.compile(
    r"本番|裏オプ|裏OP|裏ＯＰ|HJ|ＨＪ|抜き|ヌキ|NN|NS|SKR|素股|フェラ|手コキ|挿入|射精|発射|"
    r"お触り|おさわり|OSWR|TKB|乳首|おっぱい|股間|勃起|ゴム|生で|85|8585|4545|HB|ハンド",
    re.I,
)
TROUBLE = re.compile(
    r"追加料金|追加で\d|追加請求|請求され|ぼったくり|ボッタクリ|ぼったくられ|時間短縮|時短|短くされ|"
    r"早く終わ|返金|態度が悪|対応が悪|接客が悪|最悪|二度と行かない|地雷|嘘|騙され"
)


def parse_reviews(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select("ol.commentlist > li, ol.comment-list > li") or soup.select("li.comment")
    out = []
    for li in items:
        txt = li.get_text(" ", strip=True)
        m = re.search(r"投稿日\s*(\d{4})年(\d{1,2})月(\d{1,2})日", txt)
        date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None
        body_el = li.select_one(".comment-content, .comment-body, .commentbody") or li
        body = re.sub(r"\s+", " ", body_el.get_text(" ", strip=True))
        body = re.sub(r"口コミは参考になりましたか.*$", "", body)
        body = re.sub(r"^.*?投稿日\s*\d{4}年\d{1,2}月\d{1,2}日", "", body).strip()
        out.append({"date": date, "body": body})
    return out


def summarize_reviews(reviews: list[dict], today: datetime) -> dict:
    dated = [r for r in reviews if r["date"]]
    dated.sort(key=lambda r: r["date"], reverse=True)
    snippets = []
    for r in dated:
        if len(snippets) >= 2:
            break
        if r["body"] and not BLOCK.search(r["body"]):
            snippets.append({"date": r["date"], "text": r["body"][:110] + ("…" if len(r["body"]) > 110 else "")})
    trouble = []
    for r in dated:
        age = (today.date() - datetime.strptime(r["date"], "%Y-%m-%d").date()).days
        if age > 365:
            continue
        m = TROUBLE.search(r["body"])
        if not m:
            continue
        s = max(0, m.start() - 35)
        win = r["body"][s:m.end() + 35]
        trouble.append({"date": r["date"], "word": m.group(0), "text": None if BLOCK.search(win) else ("…" + win + "…")})
    return {
        "count": len(reviews),
        "last": dated[0]["date"] if dated else None,
        "recent_1y": sum(1 for r in dated if (today.date() - datetime.strptime(r["date"], "%Y-%m-%d").date()).days <= 365),
        "snippets": snippets,
        "trouble": trouble[:5],
        "trouble_flag": len(trouble) >= 2,
    }


# ---------------------------------------------------------------- 摘発報道
NEWS_QUERIES = [
    "メンズエステ 八王子 逮捕", "メンズエステ 八王子 摘発",
    "メンズエステ 日野 逮捕", "メンズエステ 京王八王子 逮捕",
]


def core_name(name: str) -> list[str]:
    """店名から検索用の名前を作る。例: 'Aroma Rose（アロマローズ）' -> ['Aroma Rose', 'アロマローズ']"""
    out = []
    for part in re.split(r"[（）()～〜]", name):
        p = re.sub(r"(八王子|西八王子|日野|京王).*$", "", part).strip(" ・☆")
        p = re.sub(r"(ルーム|店|ほか)$", "", p).strip()
        if len(p) >= 3:
            out.append(p)
    return out


def fetch_news(get) -> list[dict]:
    items = []
    for q in NEWS_QUERIES:
        url = f"https://news.google.com/rss/search?q={quote(q)}&hl=ja&gl=JP&ceid=JP:ja"
        try:
            root = ET.fromstring(get(url, timeout=20).content)
        except Exception:  # noqa: BLE001
            continue
        for it in root.iter("item"):
            items.append({
                "title": it.findtext("title") or "",
                "url": it.findtext("link") or "",
                "date": it.findtext("pubDate") or "",
                "desc": BeautifulSoup(it.findtext("description") or "", "html.parser").get_text(" "),
            })
    uniq = {i["url"]: i for i in items}
    return list(uniq.values())


def match_news(shops: list[dict], news: list[dict]) -> int:
    hits = 0
    for s in shops:
        names = core_name(s["name"])
        found = s.get("raids") or []
        known = {r["url"] for r in found}
        for n in news:
            text = n["title"] + " " + n["desc"]
            if not re.search(r"逮捕|摘発|容疑|書類送検", text):
                continue
            if any(nm in text for nm in names) and n["url"] not in known:
                found.append({"title": n["title"], "url": n["url"], "date": n["date"]})
                hits += 1
        s["raids"] = found
    return hits
