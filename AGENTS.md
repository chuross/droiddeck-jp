# リポジトリ管理ルール

- 暫定upstream patchはoverlay単位で完全削除可能な設計にする。
- 機能パッチは `overlay/<パッチ名>/` ごとに分離する。
- 各ディレクトリに `apply.py` と、そのパッチ専用の差分・helperを置く。
- パッチ適用処理は各 `apply.py` に集約し、`tools/prepare.py` は共通の準備と呼び出しを担当する。
- upstreamの適用箇所が変わったら失敗させる。差分ファイルは適用前に `git apply --check` で検証する。
- READMEは適用パッチ名と各50文字以内の説明だけを記載する。詳しい実装説明や手順は書かない。
- 変更後は関連テストとソース準備を検証する。既存のAPKビルド・Release運用は維持する。
- PRは作成せず、関連検証後に変更をmainへ直接commit・pushする。
- 説明用ドキュメントや説明用テキストファイルは新規作成しない。既存のREADME.mdとAGENTS.mdのみ必要に応じて更新する。

- 生成したProtonは現在のDroidDeckリリース（例: 0.3.2）へ添付する。Proton専用のRelease・タグは作成しない。Protonは上書きし、旧Proton archiveは新しい添付の成功後に削除する。APKの添付ファイル・latest指定を維持する。Protonの公開添付は実行用archive 1個だけにし、ソースarchive・metadata・チェックサム・逆アセンブルなどは添付しない。検証情報はarchive内部とCI artifactに保持する。
- APK Releaseの表示説明は「本家リリース」「本家コミット」「APK SHA-256」の3項目だけにする。Protonの説明や追加項目は記載しない。自動更新判定用の非表示HTMLコメントは使用してよい。
