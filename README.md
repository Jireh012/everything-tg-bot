# Everything 搜索 Telegram Bot

在 Telegram 里搜索 [Everything HTTP](https://voidtools.com/support/everything/http/) 站点上的文件，回复编号后由 bot 下载并发送。大文件通过 Local Bot API 发送（上限 2GB）。

默认搜索地址：`http://www.https.ng:1234`。

## 功能

- 发送关键词搜索，按修改时间倒序列出结果
- 回复编号下载，bot 把文件发到当前对话
- 任意对话输入 `@bot 关键词` Inline 搜索，点「私聊下载」取文件（无需把 bot 加进群）
- 支持 Everything 原生语法，例如 `ext:pdf 多元社会中`、`*.epub 福音`、`size:>10mb ext:pdf`
- 结果下方格式按钮：PDF / EPUB / MOBI 等，点选后重新向网站查询
- 翻页、路径末段区分同名文件
- 公开使用，带搜索/下载频率限制和并发下载上限

## 准备

1. 向 [@BotFather](https://t.me/BotFather) 创建 bot，拿到 `BOT_TOKEN`
2. 在 BotFather 打开 Inline：`/setinline` → 选中 bot → 提示语填 `搜索文件，例如 ext:pdf 福音`
3. 在 [my.telegram.org](https://my.telegram.org) 申请 `TELEGRAM_API_ID` 和 `TELEGRAM_API_HASH`（Local Bot API 需要）
4. 安装 Docker（推荐）或本机 Python 3.11+

## 用 Docker 启动

```bash
cd everything-tg-bot
cp .env.example .env
# 编辑 .env，填入 BOT_TOKEN、TELEGRAM_API_ID、TELEGRAM_API_HASH
docker compose up -d --build
```

Bot 容器会连接 `telegram-bot-api:8081`。查看日志：

```bash
docker compose logs -f bot
```

## 本机跑 bot（Local API 仍用 Docker）

```bash
docker compose up -d telegram-bot-api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 填 token 与 API_ID / API_HASH；LOCAL_BOT_API_URL 保持 http://127.0.0.1:8081
# 本机 bot 与容器不共享磁盘时，设 USE_FILE_URI=0
python -m bot.main
```

## Inline 使用

在任意私聊或群里输入：

```text
@你的Bot用户名 多元社会中
@你的Bot用户名 ext:pdf 福音
```

点选一条结果会发出文件信息；再点「私聊下载」，bot 在私聊里把文件发给你。大文件仍走 Local Bot API，不能直接作为 Inline 附件发出。

## 环境变量

见 `.env.example`。常用项：

| 变量 | 说明 |
| --- | --- |
| `BOT_TOKEN` | BotFather 发放的 token |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | my.telegram.org 申请 |
| `EVERYTHING_BASE_URL` | Everything HTTP 搜索地址 |
| `LOCAL_BOT_API_URL` | Local Bot API 根地址 |
| `DOWNLOAD_DIR` | 临时下载目录，需与 Local API 共享才能用 `file://` |
| `USE_FILE_URI` | `1` 时用本地路径交给 Local API，避免再上传一遍 |

## 使用边界

请只搜索、转发你有权分享的文件。Bot 不长期保存文件正文，下载发送后会删除临时文件。
