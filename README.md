# DroidDeck Patch

- [日本語化](overlay/japanese-fonts/): Protonへ日本語フォントを追加。
- [Protonログ出力先](overlay/proton-logs/): Protonログを現在のセッションのログフォルダへ保存。
- [Steam診断](overlay/steam-diagnostics/): 標準ログの解析と選択Protonの照合。
- [Protonファイル導入](overlay/proton-import/): ファイル選択でARM64版Protonを追加・削除。
- [トースト表示](overlay/readable-toasts/): Proton導入・ログ削除などの通知を白文字と濃色背景で表示。
- [Low Memoryモード](overlay/low-memory/): DXGI Budget制限・Steam未使用メモリ回収・Decky停止。
- [スリープ診断](overlay/suspend-survival/): 終了原因・復帰状態の記録と実験用WakeLock保持。

- [ARM64 Proton修正](overlay/proton-ntcontext/): DroidDeck-ProtonベースのNULL修正とDXGI Budget cap。
- [VKD3D省メモリ](overlay/vkd3d-low-memory/): Low Memory時の未使用バッファ・描画プール保持を削減。
- [ビルドキャッシュ](overlay/build-cache/): 検証済み依存ソースを再利用してAPKビルドを短縮。
- [ログ共有](overlay/log-share/): 実ログを変更せず省メモリの共有snapshotを作成。


- [Quick Share](overlay/quick-share/): ログをBluetooth既定設定を経由せずQuick Shareへ共有。
- [CI安定化](overlay/ci-stability/): 依存関係検出の範囲とテスト待機スレッドを修正。
