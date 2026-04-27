from pathlib import Path


def test_paper_crontab_default_runs_every_minute():
    installer = Path("deploy/cron/install-trading-system-paper-crontab.sh")
    text = installer.read_text(encoding="utf-8")

    assert 'CRON_EXPR="${TRADING_PAPER_CRON_EXPR:-* * * * *}"' in text
    assert "*/5 * * * *" not in text
    assert "*/15 * * * *" not in text
