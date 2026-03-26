# 🚣 Rowing Data Aggregator
JARA（日本ローイング協会）の公式サイトからレース結果を自動収集し、構造化データとしてPostgreSQLに蓄積するツールです。

## 🌟 主な機能
- 一括インポート: 大会TopページのURLを入力するだけで、全種目の結果を自動巡回して取得。
- メタ情報自動取得: 大会の「期日（開催日）」や「場所」をHTMLから自動抽出。
- 重複防止: 同一大会・同一レースの二重取り込みをSQLレベルでガード。
- サマリー表示: 現在DBにどの大会のデータが何件入っているかをアプリ上で一覧確認。

## 🚀 運用ガイド
1. ネットワークアクセス
本ツールおよび可視化ツール（Metabase）は、同一ネットワーク内の別端末からブラウザ経由でアクセス可能です。

- データ登録 (Streamlit): http://(サーバーのIPアドレス):8501
- データ分析 (Metabase): http://(サーバーのIPアドレス):3000

 > アクセスできない場合は、ホストマシンのファイアウォール設定で 8501 および 3000 ポートが解放されているか確認してください。

1. Metabase 閲覧用アカウントの作成
チームメンバーに「閲覧のみ」の権限を付与する手順です。
   1. 管理者でログインし、右上の 「管理者設定 (Admin settings)」 を開く。
   2. 「人々 (People)」 タブからメンバーを招待。
   3. 「データの権限 (Permissions)」 で、特定のデータベースに対し「閲覧のみ (View counts/Saved questions)」を設定。

1. 効率的なデータ収集のコツ
   - ブロック対策: 短時間に大量のURLを読み込むとサイト側から一時制限を受ける場合があります。一括取得時はコード内の time.sleep() を適切に設定してください。
   - データの修正: 大会名や場所が誤って登録された場合は、DB（PostgreSQL）の regattas テーブルを直接編集するか、該当レコードを NULL にして再度URLを読み込ませてください。

## 🌐 外部公開 (Cloudflare Tunnel)
VPNやポート開放なしで、外出先から安全にアクセスしたい場合に検討してください。

cloudflared を導入し、特定のドメイン（例: rowing.example.com）を localhost:8501 にトンネルさせることで、セキュアな外部公開が可能です。

## 📊 スキーマ構造
データは以下の4階層で正規化されています。分析時はこれらを JOIN して使用します。

1. regattas (大会情報)
2. events (種目: 男子エイト等)
3. races (組: 予選・決勝等)
4. crews (着順・タイム・選手情報)

---

## memo
metabaseリレーション設定

|エンティティ（テーブル） | 設定するカラム（FK） | ターゲットのテーブル | ターゲットのカラム（PK）|
|-|-|-|-|
| rower_profiles | rower_id | rowers | id |
| events | regatta_id | regattas | id |
| races | event_id | events | id |
| crews | race_id | races | id |
| crew_members | crew_id | crews | id |
| crew_members | rower_id | rowers | id| 

```
docker compose --profile external up -d
```

