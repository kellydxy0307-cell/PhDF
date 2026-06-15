# Contributing to PhD Floating Summary

Thanks for your interest in improving this project.

We welcome bug reports, feature proposals, documentation fixes, and pull requests.

## Before You Start

- Please read `README.md` first.
- Check whether a similar issue or pull request already exists.
- For security-sensitive topics, do not open a public issue first. Please follow `SECURITY.md`.

## Development Environment

Recommended environment:

- Windows 10 or later
- Python 3.10+

Install dependencies:

```bat
python -m pip install -r requirements.txt
```

Run locally:

```bat
python -m phdfloating.main
```

Or use:

```bat
run-debug.bat
```

## Contribution Types

Useful contributions include:

- Fixing PDF selection or extraction bugs
- Improving summary PDF layout
- Improving stability of the floating UI
- Clarifying setup and packaging documentation
- Adding tests or validation scripts

## Pull Request Guidelines

Please try to keep pull requests focused.

- One topic per pull request
- Explain what changed and why
- Include manual verification steps
- Mention Windows version and Python version when relevant
- Attach screenshots for UI changes

If your change affects PDF parsing or summary generation, include:

- Example input conditions
- Expected output behavior
- Known tradeoffs or limitations

## Coding Notes

- Follow the existing project structure and naming where practical.
- Keep changes conservative unless a broader refactor is clearly necessary.
- Avoid committing unrelated formatting churn.
- Do not add real API keys, tokens, or secrets anywhere in the repository.

## UI Assets

This project includes commissioned UI assets.

- Do not replace, redistribute, or re-license assets casually.
- Keep asset changes clearly described in pull requests.
- Preserve transparency, sizing, and runtime compatibility for PNG/GIF resources.

## Issues

When opening an issue, please include as much of the following as possible:

- What you expected
- What actually happened
- Reproduction steps
- Windows version
- Python version
- Whether you launched with `run-floating-summary.bat` or `run-debug.bat`
- Screenshots or error text, if available

## Language

Issues and pull requests in either English or Chinese are welcome.

## Code of Conduct

Please be respectful, practical, and kind.
