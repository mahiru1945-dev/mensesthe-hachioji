"""公式HP側の出勤表を扱う。

- find_schedule: 公式HPのリンク一覧から「出勤」「スケジュール」ページを探す
- scrape_official: 公式HPの出勤ページをブラウザで開き、写真・名前・年齢・時間を拾う
- update_history: 女の子ごとの出勤履歴(日付と時間)を残す
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse

TEXT_HINT = re.compile(r"本日の出勤|今日の出勤|出勤情報|出勤表|出勤|スケジュール|SCHEDULE|Schedule", re.I)
HREF_HINT = re.compile(r"schedule|syukkin|shukkin|attend|today|shift", re.I)
BAD_HREF = re.compile(r"recruit|kyujin|求人|mailto:|tel:|twitter|x\.com|instagram|line\.me", re.I)
NAME_OK = re.compile(r"^[぀-ヿ一-鿿A-Za-zＡ-Ｚａ-ｚ・ー々]{1,10}$")
TIME_RE = re.compile(r"(\d{1,2}:\d{2})\s*[-〜~～]\s*(翌?\d{1,2}:\d{2}|LAST|ラスト|ラスト迄)?")
AGE_RE = re.compile(r"([^\s()（）]{1,12})\s*[（(](\d{2})(?:歳|才)?[)）]")
KEEP_DAYS = 21


def _same_site(a: str, b: str) -> bool:
    ha, hb = urlparse(a).netloc.split(":")[0], urlparse(b).netloc.split(":")[0]
    root = lambda h: ".".join(h.split(".")[-2:])  # noqa: E731
    return bool(ha) and root(ha) == root(hb)


def find_schedule(links: list[dict], base: str) -> str:
    best, score = "", 0
    for ln in links:
        href, text = ln.get("h") or "", (ln.get("t") or "").strip()
        if not href.startswith("http") or BAD_HREF.search(href + text) or not _same_site(href, base):
            continue
        sc = 0
        if TEXT_HINT.search(text):
            sc += 3 if re.search(r"本日|今日", text) else 2
        if HREF_HINT.search(href):
            sc += 2
        if sc > score:
            best, score = href, sc
    return best


def state_of(text: str) -> str:
    if re.search(r"受付終了|本日終了|退勤|完売", text):
        return "ended"
    if re.search(r"案内中|接客中|予約満了|満了|ご予約完売", text):
        return "busy"
    if re.search(r"待機中|即案内|即ご案内|すぐ案内|案内可|今すぐ|即日", text):
        return "now"
    if re.search(r"次回|出勤予定", text):
        return "later"
    return ""


def parse_card(text: str) -> dict | None:
    text = re.sub(r"\s+", " ", text)
    m = AGE_RE.search(text)
    if not m:
        return None
    name, age = m.group(1).strip("「」【】[]"), int(m.group(2))
    if not (18 <= age <= 59) or not NAME_OK.match(name) or re.search(r"店長|スタッフ|新人|体験|オーナー|お知らせ|割引|コース", name):
        return None
    t = TIME_RE.search(text)
    time = ""
    if t:
        time = f"{t.group(1)}〜{t.group(2)}" if t.group(2) else f"{t.group(1)}〜"
    return {"name": name, "age": age, "time": time, "state": state_of(text)}


def scrape_official(ctx, shops: list[dict], js_cards: str, gate_re: str, label: str) -> list[dict]:
    """shops のうち公式HPに出勤ページがある店を回る。"""
    from playwright.sync_api import sync_playwright

    out: list[dict] = []
    visited = ok = 0
    skip = getattr(ctx, "SKIP", set())
    targets = [s for s in shops if s.get("hp_schedule") and s.get("status") != "閉店" and s["id"] not in skip]
    with sync_playwright() as p:
        b = p.chromium.launch()
        cx = b.new_context(user_agent=ctx.UA, locale="ja-JP", viewport={"width": 1280, "height": 900},
                           ignore_https_errors=True)
        for s in targets:
            pg = cx.new_page()
            try:
                pg.goto(s["hp_schedule"], wait_until="domcontentloaded", timeout=30000)
                pg.wait_for_timeout(1000)
                gate = pg.locator("a, button, input[type=button], input[type=submit]").filter(
                    has_text=re.compile(gate_re)).first
                if gate.count() and gate.is_visible():
                    try:
                        gate.click(timeout=4000)
                        pg.wait_for_load_state("domcontentloaded", timeout=12000)
                        if not _same_site(pg.url, s["hp_schedule"]) or "schedule" not in pg.url:
                            pg.goto(s["hp_schedule"], wait_until="domcontentloaded", timeout=30000)
                    except Exception:  # noqa: BLE001
                        pass
                for _ in range(15):
                    pg.mouse.wheel(0, 1500)
                    pg.wait_for_timeout(180)
                pg.wait_for_timeout(1000)
                cards = pg.evaluate(js_cards)
            except Exception as e:  # noqa: BLE001
                ctx.log["errors"].append(f"{label} 公式出勤の取得失敗 {s['name']}: {type(e).__name__}")
                pg.close()
                continue
            visited += 1
            got = []
            for c in cards:
                d = parse_card(c.get("text") or "")
                if not d:
                    continue
                href = c.get("href") or ""
                d.update({
                    "shop_id": s["id"], "shop_name": s["name"], "src": "公式HP",
                    "img": c.get("img") or "",
                    "url": href if href.startswith("http") and _same_site(href, s["hp_schedule"]) else s["hp_schedule"],
                    "now": d["state"] == "now",
                })
                got.append(d)
            uniq = {(g["name"], g["age"]): g for g in got}
            if 0 < len(uniq) <= 150:
                out += list(uniq.values())
                ok += 1
            pg.close()
        b.close()
    ctx.log["steps"][f"{label}_official"] = {"schedule_pages": len(targets), "opened": visited, "shops_with_girls": ok,
                                             "girls": len(out)}
    return out


def update_history(path, today_str: str, groups: list[list[dict]]) -> None:
    """女の子ごとに、日付→出勤時間 を残す(直近 KEEP_DAYS 日)。"""
    try:
        hist = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:  # noqa: BLE001
        hist = {}
    for group in groups:
        for t in group:
            key = f"{t['shop_id']}|{t['name']}"
            day = hist.setdefault(key, {})
            if t.get("time") or today_str not in day:
                day[today_str] = t.get("time") or "時間不明"
    cutoff = (datetime.strptime(today_str, "%Y-%m-%d") - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    hist = {k: {d: v for d, v in days.items() if d >= cutoff} for k, days in hist.items()}
    hist = {k: v for k, v in hist.items() if v}
    path.write_text("{\n" + ",\n".join(json.dumps(k, ensure_ascii=False) + ":" + json.dumps(v, ensure_ascii=False)
                                       for k, v in sorted(hist.items())) + "\n}\n", encoding="utf-8")
