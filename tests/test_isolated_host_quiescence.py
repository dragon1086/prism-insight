import pytest

from tools import isolated_host_quiescence as probe


def fake_proc(tmp_path, monkeypatch, *, command=b"python\0", mount="/", socket_path=None):
    monkeypatch.setattr(probe.sys, "platform", "linux")
    monkeypatch.setattr(probe.os, "getpid", lambda: 99999)
    proc = tmp_path / "proc"
    pid = proc / "42"
    (pid / "fd").mkdir(parents=True)
    (pid / "ns").mkdir()
    fields = ["S"] + ["0"] * 49
    fields[19] = "12345"
    (pid / "stat").write_text("42 (SECRET_PROCESS_CANARY) " + " ".join(fields))
    (pid / "cmdline").write_bytes(command)
    (pid / "mountinfo").write_text(f"1 0 8:1 {mount} /arm rw - ext4 /dev/root rw\n")
    for name in ("mnt", "pid", "net", "user"):
        (pid / "ns" / name).symlink_to(f"{name}:[{100 + len(name)}]")
    (proc / "net").mkdir()
    (proc / "net" / "unix").write_text("Num RefCount Protocol Flags Type St Inode Path\n" + (
        f"000: 2 0 10000 1 1 900 {socket_path}\n" if socket_path else ""))
    return proc


def test_no_matches_retains_historical_unknown(tmp_path, monkeypatch):
    proc = fake_proc(tmp_path, monkeypatch)
    result = probe._collect(("/registered/stage",), proc)
    assert result["current_matches"] == 0 and result["scanned_process_count"] == 1
    assert result["historical_start_identity"] == "NOT_CAPTURED"
    assert "SECRET_PROCESS_CANARY" not in str(result)


@pytest.mark.parametrize("kind", ["argv", "mount", "fd"])
def test_matches_capture_identity_without_argv_or_paths(tmp_path, monkeypatch, kind):
    root = "/registered/stage"
    proc = fake_proc(tmp_path, monkeypatch,
                     command=(f"python\0{root}/runner.py\0SECRET_ARG_CANARY\0".encode() if kind == "argv" else b"python\0"),
                     mount=root if kind == "mount" else "/")
    if kind == "fd":
        (proc / "42/fd/5").symlink_to(root + "/state.sqlite")
    result = probe._collect((root,), proc)
    assert result["current_matches"] == 1
    assert result["matches"][0]["pid"] == 42
    assert result["matches"][0]["start_ticks"] == 12345
    assert kind in result["matches"][0]["match_sources"]
    assert root not in str(result) and "CANARY" not in str(result)


def test_socket_observation_and_nonlinux_fail_closed(tmp_path, monkeypatch):
    proc = fake_proc(tmp_path, monkeypatch, socket_path="/registered/stage/response.sock")
    result = probe._collect(("/registered/stage",), proc)
    assert result["relevant_socket_count"] == 1
    monkeypatch.setattr(probe.sys, "platform", "darwin")
    with pytest.raises(probe.QuiescenceRejected):
        probe._collect(("/registered/stage",), proc)


def test_pid_churn_or_unreadable_proc_is_not_quiescence(tmp_path, monkeypatch):
    proc = fake_proc(tmp_path, monkeypatch)
    (proc / "42/stat").unlink()
    with pytest.raises(probe.QuiescenceRejected):
        probe._collect(("/registered/stage",), proc)


def test_malformed_or_incomplete_receipts_rejected(tmp_path, monkeypatch):
    proc = fake_proc(tmp_path, monkeypatch)
    receipt = probe._collect(("/registered/stage",), proc)
    assert probe.validate_receipt(receipt) is True
    receipt["current_matches"] = True
    assert probe.validate_receipt(receipt) is False


def test_process_set_churn_is_unknown(tmp_path, monkeypatch):
    proc = fake_proc(tmp_path, monkeypatch)
    calls = []
    def pids(_):
        calls.append(True)
        return [42] if len(calls) == 1 else [42, 43]
    monkeypatch.setattr(probe, "_pids", pids)
    with pytest.raises(probe.QuiescenceRejected):
        probe._collect(("/registered/stage",), proc)


def test_permission_failure_hides_error_content(tmp_path, monkeypatch):
    proc = fake_proc(tmp_path, monkeypatch)
    original = probe._read
    def read(path, bound):
        if path.name == "cmdline":
            raise PermissionError("SECRET_ERROR_CANARY")
        return original(path, bound)
    monkeypatch.setattr(probe, "_read", read)
    with pytest.raises(probe.QuiescenceRejected) as error:
        probe._collect(("/registered/stage",), proc)
    assert "CANARY" not in str(error.value)


def test_mount_escaped_paths_are_not_missed(tmp_path, monkeypatch):
    proc = fake_proc(tmp_path, monkeypatch, mount=r"/registered/my\040stage")
    result = probe._collect(("/registered/my stage",), proc)
    assert result["current_matches"] == 1


def test_orphan_host_provider_outside_stages_with_only_pipe_fds_is_found(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    from tools import codex_probe_sandbox
    code_root = "/external/installed/market_provider"
    verified = []
    def verify(names, *, host_runtime_root):
        verified.append((names, host_runtime_root))
        return {name: {"code_root": code_root if name == "yahoo_finance" else "/external/perplexity"} for name in names}
    monkeypatch.setattr(codex_probe_sandbox, "load_host_manifest", lambda *args: (SimpleNamespace(verified_provider_pins=verify), {}))
    reg = SimpleNamespace(binding=SimpleNamespace(model_root=Path("/registered/model")))
    provider_roots = probe._provider_roots(reg, {"controls": {"market": "US"}},
                                          {"PRISM_PROBE_HOST_MANIFEST": "/registered/host/host-manifest.json"})
    proc = fake_proc(tmp_path, monkeypatch, command=f"/shared/python\0-u\0{code_root}/server.py\0".encode(), mount="/")
    (proc / "42/fd/5").symlink_to("pipe:[1234]")
    roots = ("/registered/model", *(str(path) for path in provider_roots))
    result = probe._collect(roots, proc)
    assert verified and result["current_matches"] == 1
    assert result["matches"][0]["match_sources"] == ["argv"]
    assert code_root not in str(result)
