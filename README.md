# 八王子・立川メンエス一覧

八王子エリアのメンズエステを、まとめサイトから集めて一覧にする個人用ページ。

- 一覧ページ: `docs/index.html`（GitHub Pagesで公開）
- 毎朝 5:47（日本時間）に GitHub Actions が `scraper/update.py` を実行
  - まとめサイトから店舗一覧を取り直して新店を追加
  - 公式HPにアクセスして、3日続けてつながらなければ「閉店」
  - エステ魂ランキングの本日出勤ページから写真付きで出勤者を取得
- データ: `docs/data/shops.json`（店舗）、`docs/data/today.json`（本日出勤）、`docs/data/last_run.json`（実行ログ）

手動で更新したいときは Actions →「毎日の更新」→「Run workflow」。
