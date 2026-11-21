import asyncio

from prism_core import ops_alert


def test_a_test_run_cannot_reach_the_maintenance_chat():
    from dotenv import load_dotenv

    load_dotenv()  # what importing a trading module does
    assert ops_alert._target() == ("", "")
    assert asyncio.run(ops_alert.send_ops_alert("test")) is False
