# ダミーメディア生成

WordPress の添付ファイル一覧から、表示確認用のダミーファイルを作ります。画像はグレーの単色です。画像以外は 0 バイトです。

## 抽出

メディア一覧は `wp_media.sql` で抽出します。結果を UTF-8 の CSV で保存します。サンプルは `wp_posts.csv` です。

## 実行

```text
python -m pip install -r requirements.txt
python generate_dummy_media.py --csv wp_posts.csv --out uploads
```

`--out` には、検証環境の `wp-content/uploads` を指定します。CSV の `2018/10/sl1-1.jpg` は `uploads/2018/10/sl1-1.jpg` として作られます。同じパスのファイルは上書きされます。

終了時に、作成した画像数、空ファイル数、スキップ数を表示します。
