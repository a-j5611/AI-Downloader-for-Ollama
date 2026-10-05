# AI-Downloader-for-Ollama

一个适用于 Ollama 的 AI 模型下载器（Windows 桌面程序）。

> 项目由 DeepSeek-V4.1-flash 制作。

\---

## 功能

下载Ollama官方提供AI模型至Ollama模型目录

## 运行环境

|项目|要求|
|-|-|
|系统|Windows 10 / 11（64 位）|
|**Python**|**不需要安装**：项目自带 `runtime\\python\\`（CPython 3.12 + tkinter/tcl-tk 8.6 + Pillow 12.3）|
|Ollama|需要本机安装 [Ollama](https://ollama.com/download) |

启动器会**优先用项目自带的解释器**；万一 `runtime\\` 缺失或损坏，会自动回退到系统安装的 Python（需含 tkinter 与 pillow）。

## 使用教程

双击 **`启动 AI Downloader.bat`**。

1. 「AI 模型库」页左侧列出全部模型：可按**视觉/工具/思考/嵌入**筛选，按**热门/名称/更新**排序，搜索框**本地即时过滤**；
2. 点任意模型，右侧列出它的**全部版本与体积**；
3. 点版本行「下载」；
4. 下载完成后模型出现在「已安装」页，可删除，或在「AI 模型库」里点「重新下载」。

## 目录数据来源

|用途|地址|
|-|-|
|全量模型|`https://ollama.com/library`（一页返回全部官方模型）|
|补充来源|`https://ollama.com/search`、`https://ollama.com/api/tags`|
|版本列表|`https://ollama.com/library/<模型>/tags`|
|精确体积|`https://registry.ollama.ai/v2/library/<模型>/manifests/<版本>`|
|下载|本机 Ollama 服务的 `/api/pull`、`/api/delete`，或自研多线程引擎直连 registry|

获取模型列表会缓存到 `%APPDATA%\\AIDownloader\\cache\\`保留6小时，**离线也能浏览**，右上角「刷新目录」可随时更新。

## 绑定下载位置（自动识别已下载模型）

1. **设置 → 下载位置（模型库）** → 点「选择文件夹…」选中任意目录；
2. 点「切换并重启 Ollama」，程序会**停服务 → 用新的 `OLLAMA\_MODELS` 起服务**，新下载的模型就落到绑定位置；
3. 程序会**自动扫描该目录**，包括**服务尚未加载、但磁盘上确实存在**的（别的工具或旧安装留下的）；
4. 若正在运行的 Ollama 用的不是绑定目录，界面会明确提示并给出一键「切换并重启」；
5. 最近用过的位置会留成快捷按钮，随时切回。

## 目录结构

```
AIDownloader.pyw          入口（无控制台窗口，单实例）
启动 AI Downloader.bat     启动器（优先使用自带解释器）
runtime\\python\\           自带 Python 运行时（CPython 3.12 + tkinter + Pillow）
app\\
  main\_window.py          窗口外壳：画布卡片布局、导航、下载指示条、状态轮询
  views\_catalog.py        ★ AI 模型库：全量列表 + 版本面板 + 下载
  catalog.py              ★ 目录抓取/解析/缓存、版本与精确体积
  downloads.py            ★ 下载队列：进度、取消、续传、停滞检测、完整性核对
  mirror\_dl.py            ★ 多线程/镜像下载引擎（Range 分段 + 断点续传 + SHA-256 校验）
  dl\_dialog.py            下载前弹窗：线程数 / 下载源 / 测速
  library.py              ★ 下载位置绑定：扫描模型目录、探测服务实际目录
  panels.py               已安装 / 服务 / 设置 / 日志
  ollama\_api.py           Ollama HTTP 客户端 + 服务管理 + 速率计
  storage.py              磁盘层：完全删除、孤儿清理、残留分片
  widgets.py / theme.py / i18n.py / config.py / backgrounds.py
release\\                 更新ZIP文件
```

## 配置

`%APPDATA%\\AIDownloader\\config.json`：主题、语言、背景、主机地址、Ollama 目录、
**模型目录与历史绑定位置**、下载线程数与镜像源、目录排序/筛选、待续传模型。
缓存：`%APPDATA%\\AIDownloader\\cache\\`；错误日志：`%APPDATA%\\AIDownloader\\error.log`。
