"""Application entry point."""

from __future__ import annotations

from .app_ui import FloatingSummaryApp


def main() -> int:
    app = FloatingSummaryApp()
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
