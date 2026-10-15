# DroidDeck Patch

- [日本語化](overlay/japanese-fonts/): Protonへ日本語フォントを追加。
- [Protonログ出力先](overlay/proton-logs/): Protonログを現在のセッションのログフォルダへ保存。
- [Protonファイル導入](overlay/proton-import/): ファイル選択でARM64版Protonを追加・削除。
- [Low Memoryモード](overlay/low-memory/): Steam軽量表示・裏のホーム解放・画像キャッシュ制限。

- [ARM64 Proton修正](overlay/proton-ntcontext/): 本家DLLのNULLガード・Low Memory時のFEX検索RAM削減。
- [ログ共有](overlay/log-share/): 実ログを変更せず省メモリの共有snapshotを作成。


- [Quick Share](overlay/quick-share/): ログをBluetooth既定設定を経由せずQuick Shareへ共有。
- [CI安定化](overlay/ci-stability/): 依存関係検出の範囲とテスト待機スレッドを修正。

