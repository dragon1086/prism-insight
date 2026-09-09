"""Parent-loss tests use anonymous pipes and harmless process trees only."""
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time

import pytest

from tools import codex_probe_sandbox as sandbox


def test_closed_parent_pipe_fails_before_spawn():
    reader, writer = os.pipe()
    os.close(writer)
    try:
        with pytest.raises(sandbox.BoundaryError, match="parent_lease_lost"):
            sandbox.ParentLease(reader, os.getppid())
    finally:
        os.close(reader)


def test_parent_lease_rejects_write_end():
    reader, writer = os.pipe()
    try:
        with pytest.raises(sandbox.BoundaryError, match="invalid_parent_lease"):
            sandbox.ParentLease(writer, os.getppid())
    finally:
        os.close(reader)
        os.close(writer)


def test_lost_lease_terminates_and_reaps_only_owned_child():
    reader, writer = os.pipe()
    child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"], close_fds=True)
    sentinel = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"], close_fds=True)
    try:
        lease = sandbox.ParentLease(reader, os.getppid())
        os.close(writer)
        assert sandbox.wait_owned_child(child, lease, threading.Event()) == 2
        assert child.returncode is not None and sentinel.poll() is None
    finally:
        os.close(reader)
        if child.poll() is None:
            child.kill()
        child.wait()
        sentinel.terminate()
        sentinel.wait()


_WRAPPER = r'''
import json,os,socket,subprocess,sys,threading
from tools.codex_probe_sandbox import ParentLease,wait_owned_child
from tools.codex_probe_mcp_bridge import ReadMcpBridge,_host_command
lease=ParentLease(int(sys.argv[1]),int(sys.argv[2]))
stubborn="import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)"
childcode="import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',"+repr(stubborn)+"],start_new_session=True);time.sleep(30)"
provider="import json,subprocess,sys;subprocess.Popen([sys.executable,'-c',"+repr(stubborn)+"],start_new_session=True)\nfor line in sys.stdin:\n r=json.loads(line);print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':{'protocolVersion':'2024-11-05','capabilities':{}}}),flush=True)"
child=None
with ReadMcpBridge(sys.argv[3],server_name='yahoo_finance',argv=[sys.executable,'-u','-c',provider],env={'PATH':'/usr/bin:/bin'},cwd='/',parent_alive=lease.alive) as bridge:
 client=socket.socket(socket.AF_UNIX);client.connect(sys.argv[3]);client.sendall(b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}\n');assert 'result' in json.loads(client.recv(4096))
 try:
  lease.check()
  child=subprocess.Popen(_host_command([sys.executable,'-c',childcode]),env={'PATH':'/usr/bin:/bin'},close_fds=True)
  lease.check()
  print(json.dumps({'wrapper':os.getpid(),'model':child.pid}),flush=True)
  wait_owned_child(child,lease,threading.Event())
 finally:
  client.close()
  if child is not None and child.poll() is None:child.kill();child.wait()
'''


@pytest.mark.skipif(sys.platform != "linux" or not Path("/usr/bin/bwrap").is_file(), reason="real Linux private PID namespace required")
def test_supervisor_sigkill_reaps_model_and_provider_namespaces_without_sentinel():
    """Real lease + bridge + namespace primitives, NOT an actual model run."""
    supervisor_code = r'''
import json,os,subprocess,sys,time
reader,writer=os.pipe()
wrapper=subprocess.Popen([sys.executable,'-u','-c',sys.argv[1],str(reader),str(os.getpid()),sys.argv[2]],pass_fds=(reader,),stdout=subprocess.PIPE,text=True)
os.close(reader)
print(wrapper.stdout.readline(),end='',flush=True)
time.sleep(30)
'''
    def alive(pid):
        try:
            return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1].split()[0] != "Z"
        except FileNotFoundError:
            return False
    def descendants(parent):
        result = {parent}
        for _ in range(8):
            for path in Path("/proc").glob("[0-9]*/stat"):
                try:
                    fields = path.read_text().split(") ", 1)[1].split()
                    if int(fields[1]) in result:
                        result.add(int(path.parent.name))
                except (OSError, ValueError, IndexError):
                    continue
        return result
    sentinel = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
    with tempfile.TemporaryDirectory(prefix="lease-", dir="/tmp") as directory:
        supervisor = subprocess.Popen([sys.executable, "-u", "-c", supervisor_code, _WRAPPER, directory + "/read.sock"],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, close_fds=True)
        tracked = set()
        try:
            assert select.select([supervisor.stdout], [], [], 8)[0], "fixture startup deadline"
            ready = json.loads(supervisor.stdout.readline())
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                tracked = descendants(ready["wrapper"])
                if len(tracked) >= 7:
                    break
                time.sleep(.02)
            assert len(tracked) >= 7, "both namespace descendant trees must exist"
            supervisor.kill()
            supervisor.wait(timeout=3)
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline and any(alive(pid) for pid in tracked):
                time.sleep(.02)
            assert not any(alive(pid) for pid in tracked)
            assert sentinel.poll() is None
        finally:
            if supervisor.poll() is None:
                supervisor.kill()
            supervisor.wait(timeout=3)
            for pid in tracked:
                if alive(pid):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            sentinel.terminate()
            sentinel.wait(timeout=3)
