# PhD Floating Summary

![PhD Floating Summary Poster](./ui/poster.png)

[中文版](./Readme.zh.md)

A Windows floating-ball app for batch summarizing the PDFs currently selected in File Explorer.

The app keeps PDF parsing local, sends only extracted text to an OpenAI-compatible API, and writes the final result to `summary.pdf` in the user's Downloads folder.

## Features

- Draggable desktop floating ball
- Double-click to expand or collapse
- Batch process up to 15 selected PDFs at once
- Supports both text PDFs and image/scanned PDFs
- Local API configuration UI
- Output summary PDF with title, summary, and keywords
- Summary language switch: `中文简体` / `English`

## Project Layout

```text
phdfloating/
  app_ui.py               Floating ball UI and interaction
  main.py                 App entry point
  pdf_text_extractor.py   Local PDF text extraction pipeline
  llm_client.py           OpenAI-compatible API client
  prompt_builder.py       Prompt generation and JSON parsing
  summary_pdf_writer.py   Summary PDF writer
  settings_store.py       Local settings persistence
  windows_selection.py    Selected PDF detection from File Explorer
ui/
  ...                     Runtime UI assets
run-floating-summary.bat  Launch app normally
run-debug.bat             Launch app with a visible console
requirements.txt          Python dependencies
```

## Requirements

- Windows 10 or later
- Python 3.10+ recommended
- A valid OpenAI-compatible chat completions API endpoint

## Install

```bat
python -m pip install -r requirements.txt
```

Optional local PDF engines are documented in `requirements.txt` and can be installed manually when your Python version supports them.

## Run

Normal launch:

```bat
run-floating-summary.bat
```

Debug launch with console output:

```bat
run-debug.bat
```

You can also run the module directly:

```bat
python -m phdfloating.main
```

## Usage

1. Double-click the floating ball to expand it.
2. Open **模型设置** and fill in:
   - `API URL`
   - `API Key`
   - `模型名`
   - `Temperature`
   - `请求超时(ms)`
   - `总结语言`
3. Save the configuration.
4. In Windows File Explorer, select one or more PDF files.
5. Click the floating ball document button.
6. The app extracts text locally, calls the configured model, and writes the result to the Downloads folder.

If more than 15 PDFs are selected, only the first 15 are processed and the app shows a warning.

## Output

The default output file is:

```text
%USERPROFILE%\Downloads\summary.pdf
```

If `summary.pdf` already exists, the app automatically creates:

- `summary_1.pdf`
- `summary_2.pdf`
- ...

## Settings Storage

Local settings are stored at:

```text
%APPDATA%\PhDFloatingSummary\settings.json
```

API keys are stored only on the local machine in the user config file and are encrypted with Windows DPAPI before being written to disk. No real API keys are included in this repository.

## License

The source code in this repository is licensed under the [MIT License](./LICENSE).

Third-party dependencies remain under their own licenses. For commissioned UI assets, please keep the original commercial authorization records so future redistribution terms stay traceable.

## Packaging Notes

This repository is maintained as a source project first. If you package it into an `.exe`, later code updates will usually require rebuilding the package to distribute a new version.

For release builds, keep runtime assets in the `ui/` folder alongside the packaged app.

## Development Notes

- UI assets currently used by the app are referenced from `phdfloating/app_ui.py`.
- Temporary preview folders, caches, and debug exports are ignored by `.gitignore`.
- The PDF extraction contract remains:

```json
[{"file_name": "...", "pdf_content": "..."}]
```
