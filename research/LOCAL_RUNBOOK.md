# 週末ローカル実行手順

## 実装棚卸しと差分

既存GitHubの `simulation.py` と3件のテストを保持して拡張。従来のJS手入力200回版は元ワークスペース `work/keiba-lab` にそのまま残す。本研究用の画面には投票・予算配分を導入しない。

追加：時点管理付きDuckDB、過去走参照、初期能力推定、標準100,000回、全頭順位、固定予測、結果照合、ローカルUI。Python標準HTTPサーバーを使い、追加依存はDuckDBのみ。通常の実行にLLMや外部APIは不要。

## セットアップ（Windows / リポジトリのルート）

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[test]"
New-Item -ItemType Directory -Path data -Force
.venv/Scripts/python.exe -m pytest -q --basetemp data/test-temp
.venv/Scripts/python.exe -m jra_engine.local --dataset demo demo
.venv/Scripts/python.exe -m jra_engine.local --dataset demo serve
```

画面： http://127.0.0.1:4180 。`demo`は架空のテスト専用DB。実データ運用はサーバーを停止し、`--dataset real serve` で起動する。両方を同時に使うなら別の `--port` を指定。同じDBに複数プロセスで書き込まない。HTTP公開サーバーではなく、このPC内の研究用。

## データ入力

JSON全体は `{"schema_version":1,"dataset":"real","tables":{"races":[...],"race_entries":[...],"horse_performances":[...]}}`。

全レコードに必要：`source`（URL等）, `available_at`（公開され利用可能になった日時）, `retrieved_at`（取得日時）, `updated_at`, `availability_evidence`（その公開日時を確認できた根拠）。すべてタイムゾーン付きISO日時。根拠を確認できない過去の公開日時を推定で埋めない。取得が後日の資料でも公開時点の裏付けを利用者が確認して入力する。

テーブル別の識別子・主な列：

|テーブル|フィールド|
|---|---|
|horses|horse_id, name|
|races|race_id, name, venue（JRA場名）, surface（芝/ダート）, distance, start_at, declared_entries（取消を含む公式出走表の全頭数）|
|race_entries|entry_id, race_id, horse_id, number, name, status（active/scratched/excluded）, style（逃げ/先行/差し/追込・不明は省略）, carried_weight, frame, jockey, body_weight, weight_change|
|horse_performances|entry_id, race_id, horse_id, start_at, venue, surface, distance, field_size, finish, status（finishedのみ使用）, going, time_seconds, margin, last3f, passing_positions, closing_percentile（任意0〜1、高いほど高評価）|
|track_conditions|race_id, going, weather, cushion, moisture, front_bias（-2〜2、正が先行有利）, sample_count|
|odds_snapshots|entry_id, race_id, horse_id, odds（任意・保存用、能力推定には使用しない）|
|paddock_evaluations|entry_id, race_id, horse_id, score（-2〜2、手動）|
|race_results|entry_id, race_id, horse_id, status（finished/scratched/excluded/dnf/disqualified）, finish（完走時）, final（true）, time_seconds|

`race_id`はJRA公式12桁、`horse_id`は公式10桁。`entry_id`は `race_id:horse_id`。架空DBはDEMO識別子を使う。全頭の出走表が必要で、履歴不足は画面に明示。

```powershell
.venv/Scripts/python.exe -m jra_engine.local --dataset real import authorized-bundle.json
.venv/Scripts/python.exe -m jra_engine.local --dataset real import entries.csv --table race_entries
.venv/Scripts/python.exe -m jra_engine.local --dataset real predict <race_id> --seed 42
.venv/Scripts/python.exe -m jra_engine.local --dataset real compare <race_id>:live
```

CSVはUTF-8、上記フィールドを列名に使用。IDは文字列のまま保持する。JSONファイルは画面からも取り込める。既存200回版のJSONとは形式が異なるため自動移行しない。

## 能力推定と未校正項目

過去走は対象馬のみSQL抽出し最大20走、直近3走を0.5/0.3/0.2の重みで評価する。基本スコアは過去走の頭数内の相対着順から70〜85に変換。同場・同馬場、近距離などの実績差をサンプル数で縮小する。異なる競馬場の時計やクラスは直接比較しない。時計・着差は保存できるが、標準時計や対戦水準の校正は未実装。騎手固有効果も未校正で、斤量のみ補正。少数履歴の事前値77.5は「観測された実績」ではなく未校正の中立仮定として表示する。

能力分散は過去スコアから推定し2〜6に制限、3走未満は4.5。各バッチで全馬共通の展開変動と各馬の脚質感応度を加える。固定seed、同一NumPy版、同一バッチ設定で再現。勝率同点時は馬番順。

当日バイアスは手動実測値をsample_count/5で縮小。馬場状態だけから先行有利を決めない。クッション値・含水率・馬体重は保存用で、自動係数は未校正。パドックは手動のみ。

## 予測固定と時点管理

DBはID＋公開時点で追記。二重取り込みは無視。同一公開時点の内容変更は拒否し、訂正は新しい公開日時で追記。公開日時を過去に偽装しない。

予測はliveでレースごとに1件。固定後の馬場・取消等を使って上書きしない。入力の状態が変わる前に固定した内容は、その当時の予測として残す。事後の再計算はretrospectiveで別IDとし、入力ハッシュ・seed・時点からキャッシュする。公開時点as_of以降の情報と当該レース結果は特徴量から除外。モデルは現在未学習のため学習期間はなし。将来の学習モデル導入時はtrain_end < as_ofを必須化する。

DuckDBとJSONスナップショットにSHA-256を保存。APIに更新・削除機能はない。これはアプリ上の追記固定と改変検知であり、PC管理者によるDB直接編集を物理的に不可能にするWORM媒体ではない。独立した時刻証明もない。PC時計が正しいことを確認して運用する。サーバー停止後にdataディレクトリをバックアップする。DBを開く前にも閉じたDBをローカルバックアップする。

## 結果照合の固定ルール

- 全頭分の確定結果が揃うまで評価しない。公開日時は発走後。
- 順位差は予測順位−確定順位。
- Kendall tau-bは完走した馬のみ。同着は同順位、同点補正あり。
- 取消・除外・中止・失格は全頭表に状態を表示し、tau-bから除外。
- 固定後の取消・除外時は確率の標本空間が変わるためLog Loss/Brierは欠測。事後の再正規化はしない。
- 中止・失格は勝利確率評価では非勝者。勝者同着は勝者に均等なターゲット確率を配分する一般化スコア。
- 上位同着時のTop3重複は確定順位<=3の集合との重複。完全一致は対象外（null）。
- 結果訂正時は結果ハッシュごとに別レポートを追記し、予測を変更しない。

## 週末の実施状況

10/31東京11RペルセウスS、京都11RファンタジーS、11/1京都11RまほろばS、東京11R天皇賞（秋）。予定はJRA公式 https://www.jra.go.jp/ と研究計画で確認。公式ID・確定出走馬・枠順を未取得のため、本番DBへ推測値を登録していない。4レースの本番予測と実結果照合は未実施。

当日、公式確定出走表を確認→合法的な手入力/インポート→全頭数と欠損を確認→発走60〜30分前に固定→結果確定後に全頭結果を入力→照合。未取得なら「未実施」と記録。

Supabase、動画解析、NAR、回収率評価は今回の実装対象外。実データの十分な蓄積、時計・クラス・騎手補正の校正、市場との比較、多数レースの時系列集計は後続。4レースの結果から統計的優位性は主張しない。

## 2026-10-28：実データ準備テーブル

`race_targets`（target_key）、`race_registrations`（fact_id）、`observed_performances`（fact_id）を追加。未確定の登録馬、公式IDの未照合データを正規の出走表から分離して追記保存する。これらは予測エンジンの入力テーブルに含めない。欠損ID・馬番を架空の値で埋めない。

実データ画面は `python -m jra_engine.local --dataset real serve`。`/api/preparation` はこの端末に蓄積した準備情報だけを返す。登録馬は「登録段階」と表示し、年度最高レーティングと予想着順を混同しない。原資料の時刻が確認できない場合、available_at は閲覧確認時刻とし、published_at はnullのまま。公開日だけ分かる資料を過去の00:00公開と仮定しない。

このワークスペースでは4対象レースの登録情報61頭分（天皇賞17、ペルセウス19、ファンタジー12、まほろば13）、公式年度最高レーティング16件、過去成績17件をローカルに記録した。天皇賞はJRA公式、他3レースの登録名簿はスポーツナビの照合待ち情報。過去成績は2025天皇賞全14頭とガロンヌの直近3走。公式ID未解決のためモデルに投入せず、データ確認画面で表示する。これは全61頭の直近3走収集完了を意味しない。

実データそのものと一回限りの転記スクリプトは `data/` 内に限定し、Publicリポジトリには含めない。別のPCでcloneするとデータは空で起動する。

## 2026-10-29：本番固定前の確認と履歴の画面表示

`python -m jra_engine.local --dataset real readiness` で登録候補のID・正規化済み履歴件数・3走枠不足・固定可能件数を確認する。
`readiness <race_id>` はそのレースの不足を全頭分返す。HTTPは `/api/readiness` と `/api/readiness?race_id=...`。
画面は `horse_performances` の実件数を表示し、各馬の掲載JRA直近3走と出典を開ける。準備状況JSONを保存できる。

実データのCLI/APIによるlive固定には、`races.confirmed=true`、出走表各行の `official_confirmed=true`、
`identity_verified=true` と `identity_source`、馬番・枠順・騎手・斤量・全頭数が必要。
レース／出走表の出典は `source` または `official_source` にJRA公式URLを記録し、`source_sha256` に原本SHA-256を残す。
レースに `target_key` を入れるか `race_targets.official_race_id` を照合済みIDへ追記すると準備欄に関連付く。
これらの確認フラグは実際の照合作業後に記録する。フラグだけで独立した公開時点証明になるものではない。

過去走は最低1走必要。3走未満・任意の馬場／脚質／パドック不足は警告として表示し、既存モデルの欠損処理を維持する。
モデルが使用する最大20走と当日情報の取得時刻がas_ofより後なら固定しない。原本SHA-256・出典不足も拒否する。
固定済みlive予測は追加データやseed変更で書き換えず、同じ記録を返す。demoとretrospectiveは既存動作を維持。
`pipeline.py` と `simulation.py` の係数・計算処理は変更していない。

## 2026-10-31：Claude引継ぎの独立確認とジョブレビュー

ローカル正本では対象4レースのlive予測を読取確認した。天皇賞は既存固定、ペルセウスS・ファンタジーSは10:44 JST、まほろばSは10:50 JSTの早期固定（テストで意図より早く保存されたもの）。全てseed42・10万回・同一baselineモデル。既存のliveを再実行・上書きしない。朝の固定には発走直前の変化が含まれないため、取消や当日状態の差分は別記録にする。

ClaudeのJRA HTML変換・結果ジョブ・スケジューラをパッケージへ移し、結果のレース同定、全頭ID/馬番、ロック所有権、移植可能なパスとオッズ比較を回帰テストで確認した。実際のレース後の結果リンク発見・解析は未検証。登録済みWindowsタスクの再登録・削除は行っていない。設定と実行方法は[RACE_JOBS.md](RACE_JOBS.md)を参照。

過去馬場goingの候補76件は5件→全件の隔離検証後に時点付き追記し、再取り込み0件・既存履歴1009行と全予測ハッシュ不変を確認。公式15頭/既存16頭の3件は頭数定義の確認待ちで隔離を継続。今回の追加情報を過去に固定した入力へ遡及して混ぜない。クラウド非公開DBは以前のコピーであり、PC正本へ切替済みとは扱わない。
