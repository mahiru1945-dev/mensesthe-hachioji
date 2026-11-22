"""回春エステ(風俗エステ・性感マッサージ)の一覧を作る。update.py から呼ぶ。

情報元:
  - 風俗じゃぱん  八王子市の回春性感マッサージ一覧
  - ランキングデリ 八王子エリア一覧
  - シティヘブン  八王子 風俗エステ一覧(機械からのアクセスを拒否されることが多いので、取れたら使う)

八王子のページには新宿・五反田などから派遣してくる店も多く載るので、
拠点が八王子・立川周辺の店に local=True を付ける。
"""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

FUZOKU_LIST = "https://fuzoku.jp/tokyo/city182/ge_40/"
RDELI_LIST = "https://ranking-deli.jp/tokyo/local182/genre1/"
HEAVEN_LIST = "https://www.cityheaven.net/tokyo/A1330/A133004/shop-list/biz7/"

LOCAL_WORDS = re.compile(r"八王子|立川|日野|豊田|西八王子|京王堀之内|南大沢|拝島|昭島|国立|府中|多摩")
RDELI_LOCAL_AREAS = {"220", "39"}  # ランキングデリのエリア番号: 220=八王子, 39=立川

TIME_RE = re.compile(r"(\d{1,2}:\d{2})\s*[-〜~～]\s*(翌?\d{1,2}:\d{2}|LAST|ラスト)")
PRICE_RE = re.compile(r"(\d{2,3})分\s*[¥￥]?\s*(\d{1,3}(?:,\d{3})+|\d{4,6})\s*円?")
TEL_RE = re.compile(r"0\d{1,4}-\d{1,4}-\d{3,4}")


def _block(a, stop_words=("出勤", "電話", "詳細")):
    """店1件ぶんの囲み要素まで遡る。別の店へのリンクを含む親には上がらない。"""
    own = a["href"]
    blk = a
    for _ in range(7):
        par = blk.parent
        if par is None:
            break
        others = {x["href"] for x in par.find_all("a", href=True) if x is not a}
        shopish = {h for h in others if h != own and re.search(r"^/[a-z0-9_\-]+/$|/area\d+/style\d+/\d+/$|/A\d+/A\d+/[a-z0-9_\-]+/$", h)}
        if shopish:
            break
        blk = par
        t = blk.get_text(" ", strip=True)
        if len(t) > 30 and any(w in t for w in stop_words) and TEL_RE.search(t):
            break
    return blk


def _row(name, url, text, src):
    t = TIME_RE.search(text)
    p = PRICE_RE.search(text)
    tel = TEL_RE.search(text)
    today = re.search(r"(?:本日出勤|出勤)\s*(\d+)\s*(?:人|名)", text)
    kind = "店舗型" if re.search(r"店舗型|受付型|店舗・受付", text) else ("派遣型" if re.search(r"派遣|デリヘル|発", text) else "")
    base = re.search(r"[（(]([^（）()]{1,20}?)発[)）]", text)
    paren = " ".join(re.findall(r"[（(]([^（）()]{1,25})[)）]", text[:250]))
    return {
        "name": name, "page": url, "src": src, "text": text[:400],
        "hours": f"{t.group(1)}～{t.group(2)}" if t else "",
        "price": f"{p.group(1)}分{p.group(2)}円～" if p else "",
        "phone": tel.group(0) if tel else "",
        "today": int(today.group(1)) if today else None,
        "kind": kind,
        "base": (base.group(1) + "発") if base else "",
        "paren": paren,
    }


def scrape_fuzoku(get) -> list[dict]:
    r = get(FUZOKU_LIST)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = urljoin(FUZOKU_LIST, a["href"])
        m = re.fullmatch(r"https://fuzoku\.jp/([a-z0-9_\-]+)/", href)
        name = a.get_text(" ", strip=True)
        if not m or m.group(1) in ("tokyo", "kanagawa", "saitama", "chiba") or not name or len(name) > 60 or href in seen \
                or re.search(r"詳細|もっと|一覧|予約|求人|クーポン|地図|マイページ|ログイン|会員", name):
            continue
        blk = _block(a)
        text = blk.get_text(" ", strip=True)
        if not TEL_RE.search(text) and "出勤" not in text:
            continue
        seen.add(href)
        out.append(_row(name, href, text, "風俗じゃぱん"))
    return out


