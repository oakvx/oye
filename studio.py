"""Oye Studio 41: WebView2/browser entry point; no Tkinter or Qt UI required."""
import sys
from oye_web_app import run_app, smoke_test

if __name__ == '__main__':
    if '--smoke-test' in sys.argv:
        raise SystemExit(smoke_test())
    run_app(browser_only='--browser' in sys.argv,
            initial_tab='timeline' if '--timeline' in sys.argv else 'studio' if '--studio' in sys.argv else 'mini')
