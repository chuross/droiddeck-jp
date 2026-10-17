# DroidDeck Patch

- [日本語化](overlay/japanese-fonts/): Protonへ日本語フォントを追加。
- [Protonログ出力先](overlay/proton-logs/): Protonログを現在のセッションのログフォルダへ保存。
- [Protonファイル導入](overlay/proton-import/): ファイル選択でARM64版Protonを追加・削除。
- [ARM64 Proton修正](overlay/proton-ntcontext/): 本家DLLのNULLガード。
- [background-memory](overlay/background-memory/): セッション前のキャッシュ解放と終了時に復元する背景アプリ数制限
- [turnip-process-heap-usage](overlay/turnip-process-heap-usage/): 公式DD-Turnipへプロセス全体のheapUsage修正を追加
