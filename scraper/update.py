"""八王子・立川エリアのメンズエステ一覧を毎日更新するスクリプト。

やること:
  1. まとめサイト(エステ魂ランキング / ゴーメンズエステ)から店舗一覧を取り直し、新店を追加する
  2. 各店の公式HPにアクセスし、つながらなければ閉店の疑いとして記録する
  3. エステ魂ランキングの「本日出勤」ページを本物のブラウザで開き、出勤中の女の子の写真を集める

結果は docs/data/shops.json と docs/data/today.json に書き出す。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extras  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
SHOPS = DATA / "shops.json"
TODAY = DATA / "today.json"
LOG = DATA / "last_run.json"
NEWS = DATA / "news.json"

JST = timezone(timedelta(hours=9))
NOW = datetime.now(JST)
TODAY_STR = NOW.strftime("%Y-%m-%d")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"}

# 同じサイトに連続アクセスしないための待ち時間(秒)
WAIT_SAME_SITE = 3.0
# この回数続けてHPにつながらなかったら「閉店」にする
CLOSE_AFTER = 3

AREAS = [
    {"area": "八王子", "station": "八王子・京王八王子",
     "er": "https://www.esthe-ranking.jp/hachioji/ippan/",
     "schedule": "https://www.esthe-ranking.jp/schedule/hachioji/ippan/",
     "go": "https://go-mensesthe.net/category/chuo/%e5%85%ab%e7%8e%8b%e5%ad%90/"},
    {"area": "立川", "station": "立川",
     "er": "https://www.esthe-ranking.jp/tachikawa/ippan/",
     "schedule": "https://www.esthe-ranking.jp/schedule/tachikawa/ippan/",
     "go": "https://go-mensesthe.net/category/chuo/%e7%ab%8b%e5%b7%9d/"},
]
SHOTS = ROOT / "docs" / "shots"

log: dict = {"started": NOW.isoformat(), "steps": {}, "errors": []}
_last_hit: dict[str, float] = {}


def polite_get(url: str, **kw) -> requests.Response:
    host = urlparse(url).netloc
    wait = WAIT_SAME_SITE - (time.time() - _last_hit.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    try:
        return requests.get(url, headers=HEADERS, timeout=kw.pop("timeout", 25), **kw)
    finally:
        _last_hit[host] = time.time()


def norm_phone(p: str | None) -> str:
    return re.sub(r"\D", "", p or "")


def er_uuid(url: str) -> str | None:
    m = re.search(r"/(?:shop-detail|therapist)/([0-9a-f-]{36})", url or "")
    return m.group(1) if m else None


def make_id(er: str = "", go: str = "", name: str = "") -> str:
    u = er_uuid(er)
    if u:
        return "er-" + u[:8]
    if go:
        return "go-" + hashlib.md5(go.encode()).hexdigest()[:8]
    return "x-" + hashlib.md5(name.encode()).hexdigest()[:8]


# ---------------------------------------------------------------- 1. 店舗一覧

def scrape_er_list(ER_LIST: str) -> list[dict]:
    """エステ魂ランキングのエリア別メンズエステ一覧。"""
    r = polite_get(ER_LIST)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out, seen = [], set()
    for a in soup.select('h3 a[href*="/shop-detail/"]'):
        href = urljoin(ER_LIST, a["href"])
        u = er_uuid(href)
        if not u or u in seen:
            continue
        seen.add(u)
        # 店舗ブロック(「本日出勤」等を含む親要素)まで遡る
        block = a
        for _ in range(8):
            if block.parent is None:
                break
            block = block.parent
            if "電話をかける" in block.get_text() or "セラピスト" in block.get_text():
                break
        text = block.get_text(" ", strip=True)
        today = re.search(r"本日出勤\s*(\d+)\s*名", text)
        ther = re.search(r"セラピスト\s*(\d+)\s*名", text)
        price = re.search(r"(\d+)分\s*⁄?\s*([\d,]+)円", text)
        tel = block.select_one('a[href^="tel:"]')
        items = [li.get_text(" ", strip=True) for li in block.select("li")][:4]
        out.append({
            "er": href,
            "name": a.get_text(strip=True),
            "station": items[0] if items else "",
            "hours": items[1] if len(items) > 1 else "",
            "price": f"{price.group(1)}分{price.group(2)}円～" if price else "",
            "phone": tel["href"].replace("tel:", "") if tel else "",
            "today": int(today.group(1)) if today else None,
            "ther": int(ther.group(1)) if ther else None,
            "tags": extras.tag_text(text),
        })
    return out


def scrape_go_list(GO_LIST: str) -> list[dict]:
    """ゴーメンズエステのエリア別一覧(表形式)。"""
    r = polite_get(GO_LIST)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for tr in soup.select("table tr"):
        tds = tr.find_all("td")
        if len(tds) < 8:
            continue
        name_a = tds[0].find("a")
        hp_a = tds[1].find("a")
        if not name_a:
            continue
        tags = tds[4].get_text(" ", strip=True)
        rev = re.search(r"(\d+)\s*件", tds[7].get_text())
        out.append({
            "go": urljoin(GO_LIST, name_a["href"]),
            "name": name_a.get_text(strip=True),
            "hp": hp_a["href"] if hp_a else "",
            "phone": tds[2].get_text(strip=True),
            "type": "アジアン" if "アジアン" in tags else ("日本人" if "日本人" in tags else ""),
            "hours": tds[5].get_text(strip=True),
            "price": tds[6].get_text(strip=True),
            "rev": int(rev.group(1)) if rev else 0,
        })
    return out


def find_er_official_hp(er_url: str) -> str:
    """エステ魂ランキングの店舗ページから「オフィシャルHP」のリンク先を取る。"""
    try:
        r = polite_get(er_url)
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            if "オフィシャル" in a.get_text() and "esthe-ranking.jp" not in a["href"]:
                return a["href"]
    except Exception as e:  # noqa: BLE001
        log["errors"].append(f"HP取得失敗 {er_url}: {e}")
    return ""


def merge_lists(shops: list[dict], A: dict) -> None:
    by_er = {er_uuid(s["er"]): s for s in shops if s.get("er")}
    by_go = {s["go"]: s for s in shops if s.get("go")}
    by_phone = {norm_phone(s["phone"]): s for s in shops if norm_phone(s.get("phone"))}
    added = 0

    def touch(s: dict, src: str) -> None:
        s["last_listed"] = TODAY_STR
        if src not in s["src"]:
            s["src"].append(src)

    try:
        er_rows = scrape_er_list(A["er"])
        log["steps"][f"er_list_{A['area']}"] = len(er_rows)
    except Exception as e:  # noqa: BLE001
        er_rows = []
        log["errors"].append(f"エステ魂一覧の取得失敗: {e}")
    for row in er_rows:
        u = er_uuid(row["er"])
        s = by_er.get(u) or by_phone.get(norm_phone(row["phone"]))
        if s is None:
            s = new_shop(er=row["er"], name=row["name"])
            s["hp"] = find_er_official_hp(row["er"])
            s["area"] = A["area"]
            shops.append(s)
            added += 1
        s["er"] = s.get("er") or row["er"]
        for k in ("today", "ther"):
            s[k] = row[k]
        s["list_tags"] = row["tags"]
        for k in ("station", "hours", "price", "phone"):
            if row[k] and not s.get(k):
                s[k] = row[k]
        if not s.get("type"):
            s["type"] = "日本人"
        touch(s, "エステ魂ランキング")
        by_er[u] = s
        if norm_phone(row["phone"]):
            by_phone[norm_phone(row["phone"])] = s

    try:
        go_rows = scrape_go_list(A["go"])
        log["steps"][f"go_list_{A['area']}"] = len(go_rows)
    except Exception as e:  # noqa: BLE001
        go_rows = []
        log["errors"].append(f"ゴーメンズエステ一覧の取得失敗: {e}")
    for row in go_rows:
        s = by_go.get(row["go"]) or by_phone.get(norm_phone(row["phone"]))
        if s is None:
            s = new_shop(go=row["go"], name=row["name"])
            s["area"] = A["area"]
            shops.append(s)
            added += 1
        s["go"] = s.get("go") or row["go"]
        s["rev"] = row["rev"]
        for k in ("hp", "phone", "type", "hours", "price"):
            if row[k] and not s.get(k):
                s[k] = row[k]
        if not s.get("station"):
            s["station"] = A["station"]
        touch(s, "ゴーメンズエステ")
        by_go[row["go"]] = s

    log["steps"][f"added_{A['area']}"] = added


def new_shop(er: str = "", go: str = "", name: str = "") -> dict:
    return {
        "id": make_id(er, go, name), "name": name, "type": "", "station": "", "hours": "",
        "price": "", "phone": "", "hp": "", "er": er, "go": go, "rev": 0, "ther": None,
        "today": None, "src": [], "note": "新着", "status": "未チェック", "fail_count": 0,
        "last_check": None, "last_ok": None, "http": None, "first_seen": TODAY_STR,
        "last_listed": TODAY_STR,
    }


# ---------------------------------------------------------------- 2. 閉店チェック

def check_hp(url: str) -> tuple[bool | None, str, str]:
    """True=つながった / False=つながらない / None=判断できない(ボット拒否など)。"""
    if not url:
        return None, "HPなし", ""
    try:
        r = polite_get(url, allow_redirects=True, timeout=20)
    except requests.exceptions.SSLError:
        try:
            r = requests.get(url, headers=HEADERS, timeout=20, verify=False)
        except Exception as e:  # noqa: BLE001
            return False, f"接続できない({type(e).__name__})", ""
    except requests.exceptions.ConnectionError:
        return False, "接続できない(ドメイン消失の可能性)", ""
    except requests.exceptions.Timeout:
        return None, "タイムアウト", ""
    except Exception as e:  # noqa: BLE001
        return None, type(e).__name__, ""
    if r.status_code in (404, 410):
        return False, f"HTTP {r.status_code}", ""
    if r.status_code in (401, 403, 429) or r.status_code >= 500:
        return None, f"HTTP {r.status_code}", ""
    body = r.text[:20000]
    if re.search(r"閉店いたしました|閉店しました|営業を終了|サービスを終了|このドメインは|ドメインの有効期限", body):
        return False, "閉店・ドメイン切れの表示", ""
    r.encoding = r.apparent_encoding or r.encoding
    return True, f"HTTP {r.status_code}", r.text


def check_all(shops: list[dict], key: str = "hp_check") -> None:
    import urllib3

    urllib3.disable_warnings()
    ok = ng = unk = 0
    # 同じホストが続かないよう、ホスト名で並べ替えて順番にアクセス
    for s in sorted(shops, key=lambda x: urlparse(x.get("hp") or "").netloc):
        alive, why, html = check_hp(s.get("hp", ""))
        if alive:
            try:
                text = extras.collect_hp_text(polite_get, s["hp"], html)
                s["tags"] = extras.tag_text(text)
            except Exception as e:  # noqa: BLE001
                log["errors"].append(f"タグ付け失敗 {s['name']}: {e}")
        s["last_check"] = TODAY_STR
        s["http"] = why
        if alive is True:
            ok += 1
            s["fail_count"] = 0
            s["last_ok"] = TODAY_STR
            s["status"] = "営業中"
        elif alive is False:
            ng += 1
            s["fail_count"] = s.get("fail_count", 0) + 1
            s["status"] = "閉店" if s["fail_count"] >= CLOSE_AFTER else "閉店の疑い"
        else:
            unk += 1
            if s.get("status") in (None, "未チェック"):
                s["status"] = "要確認"
        # まとめサイトから30日以上消えている店も閉店の疑いにする
        if s.get("last_listed") and s["status"] == "営業中":
            gone = (NOW.date() - datetime.strptime(s["last_listed"], "%Y-%m-%d").date()).days
            if gone >= 30:
                s["status"] = "閉店の疑い"
                s["http"] = f"まとめサイトから{gone}日掲載なし"
    log["steps"][key] = {"ok": ok, "ng": ng, "unknown": unk}


# ---------------------------------------------------------------- 2b. 公式HPをブラウザで開く

GATE = r"ENTER|Enter|enter|ＥＮＴＥＲ|入場|入口|入り口|18歳以上|はい|同意して|入る"
CLOSED_TEXT = r"閉店いたしました|閉店しました|営業を終了|サービスを終了|このドメインは|ドメインの有効期限"


def browse_all(shops: list[dict], key: str = "browse") -> None:
    """まとめサイトに載っている公式HPのリンクを、本物のブラウザで開けるか確かめる。"""
    from playwright.sync_api import sync_playwright

    SHOTS.mkdir(parents=True, exist_ok=True)
    ok = ng = gates = shots = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(user_agent=UA, locale="ja-JP", viewport={"width": 1280, "height": 800},
                                  ignore_https_errors=True)
        for s in shops:
            if not s.get("hp"):
                continue
            page = ctx.new_page()
            res = {"date": TODAY_STR, "ok": False, "gate": False, "title": "", "final": "", "why": ""}
            try:
                resp = page.goto(s["hp"], wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1500)
                status = resp.status if resp else 0
                # 年齢確認の入口ページを通り抜ける
                gate = page.locator("a, button, input[type=button], input[type=submit]").filter(has_text=re.compile(GATE)).first
                if gate.count() and gate.is_visible():
                    try:
                        gate.click(timeout=5000)
                        page.wait_for_load_state("domcontentloaded", timeout=15000)
                        page.wait_for_timeout(1200)
                        res["gate"] = True
                        gates += 1
                    except Exception:  # noqa: BLE001
                        pass
                body = page.inner_text("body")[:5000] if page.locator("body").count() else ""
                res["title"] = (page.title() or "")[:80]
                res["final"] = page.url
                if status >= 400:
                    res["why"] = f"HTTP {status}"
                elif re.search(CLOSED_TEXT, body):
                    res["why"] = "閉店・ドメイン切れの表示"
                elif len(body.strip()) < 20:
                    res["why"] = "中身がほぼ空"
                else:
                    res["ok"] = True
                shot = SHOTS / f"{s['id']}.jpg"
                if res["ok"] and (not shot.exists() or (s.get("browse") or {}).get("shot_hp") != s["hp"]):
                    page.screenshot(path=str(shot), type="jpeg", quality=45, clip={"x": 0, "y": 0, "width": 1280, "height": 800})
                    res["shot_hp"] = s["hp"]
                    shots += 1
                elif shot.exists():
                    res["shot_hp"] = (s.get("browse") or {}).get("shot_hp", s["hp"])
            except Exception as e:  # noqa: BLE001
                res["why"] = type(e).__name__
            finally:
                page.close()
            s["browse"] = res
            if res["ok"]:
                ok += 1
                # ブラウザで開けたなら営業中とみなす(requestsだけ弾かれていた店の救済)
                s["status"] = "営業中"
                s["fail_count"] = 0
                s["last_ok"] = TODAY_STR
            else:
                ng += 1
        browser.close()
    log["steps"][key] = {"ok": ok, "ng": ng, "age_gate_passed": gates, "new_screenshots": shots}


# ---------------------------------------------------------------- 口コミ・摘発報道

def update_reviews(shops: list[dict]) -> None:
    n = flagged = 0
    for s in shops:
        if not s.get("go"):
            continue
        try:
            r = polite_get(s["go"])
            r.encoding = "utf-8"
            summ = extras.summarize_reviews(extras.parse_reviews(r.text), NOW)
        except Exception as e:  # noqa: BLE001
            log["errors"].append(f"口コミ取得失敗 {s['name']}: {e}")
            continue
        s["reviews"] = summ
        if summ["count"]:
            s["rev"] = summ["count"]
        n += 1
        flagged += summ["trouble_flag"]
    log["steps"]["reviews"] = {"shops": n, "trouble_flag": flagged}


def update_news(shops: list[dict]) -> None:
    news = extras.fetch_news(polite_get)
    hits = extras.match_news(shops, news)
    keep = [x for x in news if re.search(r"逮捕|摘発|容疑|書類送検", x["title"])]
    NEWS.write_text(json.dumps({"updated": NOW.isoformat(), "items": keep[:40]}, ensure_ascii=False, indent=1), encoding="utf-8")
    log["steps"]["news"] = {"articles": len(news), "shop_matches": hits}


# ---------------------------------------------------------------- 3. 出勤写真

JS_COLLECT = r"""
() => {
  const pick = (img) => {
    if (!img) return "";
    const cands = [img.currentSrc, img.getAttribute("data-src"), img.getAttribute("data-original"),
                   img.getAttribute("data-lazy-src"), (img.getAttribute("srcset")||"").split(" ")[0], img.src];
    for (const c of cands) if (c && !c.startsWith("data:") && !/loading|noimage|no_image|dummy/i.test(c)) return c;
    return "";
  };
  const bg = (el) => {
    for (const n of [el, ...el.querySelectorAll("*")]) {
      const m = /url\(["']?([^"')]+)/.exec(getComputedStyle(n).backgroundImage || "");
      if (m && !/loading|icon/i.test(m[1])) return m[1];
    }
    return "";
  };
  const out = [];
  for (const a of document.querySelectorAll('a[href*="therapist-detail-"]')) {
    out.push({ href: a.href, text: a.innerText, img: pick(a.querySelector("img")) || bg(a) });
  }
  return out;
}
"""


def parse_therapist(item: dict) -> dict | None:
    text = re.sub(r"\s+", " ", item.get("text") or "")
    m = re.search(r"([^\s()（）]+)\s*[（(](\d{2})[)）]", text)
    if not m:
        return None
    t = re.search(r"(\d{1,2}:\d{2})\s*[〜~～\-]\s*(\d{1,2}:\d{2}|LAST|ラスト)", text)
    shop = er_uuid(item["href"])
    return {
        "shop_er": shop,
        "name": m.group(1),
        "age": int(m.group(2)),
        "time": f"{t.group(1)}〜{t.group(2)}" if t else "",
        "now": "出勤中" in text or "待機中" in text,
        "img": item.get("img") or "",
        "url": item["href"],
    }


def scrape_today(shops: list[dict], ER_SCHEDULE: str) -> list[dict]:
    from playwright.sync_api import sync_playwright

    er_map = {er_uuid(s["er"]): s for s in shops if s.get("er")}
    found: dict[str, dict] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=UA, locale="ja-JP", viewport={"width": 1280, "height": 900})
        urls = [ER_SCHEDULE]
        visited = set()
        while urls and len(visited) < 12:
            url = urls.pop(0)
            if url in visited:
                continue
            visited.add(url)
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            # 遅延読み込みの画像を出すため、少しずつ下までスクロール
            last = 0
            for _ in range(80):
                page.mouse.wheel(0, 1200)
                page.wait_for_timeout(250)
                h = page.evaluate("document.body.scrollHeight")
                y = page.evaluate("window.scrollY + window.innerHeight")
                if y >= h and h == last:
                    break
                last = h
            # 「もっと見る」系のボタンがあれば押して全員出す
            for _ in range(20):
                btn = page.locator("text=/もっと見る|さらに表示|もっと表示|次の\\d+件/").first
                if btn.count() == 0 or not btn.is_visible():
                    break
                btn.click()
                page.wait_for_timeout(1500)
                page.mouse.wheel(0, 4000)
            page.wait_for_timeout(1500)
            for item in page.evaluate(JS_COLLECT):
                t = parse_therapist(item)
                if t and t["url"] not in found:
                    found[t["url"]] = t
                elif t and t["img"] and not found[t["url"]]["img"]:
                    found[t["url"]]["img"] = t["img"]
            # 次のページ
            for href in page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)"):
                if href.startswith(ER_SCHEDULE) and re.search(r"[?&]page=\d+", href) and href not in visited:
                    urls.append(href)
            time.sleep(WAIT_SAME_SITE)
        log["steps"].setdefault("schedule_pages", 0)
        log["steps"]["schedule_pages"] += len(visited)
        browser.close()

    out = []
    for t in found.values():
        s = er_map.get(t.pop("shop_er"))
        if not s:
            continue
        t["shop_id"] = s["id"]
        t["shop_name"] = s["name"]
        out.append(t)
    return out


# ---------------------------------------------------------------- main

def main() -> int:
    shops = json.loads(SHOPS.read_text(encoding="utf-8"))
    skip_photos = "--no-photos" in sys.argv

    for s in shops:
        s.setdefault("area", "八王子")
    for A in AREAS:
        merge_lists(shops, A)
    check_all(shops)
    for step in (browse_all, update_reviews, update_news):
        try:
            step(shops)
        except Exception as e:  # noqa: BLE001
            log["errors"].append(f"{step.__name__} 失敗: {e}")
    for s in shops:
        s["tags"] = sorted(set(s.get("tags") or []) | set(s.get("list_tags") or []), key=list(extras.TAGS).index)

    therapists = None
    if not skip_photos:
        try:
            therapists = []
            for A in AREAS:
                therapists += scrape_today(shops, A["schedule"])
            log["steps"]["therapists"] = len(therapists)
            log["steps"]["therapists_with_photo"] = sum(1 for t in therapists if t["img"])
        except Exception as e:  # noqa: BLE001
            log["errors"].append(f"出勤写真の取得失敗: {e}")
            therapists = therapists or None

    SHOPS.write_text(json.dumps(shops, ensure_ascii=False, indent=1), encoding="utf-8")
    if "--no-kaishun" not in sys.argv:
        try:
            import kaishun
            kaishun.run(sys.modules[__name__])
        except Exception as e:  # noqa: BLE001
            log["errors"].append(f"回春エステの更新失敗: {e}")
    if therapists is not None:
        TODAY.write_text(json.dumps({"updated": NOW.isoformat(), "therapists": therapists},
                                    ensure_ascii=False, indent=1), encoding="utf-8")
    log["finished"] = datetime.now(JST).isoformat()
    log["shops"] = len(shops)
    LOG.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(log, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
