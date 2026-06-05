# 足球转会情报站

一个静态 HTML 足球转会信息整合站，按联赛分类展示转会新闻、来源可信度、综合热度排行榜和高可信进展。

## 本地查看

直接打开 `index.html` 即可查看样例数据。部分浏览器对本地 `fetch` 有限制时，可以启动本地静态服务：

```powershell
python -m http.server 8000
```

然后访问 `http://localhost:8000`。

## 更新数据

```powershell
python scripts/fetch_transfers.py
```

脚本会读取公开 RSS 源，筛选转会相关内容，写入 `data/transfers.json`。如果网络或来源临时失败，会保留已有数据并在 JSON 中记录 `errors`。

## GitHub Pages 自动更新

1. 将本目录推送到 GitHub 仓库的 `main` 分支。
2. 在仓库 `Settings -> Pages` 中选择 GitHub Actions 作为发布源。
3. `.github/workflows/update.yml` 会每小时运行一次，也支持在 Actions 页面手动触发。

## 可信度规则

- S：俱乐部官网、联赛官网、球员官方渠道。
- A：长期可靠的认证记者、通讯社或高可信媒体。
- B：主流体育媒体和 Transfermarkt 等聚合源。
- C：二次转载、球迷站或原始出处不足的内容。

无认证个人爆料、匿名截图、纯搬运账号和无法访问原文的内容不进入默认采集源。
