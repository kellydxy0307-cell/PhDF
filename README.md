# PhD Floating Summary

一个 Windows 桌面悬浮球，用于批量总结资源管理器中当前选中的 PDF。初版不内置任何 API Key。

## 运行

双击：

```bat
run-floating-summary.bat
```

如果需要看报错日志，用：

```bat
run-debug.bat
```

## 使用方式

1. 双击悬浮球展开。
2. 点“设置”图标，填写 `API URL`、`API Key`、`模型名`、`Temperature`、`请求超时(ms)`，保存。
3. 在 Windows 文件资源管理器里选中 PDF。
4. 单击悬浮球，或展开后点击“总结”图标。
5. 程序会读取前 5 份 PDF，调用 OpenAI-compatible Chat Completions API，总结结果输出到下载文件夹的 `summary.pdf`。

如果选中的 PDF 超过 5 份，程序只处理前 5 份并弹窗提醒。

## 关键文件

- `phdfloating/pdf_text_extractor.py`：专门负责读取 PDF 并输出 `input_json_list = [{"file_name":"","pdf_content":""}]`。
- `phdfloating/windows_selection.py`：读取资源管理器当前选中的 PDF。
- `phdfloating/prompt_builder.py`：论文总结 system prompt 和 JSON 解析。
- `phdfloating/llm_client.py`：OpenAI-compatible API 请求。
- `phdfloating/summary_pdf_writer.py`：生成下载文件夹里的 `summary.pdf`。
- `phdfloating/app_ui.py`：悬浮球、设置面板、按钮交互。

## 配置保存位置

配置保存在当前 Windows 用户的：

```text
%APPDATA%\PhDFloatingSummary\settings.json
```

API Key 只会保存在本机配置文件中，仓库代码里没有写入任何真实 Key。
