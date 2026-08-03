"""Default to the safe, local-only Kakao operator CLI."""

from kakao_bot.runtime.admin_main import main


if __name__ == "__main__":
    raise SystemExit(main())