def scrape_rdeli(get) -> list[dict]:
    r = get(RDELI_LIST)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = urljoin(RDELI_LIST, a["href"])
        m = re.fullmatch(r"https://ranking-deli\.jp/tokyo/area(\d+)/style(\d+)/(\d+)/", href)
        name = a.get_text(" ", strip=True)
        if not m or not name or len(name) > 70 or href in seen or re.search(r"詳細|もっと|一覧|予約|求人|クーポン|地図", name):
            continue
        blk = _block(a)
        text = blk.get_text(" ", strip=True)
        seen.add(href)
        row = _row(name, href, text, "ランキングデリ")
        row["area_no"] = m.group(1)
        row["kind"] = "派遣型" if m.group(2) in ("2", "7") else row["kind"]
        out.append(row)
    return out


def scrape_heaven() -> list[dict]:
    """シティヘブンはブラウザでないと開けないことが多いので Playwright で試す。"""
    from playwright.sync_api import sync_playwright

    out = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(locale="ja-JP")
        pg.goto(HEAVEN_LIST, wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_timeout(2500)
        html = pg.content()
        b.close()
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(HEAVEN_LIST, a["href"])
        m = re.fullmatch(r"https://www\.cityheaven\.net/tokyo/A\d+/A\d+/([a-z0-9_\-]+)/", href)
        name = a.get_text(" ", strip=True)
        if not m or m.group(1) in ("shop-list", "girl-list", "attend") or not name or len(name) > 60 or href in seen:
            continue
        blk = _block(a)
        seen.add(href)
        out.append(_row(name, href, blk.get_text(" ", strip=True), "シティヘブン"))
    return out


HP_WORDS = re.compile(r"公式|オフィシャル|ホームページ|\bHP\b|Official", re.I)
ROW_WORDS = re.compile(r"ホームページ|^\s*URL\s*$|公式|オフィシャル|^\s*HP\s*$", re.I)
AGGREGATORS = ("fuzoku.jp", "ranking-deli.jp", "cityheaven.net", "dto.jp", "fujoho.jp", "twitter.com", "x.com",
               "line.me", "instagram.com", "google.", "yahoo.", "qzin", "kaikatsu", "girlsheaven", "yoasobi")


def find_hp(get, page: str) -> str:
    try:
        soup = BeautifulSoup(get(page, timeout=20).text, "html.parser")
    except Exception:  # noqa: BLE001
        return ""
    def external(a):
        href = urljoin(page, a.get("href", ""))
        net = urlparse(href).netloc
        return href if href.startswith("http") and net and not any(x in net for x in AGGREGATORS) else ""

    # 1) 店舗情報の表で「ホームページ」「URL」欄にあるリンク
    for row in soup.select("tr, dl"):
        head = row.find(["th", "dt"])
        if head and ROW_WORDS.search(head.get_text(" ", strip=True)):
            for a in row.find_all("a", href=True):
                if external(a):
                    return external(a)
    # 2) 「HP」「公式」などと書かれたリンク
    for a in soup.find_all("a", href=True):
        label = a.get_text(" ", strip=True) + " " + (a.get("title") or "") + " " + " ".join(i.get("alt", "") for i in a.find_all("img"))
        if external(a) and HP_WORDS.search(label):
            return external(a)
    return ""


def norm_name(n: str) -> str:
    n = re.sub(r"[（(].*?[)）]", "", n)
    return re.sub(r"[\s・~～〜!！☆★\-]", "", n).lower()


def merge(ctx, shops: list[dict]) -> None:
    get = ctx.polite_get
    rows: list[dict] = []
    for fn, label in ((scrape_fuzoku, "fuzoku"), (scrape_rdeli, "rdeli")):
        try:
            got = fn(get)
            ctx.log["steps"][f"kaishun_{label}"] = len(got)
            rows += got
        except Exception as e:  # noqa: BLE001
            ctx.log["errors"].append(f"回春 {label} 一覧の取得失敗: {e}")
    try:
        got = scrape_heaven()
        ctx.log["steps"]["kaishun_heaven"] = len(got)
        rows += got
    except Exception as e:  # noqa: BLE001
        ctx.log["errors"].append(f"回春 シティヘブン一覧の取得失敗(拒否されることが多い): {type(e).__name__}")

    by_page = {p: s for s in shops for p in s.get("pages", {}).values()}
    by_phone = {ctx.norm_phone(s["phone"]): s for s in shops if ctx.norm_phone(s.get("phone"))}
    by_name = {norm_name(s["name"]): s for s in shops}
    added = 0
    for row in rows:
        s = by_page.get(row["page"]) or by_phone.get(ctx.norm_phone(row["phone"])) or by_name.get(norm_name(row["name"]))
        if s is None:
            s = ctx.new_shop(name=row["name"])
            s["id"] = "k-" + ctx.hashlib.md5(row["page"].encode()).hexdigest()[:8]
            s["pages"] = {}
            s["type"] = "回春エステ"
            shops.append(s)
            added += 1
        s.setdefault("pages", {})[row["src"]] = row["page"]
        if row["src"] not in s["src"]:
            s["src"].append(row["src"])
        s["last_listed"] = ctx.TODAY_STR
        for k in ("hours", "price"):
            if row.get(k):
                s[k] = row[k]
        for k in ("phone", "kind"):
            if row.get(k) and not s.get(k):
                s[k] = row[k]
        if row["today"] is not None:
            s["today"] = row["today"]
        if row.get("base"):
            s["base"] = row["base"]
        # 地元発: 店名・発着表記・ランキングデリのエリア番号のどれかが八王子/立川周辺
        if row["src"] == "ランキングデリ":
            local = row.get("area_no") in RDELI_LOCAL_AREAS
        else:
            local = bool(LOCAL_WORDS.search(row.get("base") or row.get("paren") or ""))
        local = local or bool(LOCAL_WORDS.search(row["name"]))
        s.setdefault("local_votes", {})[row["src"]] = local
        s["local"] = any(s["local_votes"].values())
        s["station"] = s.get("base") or ("八王子・立川周辺" if s["local"] else "都内から派遣")
        by_page[row["page"]] = s
        by_name[norm_name(row["name"])] = s
        if ctx.norm_phone(row["phone"]):
            by_phone[ctx.norm_phone(row["phone"])] = s
    # 公式HPは、まだ無い店だけ店舗ページから探す(地元の店を優先)
    for s in sorted(shops, key=lambda x: not x.get("local")):
        if s.get("hp") or s.get("hp_tried") == ctx.TODAY_STR:
            continue
        for page in s.get("pages", {}).values():
            hp = find_hp(get, page)
            if hp:
                s["hp"] = hp
                break
        s["hp_tried"] = ctx.TODAY_STR
    ctx.log["steps"]["kaishun_added"] = added


# ---------------------------------------------------------------- 出勤

JS_CARDS = r"""
() => {
  // 写真1枚ごとに、年齢(NN)が1人ぶんだけ入る一番外側の囲みまで遡る
  const AGE = /[（(]\d{2}[)）]/g;
  const out = [], seen = new Set();
  for (const img of document.querySelectorAll("img")) {
    let el = img, best = null;
    for (let i = 0; i < 8 && el.parentElement; i++) {
      el = el.parentElement;
      const t = el.innerText || "";
      const n = (t.match(AGE) || []).length;
      if (n > 1 || t.length > 600) break;
      if (n === 1) best = el;
    }
    if (!best || seen.has(best)) continue;
    seen.add(best);
    const a = best.closest("a") || best.querySelector("a[href*='girl'], a");
    const src = img.currentSrc || img.getAttribute("data-src") || img.getAttribute("data-original") || img.src;
    out.push({ text: best.innerText, img: src && !src.startsWith("data:") ? src : "", href: a ? a.href : location.href });
  }
  return out;
}
"""


def state_of(text: str) -> str:
    if re.search(r"受付終了|本日終了|退勤", text):
        return "ended"
    if re.search(r"案内中|接客中|予約満了|満了", text):
        return "busy"
    if re.search(r"待機中|即案内|すぐ案内|案内可|今すぐ", text):
        return "now"
    if re.search(r"次回|出勤予定", text):
        return "later"
    return ""


def scrape_today(ctx, shops: list[dict]) -> list[dict]:
    from playwright.sync_api import sync_playwright

    out = []
    targets = [s for s in shops if s.get("local") and s.get("status") != "閉店"]
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(user_agent=ctx.UA, locale="ja-JP", viewport={"width": 1280, "height": 900})
        for s in targets:
            for src, page in s.get("pages", {}).items():
                if src == "シティヘブン":
                    continue
                url = page.rstrip("/") + "/schedule/"
                try:
                    pg.goto(url, wait_until="domcontentloaded", timeout=40000)
                    for _ in range(25):
                        pg.mouse.wheel(0, 1500)
                        pg.wait_for_timeout(200)
                    pg.wait_for_timeout(1200)
                    cards = pg.evaluate(JS_CARDS)
                except Exception as e:  # noqa: BLE001
                    ctx.log["errors"].append(f"回春 出勤取得失敗 {s['name']}: {type(e).__name__}")
                    continue
                got = 0
                for c in cards:
                    text = re.sub(r"\s+", " ", c["text"])
                    m = re.search(r"([^\s()（）]{1,12})\s*[（(](\d{2})[)）]", text)
                    t = TIME_RE.search(text)
                    if not m:
                        continue
                    st = re.search(r"(\d{1,2}:\d{2})\s*[～〜~]", text)
                    out.append({"shop_id": s["id"], "shop_name": s["name"], "name": m.group(1), "age": int(m.group(2)),
                                "time": f"{t.group(1)}〜{t.group(2)}" if t else (f"{st.group(1)}〜" if st else ""),
                                "state": state_of(text), "now": state_of(text) == "now",
                                "img": c["img"], "url": c["href"] or url})
                    got += 1
                ctx.time.sleep(ctx.WAIT_SAME_SITE)
                if got:
                    break  # 1つのサイトで取れたら十分
        b.close()
    uniq = {(t["shop_id"], t["name"], t["age"]): t for t in out}
    return list(uniq.values())


def run_today(ctx) -> None:
    path = ctx.DATA / "kaishun.json"
    if not path.exists():
        return
    shops = json.loads(path.read_text(encoding="utf-8"))
    th = scrape_today(ctx, shops)
    (ctx.DATA / "kaishun_today.json").write_text(
        json.dumps({"updated": ctx.NOW.isoformat(), "therapists": th}, ensure_ascii=False, indent=1), encoding="utf-8")
    ctx.log["steps"]["kaishun_therapists"] = len(th)


def run(ctx) -> None:
    path = ctx.DATA / "kaishun.json"
    today_path = ctx.DATA / "kaishun_today.json"
    shops = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    merge(ctx, shops)
    ctx.check_all(shops, key="kaishun_hp_check")
    try:
        ctx.browse_all(shops, key="kaishun_browse")
    except Exception as e:  # noqa: BLE001
        ctx.log["errors"].append(f"回春 ブラウザ確認失敗: {e}")
    for s in shops:
        s["tags"] = sorted(set(s.get("tags") or []), key=list(ctx.extras.TAGS).index)
    path.write_text(json.dumps(shops, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        th = scrape_today(ctx, shops)
        today_path.write_text(json.dumps({"updated": ctx.NOW.isoformat(), "therapists": th}, ensure_ascii=False, indent=1),
                              encoding="utf-8")
        ctx.log["steps"]["kaishun_therapists"] = len(th)
    except Exception as e:  # noqa: BLE001
        ctx.log["errors"].append(f"回春 出勤取得失敗: {e}")
    ctx.log["steps"]["kaishun_shops"] = len(shops)
    ctx.log["steps"]["kaishun_local"] = sum(1 for s in shops if s.get("local"))
