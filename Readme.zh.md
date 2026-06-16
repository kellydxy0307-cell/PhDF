# PhD Floating Summary

![PhD Floating Summary 海报](./ui/poster.png)

[English](./README.md)

一个 Windows 桌面悬浮球应用，用于批量总结 Windows 文件资源管理器中当前选中的 PDF。

程序在本地完成 PDF 解析，只把提取出的文本发送到 OpenAI-compatible API，并将最终总结结果输出到下载目录中的 `summary.pdf`。

## 功能

- 可拖拽桌面悬浮球
- 双击展开或收起
- 单次最多处理 15 份已选中的 PDF
- 同时支持文本型 PDF 和图像/扫描型 PDF
- 本地 API 配置界面
- 输出包含标题、总结、关键词的 summary PDF
- 支持总结语言切换：`中文简体` / `English`

## 项目结构

```text
phdfloating/
  app_ui.py               悬浮球 UI 与交互
  main.py                 程序入口
  pdf_text_extractor.py   本地 PDF 文本提取
  llm_client.py           OpenAI-compatible API 客户端
  prompt_builder.py       Prompt 构造与 JSON 解析
  summary_pdf_writer.py   总结 PDF 输出
  settings_store.py       本地配置保存
  windows_selection.py    读取资源管理器中当前选中的 PDF
ui/
  ...                     运行时 UI 资源
run-floating-summary.bat  正常启动
run-debug.bat             带控制台调试启动
requirements.txt          Python 依赖
```

## 环境要求

- Windows 10 或更高版本
- 推荐 Python 3.10+
- 一个可用的 OpenAI-compatible chat completions API 地址

## 安装

```bat
python -m pip install -r requirements.txt
```

`requirements.txt` 中还列出了可选的本地 PDF 引擎，可按你的 Python 版本和环境自行安装。

## 运行

正常启动：

```bat
run-floating-summary.bat
```

带控制台调试启动：

```bat
run-debug.bat
```

也可以直接运行模块：

```bat
python -m phdfloating.main
```

## 使用方法

1. 双击悬浮球，展开操作面板。
2. 打开 **模型设置**，填写：
   - `API URL`
   - `API Key`
   - `模型名`
   - `Temperature`
   - `请求超时(ms)`
   - `总结语言`
3. 保存配置。
4. 在 Windows 文件资源管理器中选中一份或多份 PDF。
5. 点击悬浮球上的文档按钮。
6. 程序会先在本地提取文本，再调用模型生成总结，并将结果写入下载目录。

如果一次选中的 PDF 超过 15 份，程序只处理前 15 份，并给出提示。

## 输出文件

默认输出到：

```text
%USERPROFILE%\Downloads\summary.pdf
```

如果 `summary.pdf` 已存在，程序会自动顺延命名为：

- `summary_1.pdf`
- `summary_2.pdf`
- ...

## 配置文件位置

本地配置保存在：

```text
%APPDATA%\PhDFloatingSummary\settings.json
```

API Key 只保存在本机用户配置文件中，写入磁盘前会使用 Windows DPAPI 做本机加密，仓库中不包含任何真实 Key。

## 许可证

本仓库源代码采用 [MIT License](./LICENSE)。

第三方依赖仍遵循各自的许可证。对于委托设计交付的 UI 素材，请继续保留原始商业授权记录，方便后续追溯再分发权限。

## 打包说明

本仓库以源码项目为主。如果你把它打包成 `.exe`，后续代码有更新时，通常仍需要重新打包，才能把新版本交付给其他人使用。

发布时建议将 `ui/` 资源目录和程序主体一起保留。

## 开发说明

- 当前实际使用的 UI 资源由 `phdfloating/app_ui.py` 引用。
- 临时预览目录、缓存和调试导出目录已通过 `.gitignore` 忽略。
- PDF 提取输出契约保持为：

```json
[{"file_name": "...", "pdf_content": "..."}]
```
