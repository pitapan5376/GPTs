# ローマ字→漢字変換Webアプリ

iPhoneなどローマ字入力しかできない環境向けに、ローマ字で入力した文章をAnthropic Claude APIで漢字かな交じり文に変換するツールです。

## 特徴

- サーバー不要（単一HTMLファイル、GitHub Pagesでの配信を想定）
- APIキーはこの端末のブラウザ（localStorage）にのみ保存され、ソースコードやサーバーには一切保存されない
- 変換はブラウザからAnthropic APIへ直接送信（`anthropic-dangerous-direct-browser-access` ヘッダーを使用）

## 使い方

1. ページを開くと初回はAPIキー設定画面が開くので、[console.anthropic.com](https://console.anthropic.com) で発行したAPIキーを入力して保存
2. ローマ字（スペース区切り・ベタ打ちどちらでも可）を入力し「変換する」をタップ
3. 変換結果は直接編集してから「結果をコピー」でクリップボードにコピー可能

設定（⚙アイコン）からAPIキーの変更・削除、使用モデル（Claude Haiku 4.5 / Claude Sonnet 5）の切り替えができます。

## 注意事項

- Anthropic APIキーは [console.anthropic.com](https://console.anthropic.com) で発行する従量課金のAPIキーです。Claude.aiのサブスクリプションとは別物・別課金です。
- ブラウザに保存したAPIキーは、その端末・ブラウザにアクセスできる人には見える状態になります。個人利用の端末でのみ使用してください。
- OpenAI APIはブラウザから直接呼び出すとCORSでブロックされるため、このツールはAnthropic Claude APIのみに対応しています。
