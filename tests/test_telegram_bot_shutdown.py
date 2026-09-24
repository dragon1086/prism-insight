"""Offline real bot lifecycle with PTB-like queue fetcher and in-flight delivery."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUN = r'''
import asyncio
from contextlib import ExitStack
import signal
import socket
import sys
from unittest.mock import patch

sys.path.insert(0, sys.argv[1])
case = sys.argv[2]
events = []
forbidden = []
def deny(*a, **kw):
    forbidden.append(a)
    raise AssertionError("network forbidden")

with ExitStack() as stack:
    stack.enter_context(patch.object(socket.socket, "connect", deny))
    stack.enter_context(patch.object(socket, "create_connection", deny))
    stack.enter_context(patch("dotenv.load_dotenv", return_value=False))
    import telegram_ai_bot as module

    async def exercise():
        loop = asyncio.get_running_loop()
        callbacks = {}
        stack.enter_context(patch.object(loop, "add_signal_handler", lambda sig, cb: callbacks.setdefault(sig, cb)))
        stack.enter_context(patch.object(loop, "remove_signal_handler", lambda sig: callbacks.pop(sig, None)))
        queue = asyncio.Queue()
        ready = asyncio.Event()
        delivery_started = asyncio.Event()
        release_delivery = asyncio.Event()

        class Updater:
            running = False
            async def start_polling(self):
                self.running = True
                events.append("polling.start")
                if case == "startup_error":
                    raise RuntimeError("polling startup failure")
                ready.set()
            async def stop(self):
                events.append("polling.stop")
                self.running = False

        class App:
            running = False
            updater = Updater()
            async def initialize(self): events.append("initialize")
            async def start(self):
                self.running = True
                events.append("application.start")
                self.fetcher = asyncio.create_task(queue.get())
            async def stop(self):
                events.append("application.stop")
                assert not self.updater.running, "polling must stop first"
                queue.put_nowait("stop")
                await self.fetcher  # reproduces CancelledError if globally cancelled
                self.running = False
                if case == "stop_error":
                    raise RuntimeError("genuine stop failure")
            async def shutdown(self):
                events.append("shutdown")
                assert not self.running

        bot = object.__new__(module.TelegramAIBot)
        bot.stop_event = asyncio.Event()
        bot.application = App()
        async def register(): events.append("commands")
        async def process():
            events.append("delivery.start")
            delivery_started.set()
            await release_delivery.wait()
            events.append("delivery.complete")
        bot._register_bot_commands = register
        bot.process_results = process
        stack.enter_context(patch.object(module, "TelegramAIBot", return_value=bot))
        main_task = asyncio.create_task(module.main())
        if case == "startup_error":
            try:
                await main_task
            except RuntimeError as error:
                assert str(error) == "polling startup failure"
            else: raise AssertionError("startup error hidden")
            assert events[-3:] == ["polling.stop", "application.stop", "shutdown"], events
            return
        await ready.wait()
        await delivery_started.wait()
        # Independent report work must not receive global task cancellation.
        report_done = asyncio.Event()
        async def report():
            await release_delivery.wait()
            report_done.set()
        report_task = asyncio.create_task(report())
        if case == "cancel":
            main_task.cancel()
        else:
            callbacks[signal.SIGTERM]()
            callbacks[signal.SIGTERM]()  # repeated signals are idempotent
        await asyncio.sleep(.02)
        assert not report_task.cancelled(), "report task was globally cancelled"
        assert not main_task.done(), "shutdown abandoned in-flight delivery"
        release_delivery.set()
        try:
            await main_task
        except asyncio.CancelledError:
            assert case == "cancel"
        except RuntimeError as error:
            assert case == "stop_error" and str(error) == "genuine stop failure"
        else:
            assert case == "graceful"
        await report_task
        assert report_done.is_set()
        assert events.index("polling.stop") < events.index("application.stop") < events.index("shutdown"), events
        assert events.index("delivery.complete") < events.index("shutdown"), events
        assert events.count("shutdown") == 1 and not callbacks, events
        assert not forbidden
    asyncio.run(exercise())
'''


@pytest.mark.parametrize("case", ["graceful", "cancel", "startup_error", "stop_error"])
def test_real_bot_shutdown_offline(tmp_path, case):
    result = subprocess.run([sys.executable, "-c", RUN, str(ROOT), case], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30,
                            env=dict(os.environ, PRISM_DISABLE_SIGNAL_PUBLISH="1"))
    assert result.returncode == 0, result.stdout + result.stderr
