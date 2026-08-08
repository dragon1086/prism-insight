from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REMOVED_US_RUNNER = "prism-us/compress_us_trading_memory.py"


def test_docker_cron_uses_only_the_root_weekly_compression_runner():
    crontab = (ROOT / "docker" / "crontab").read_text(encoding="utf-8")

    assert REMOVED_US_RUNNER not in crontab
    assert "python3 compress_trading_memory.py" in crontab


def test_us_crontab_setup_does_not_install_removed_compression_runner():
    setup_script = (ROOT / "utils" / "setup_us_crontab.sh").read_text(encoding="utf-8")

    assert REMOVED_US_RUNNER not in setup_script
    assert "US_MEMORY_COMPRESSION_TIME" not in setup_script
