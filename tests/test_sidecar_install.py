"""One-click sidecar-engine provisioner (services.sidecar_install).

The provisioner replaces IndexTTS-2's four manual terminal steps (clone,
venv, `uv pip install -e .`, set OMNIVOICE_INDEXTTS_DIR) with a resumable
background job. These tests run the job with git/uv/httpx/HF mocked and
cover: the happy path, the disk-space preflight, the git-absent tarball
fallback, partial-install repair, already-installed detection, uninstall
safety (never delete a user's own clone), and the router wiring.
"""
import io
import os
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest

from services import sidecar_install as si


_GIB = 1024 ** 3


# ── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch, tmp_path):
    """Hermetic per-test state: managed root under tmp, no leaked jobs/env.

    Rebinds the module-level ``si`` to the LIVE ``services.sidecar_install``
    module: other suites purge ``sys.modules["services"]`` for DB isolation,
    so the object imported at collection time can differ from the one the
    engines router imports at call time — patching the stale copy would make
    the router tests order-dependent.
    """
    import importlib
    global si
    si = importlib.import_module("services.sidecar_install")
    monkeypatch.setattr(si, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(si, "_jobs", {})
    monkeypatch.delenv("OMNIVOICE_INDEXTTS_DIR", raising=False)
    monkeypatch.delenv("OMNIVOICE_FAKE_SIDE_DIR", raising=False)
    monkeypatch.delenv("OMNIVOICE_DESKTOP_CONTAINED", raising=False)
    # Set-then-delete: a bare delenv of an unset var records nothing to
    # restore, so a path an install test persists would leak into later
    # suites (an engine would then find a venv that no longer exists).
    for spec in si.SPECS.values():
        monkeypatch.setenv(spec.env_var, "")
        monkeypatch.delenv(spec.env_var)
    yield


def _mk_spec(**over) -> si.SidecarSpec:
    calls = over.pop("_calls", {})
    defaults = dict(
        engine_id="fake-side",
        display_name="Fake Sidecar",
        repo_url="https://github.com/example/fake-side.git",
        tarball_url="https://github.com/example/fake-side/archive/refs/heads/main.tar.gz",
        checkout_dirname="fake-side",
        env_var="OMNIVOICE_FAKE_SIDE_DIR",
        probe_module="fake_side.infer",
        weights_repo_id=None,
        required_bytes=1 * _GIB,
        invalidate=lambda: calls.setdefault("invalidated", 0) or calls.update(
            invalidated=calls.get("invalidated", 0) + 1
        ),
        installed_probe=lambda: False,
    )
    defaults.update(over)
    return si.SidecarSpec(**defaults)


def _fake_run_logged(created: list):
    """A _run_logged stand-in that fabricates git/uv side effects on disk."""

    def run(job, argv, *, timeout, env=None):
        created.append(argv)
        prog = os.path.basename(argv[0])
        if prog.startswith("git") and argv[1] == "clone":
            checkout = Path(argv[-1])
            checkout.mkdir(parents=True, exist_ok=True)
            (checkout / "pyproject.toml").write_text("[project]\nname='fake'\n")
        elif prog.startswith("uv") and argv[1] == "venv":
            venv = Path(argv[2])
            py = si._venv_python(venv)
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text("#!fake python\n")
            if job["engine_id"] == "dots-tts":
                constraints = venv.parent / "constraints" / "recommended.txt"
                constraints.parent.mkdir(parents=True, exist_ok=True)
                constraints.write_text("six==1.17.0\n")
        # uv pip install: nothing to fabricate
        return 0

    return run


def _stub_verify_ok(monkeypatch):
    monkeypatch.setattr(
        si.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stderr=b"", stdout=b""),
    )


def _run(spec):
    job = si._new_job(spec.engine_id)
    si._run_install(spec, job)
    return job


def _step_states(job):
    return {s["id"]: s["state"] for s in job["steps"]}


# ── happy path ─────────────────────────────────────────────────────────────


def test_happy_path_installs_and_persists(monkeypatch):
    calls = {}
    prefs_written = {}
    spec = _mk_spec(_calls=calls)
    argvs = []
    monkeypatch.setattr(si, "_locate_uv", lambda: "/fake/uv")
    monkeypatch.setattr(si, "disk_free_bytes", lambda p: 100 * _GIB)
    monkeypatch.setattr(si.shutil, "which", lambda n: "/usr/bin/git" if n == "git" else None)
    monkeypatch.setattr(si, "_run_logged", _fake_run_logged(argvs))
    _stub_verify_ok(monkeypatch)
    monkeypatch.setattr("core.prefs.set_", lambda k, v: prefs_written.update({k: v}))

    job = _run(spec)

    assert job["state"] == "succeeded", (job["error"], list(job["log"]))
    states = _step_states(job)
    assert states["preflight"] == "done"
    assert states["fetch_source"] == "done"
    assert states["create_venv"] == "done"
    assert states["install_deps"] == "done"
    assert states["verify"] == "done"
    assert states["fetch_weights"] == "skipped"       # no weights_repo_id
    assert states["persist"] == "done"
    checkout = si.managed_checkout(spec)
    # Engine usable immediately: env var set in THIS process…
    assert os.environ["OMNIVOICE_FAKE_SIDE_DIR"] == str(checkout)
    # …and persisted for the next launch via the env.* prefs mechanism.
    assert prefs_written == {"env.OMNIVOICE_FAKE_SIDE_DIR": str(checkout)}
    # Memoised venv resolution invalidated so it re-probes without restart.
    assert calls.get("invalidated") == 1
    # uv pip install targeted the sidecar venv's own python (isolation
    # preserved — the parent app's env is never touched).
    pip = next(a for a in argvs if a[1:3] == ["pip", "install"])
    assert pip[pip.index("--python") + 1] == str(si._venv_python(checkout / ".venv"))
    assert pip[-1] == str(checkout)


def test_rerun_after_success_skips_completed_steps(monkeypatch):
    spec = _mk_spec()
    argvs = []
    monkeypatch.setattr(si, "_locate_uv", lambda: "/fake/uv")
    monkeypatch.setattr(si, "disk_free_bytes", lambda p: 100 * _GIB)
    monkeypatch.setattr(si.shutil, "which", lambda n: "/usr/bin/git" if n == "git" else None)
    monkeypatch.setattr(si, "_run_logged", _fake_run_logged(argvs))
    _stub_verify_ok(monkeypatch)
    monkeypatch.setattr("core.prefs.set_", lambda k, v: None)

    assert _run(spec)["state"] == "succeeded"
    argvs.clear()
    job2 = _run(spec)
    assert job2["state"] == "succeeded"
    # No re-clone, no re-venv; only the idempotent pip repair pass runs.
    assert all(a[1:3] == ["pip", "install"] for a in argvs), argvs
    states = _step_states(job2)
    assert states["fetch_source"] == "done" and states["create_venv"] == "done"


# ── disk-space preflight ───────────────────────────────────────────────────


def test_disk_space_preflight_fails_early_with_numbers(monkeypatch):
    spec = _mk_spec(required_bytes=10 * _GIB)
    monkeypatch.setattr(si, "_locate_uv", lambda: "/fake/uv")
    monkeypatch.setattr(si, "disk_free_bytes", lambda p: 2 * _GIB)

    job = _run(spec)

    assert job["state"] == "failed"
    assert _step_states(job)["preflight"] == "error"
    # Nothing after preflight ran.
    assert _step_states(job)["fetch_source"] == "pending"
    # The error names what's needed and what's free, so the user can act.
    assert "10.0 GB" in job["error"] and "2.0 GB" in job["error"]
    assert "disk space" in job["remediation"].lower() or "disk space" in job["error"].lower()


def test_disk_preflight_subtracts_partial_install(monkeypatch, tmp_path):
    # 1 GiB required, 0.9 GiB already on disk from a prior partial run →
    # only the remainder (+headroom) must fit, so a resume isn't blocked.
    spec = _mk_spec(required_bytes=1 * _GIB)
    root = si.managed_root(spec)
    checkout = si.managed_checkout(spec)
    root.mkdir(parents=True)
    monkeypatch.setattr(si, "_source_present", lambda _spec, _checkout: True)
    monkeypatch.setattr(
        si,
        "_dir_size_bytes",
        lambda path: int(0.9 * _GIB) if path == checkout else 0,
    )
    monkeypatch.setattr(si, "disk_free_bytes", lambda p: (si.MIN_FREE_GB + 1) * _GIB)
    assert si.disk_space_error(spec) is None


def test_disk_preflight_subtracts_partial_install_from_peak_requirement(monkeypatch):
    spec = _mk_spec(required_bytes=1 * _GIB, temporary_free_bytes=4 * _GIB)
    checkout = si.managed_checkout(spec)
    monkeypatch.setattr(si, "_source_present", lambda _spec, _checkout: True)
    monkeypatch.setattr(
        si,
        "_dir_size_bytes",
        lambda path: int(0.9 * _GIB) if path == checkout else 0,
    )
    monkeypatch.setattr(si, "disk_free_bytes", lambda _path: (si.MIN_FREE_GB + 2) * _GIB)
    assert "3.1 GB" in si.disk_space_error(spec)


def test_disk_preflight_does_not_credit_weights_against_dependency_peak(monkeypatch):
    spec = _mk_spec(
        required_bytes=12 * _GIB,
        temporary_free_bytes=12 * _GIB,
        weights_repo_id="Example/Weights",
    )
    checkout = si.managed_checkout(spec)
    weights = checkout / spec.weights_subdir
    monkeypatch.setattr(si, "_source_present", lambda _spec, _checkout: True)
    monkeypatch.setattr(
        si,
        "_dir_size_bytes",
        lambda path: 7 * _GIB if path == checkout else 6 * _GIB if path == weights else 0,
    )
    monkeypatch.setattr(si, "disk_free_bytes", lambda _path: (si.MIN_FREE_GB + 10) * _GIB)

    assert "11.0 GB" in si.disk_space_error(spec)


def test_disk_preflight_does_not_credit_checkout_that_fetch_will_delete(monkeypatch):
    spec = _mk_spec(required_bytes=4 * _GIB, source_revision="new-revision")
    checkout = si.managed_checkout(spec)
    checkout.mkdir(parents=True)
    (checkout / "pyproject.toml").write_text("[project]\nname='fake'\n")
    (checkout / si._SOURCE_REVISION_MARKER).write_text("stale-revision\n")
    monkeypatch.setattr(si, "_dir_size_bytes", lambda _path: 3 * _GIB)
    monkeypatch.setattr(si, "disk_free_bytes", lambda _path: (si.MIN_FREE_GB + 2) * _GIB)

    assert "4.0 GB" in si.disk_space_error(spec)


def test_missing_uv_is_actionable(monkeypatch):
    spec = _mk_spec()
    monkeypatch.setattr(si, "_locate_uv", lambda: None)
    job = _run(spec)
    assert job["state"] == "failed"
    assert "uv" in job["error"]
    assert "docs.astral.sh/uv" in job["remediation"]


# ── git-absent tarball fallback ────────────────────────────────────────────


def _tarball_bytes(root_name: str, with_pyproject: bool = True) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        if with_pyproject:
            data = b"[project]\nname='fake'\n"
            info = tarfile.TarInfo(f"{root_name}/pyproject.toml")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        data2 = b"print('hi')\n"
        info2 = tarfile.TarInfo(f"{root_name}/fake_side/__init__.py")
        info2.size = len(data2)
        tf.addfile(info2, io.BytesIO(data2))
    return buf.getvalue()


class _FakeStream:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        return None

    def iter_bytes(self):
        yield self._payload


def test_git_absent_falls_back_to_tarball(monkeypatch):
    spec = _mk_spec()
    urls = []
    monkeypatch.setattr(si.shutil, "which", lambda n: None)   # no git anywhere
    import httpx

    def fake_stream(method, url, **kw):
        urls.append((method, url))
        return _FakeStream(_tarball_bytes("fake-side-main"))

    monkeypatch.setattr(httpx, "stream", fake_stream)

    job = si._new_job(spec.engine_id)
    step = si._job_step(job, "fetch_source")
    step["state"] = "running"
    si._step_fetch_source(spec, job)

    assert urls == [("GET", spec.tarball_url)]
    checkout = si.managed_checkout(spec)
    assert (checkout / "pyproject.toml").is_file()
    assert (checkout / "fake_side" / "__init__.py").is_file()
    assert step["detail"] == "source tarball"


def test_git_failure_falls_back_to_tarball(monkeypatch):
    """A present-but-failing git (proxy block, DNS, …) must not dead-end."""
    spec = _mk_spec()
    monkeypatch.setattr(si.shutil, "which", lambda n: "/usr/bin/git" if n == "git" else None)
    monkeypatch.setattr(si, "_run_logged", lambda job, argv, timeout: 128)  # git exits 128
    import httpx
    monkeypatch.setattr(
        httpx, "stream",
        lambda m, u, **k: _FakeStream(_tarball_bytes("fake-side-main")),
    )
    job = si._new_job(spec.engine_id)
    si._job_step(job, "fetch_source")["state"] = "running"
    si._step_fetch_source(spec, job)
    assert (si.managed_checkout(spec) / "pyproject.toml").is_file()


def test_legacy_kill_fallback_uses_only_direct_stable_handle(monkeypatch):
    calls = {}
    monkeypatch.setattr(si.os, "name", "nt")
    monkeypatch.setattr(
        si.subprocess, "run",
        lambda argv, **kw: calls.setdefault("argv", argv) or SimpleNamespace(returncode=0),
    )
    proc = SimpleNamespace(pid=4242, kill=lambda: calls.setdefault("plain_kill", True))
    si._kill_tree(proc)
    assert calls == {"plain_kill": True}


def test_desktop_installer_needs_no_unmanaged_spawn_flags(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_DESKTOP_CONTAINED", "1")
    monkeypatch.setattr(si.os, "name", "posix")
    assert si._install_containment_kwargs() == {}


def test_standalone_installer_also_delegates_to_nested_owner(monkeypatch):
    monkeypatch.delenv("OMNIVOICE_DESKTOP_CONTAINED", raising=False)
    monkeypatch.setattr(si.os, "name", "posix")
    assert si._install_containment_kwargs() == {}


def test_desktop_windows_timeout_never_taskkills_a_reusable_pid(monkeypatch):
    calls = {}
    monkeypatch.setenv("OMNIVOICE_DESKTOP_CONTAINED", "1")
    monkeypatch.setattr(si.os, "name", "nt")
    monkeypatch.setattr(
        si.subprocess,
        "run",
        lambda *args, **kwargs: calls.setdefault("taskkill", True),
    )
    proc = SimpleNamespace(pid=4242, kill=lambda: calls.setdefault("handle_kill", True))
    si._kill_tree(proc)
    assert calls == {"handle_kill": True}


def test_windows_job_timeout_waits_for_terminated_tree():
    events = []

    def timed_out_wait(timeout=None):
        events.append(("wait", timeout))
        raise subprocess.TimeoutExpired("operation.exe", timeout)

    child = SimpleNamespace(
        stdin=None,
        stdout=None,
        stderr=None,
        wait=timed_out_wait,
    )
    kernel = SimpleNamespace(
        TerminateJobObject=lambda job, code: events.append(("terminate", job, code)),
        CloseHandle=lambda job: events.append(("close", job)),
    )
    proc = si.WindowsJobPopen(child, 99, kernel)

    si._kill_tree(proc)

    assert events == [("terminate", 99, 1), ("close", 99), ("wait", 5)]


def test_desktop_installer_timeout_kills_nested_helper_before_it_can_mutate(
    monkeypatch, tmp_path
):
    """A timed-out uv/git root must not leave its pipe-owning helpers alive."""
    marker = tmp_path / "late-mutation"
    monkeypatch.setenv("OMNIVOICE_DESKTOP_CONTAINED", "1")
    drain_read, drain_write = os.pipe()
    monkeypatch.setenv("OMNIVOICE_DESKTOP_DRAIN_FD", str(drain_write))
    child_script = (
        "import os,time; time.sleep(1); "
        "open(os.environ['OMNIVOICE_TIMEOUT_MARKER'], 'w').write('bad')"
    )
    script = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {child_script!r}]); "
        "time.sleep(60)"
    )
    env = os.environ.copy()
    env["OMNIVOICE_TIMEOUT_MARKER"] = str(marker)
    job = si._new_job("fake-side")

    try:
        assert si._run_logged(job, [sys.executable, "-c", script], timeout=0.2, env=env) == -1
        time.sleep(1.2)
        assert not marker.exists()
    finally:
        os.close(drain_write)
        os.close(drain_read)


def test_backend_death_closes_control_pipe_and_kills_nested_operation(tmp_path):
    """Outer desktop teardown reaches an operation which owns a nested group."""
    marker = tmp_path / "mutation-after-backend-death"
    operation = (
        "import os,time; time.sleep(1); "
        "open(os.environ['OMNIVOICE_TIMEOUT_MARKER'], 'w').write('bad')"
    )
    backend = (
        "import os,sys,time; "
        "from core.contained_subprocess import spawn_owned; "
        f"spawn_owned([sys.executable, '-c', {operation!r}]); "
        "time.sleep(.2); os._exit(0)"
    )
    env = os.environ.copy()
    env["OMNIVOICE_DESKTOP_CONTAINED"] = "1"
    env["OMNIVOICE_TIMEOUT_MARKER"] = str(marker)
    env["PYTHONPATH"] = str(Path(__file__).parents[1] / "backend")

    run_kwargs = {}
    drain_fds = None
    if os.name == "posix":
        drain_fds = os.pipe()
        env["OMNIVOICE_DESKTOP_DRAIN_FD"] = str(drain_fds[1])
        run_kwargs["pass_fds"] = (drain_fds[1],)
    assert subprocess.run([sys.executable, "-c", backend], env=env, **run_kwargs).returncode == 0
    if drain_fds is not None:
        os.close(drain_fds[1])
        os.close(drain_fds[0])
    time.sleep(1.2)
    assert not marker.exists()


def test_safe_extract_members_blocks_tar_slip(tmp_path):
    """The pre-filter= fallback extractor must drop parent-dir escapes,
    absolute paths, and symlinks — mirroring extractall(filter='data')."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        good = tarfile.TarInfo("pkg/ok.txt")
        good.size = 2
        tf.addfile(good, io.BytesIO(b"ok"))
        evil = tarfile.TarInfo("../evil.txt")
        evil.size = 4
        tf.addfile(evil, io.BytesIO(b"pwnd"))
        absolute = tarfile.TarInfo("/abs.txt")
        absolute.size = 3
        tf.addfile(absolute, io.BytesIO(b"abs"))
        link = tarfile.TarInfo("pkg/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tf.addfile(link)
    buf.seek(0)
    dest = tmp_path / "sandbox" / "out"
    dest.mkdir(parents=True)
    with tarfile.open(fileobj=buf, mode="r:gz") as tf:
        si._safe_extract_members(tf, str(dest))
    assert (dest / "pkg" / "ok.txt").read_text() == "ok"
    assert not (tmp_path / "sandbox" / "evil.txt").exists()
    assert not (dest / "pkg" / "link").exists()


def test_tarball_without_pyproject_fails_with_remediation(monkeypatch):
    spec = _mk_spec()
    monkeypatch.setattr(si.shutil, "which", lambda n: None)
    import httpx
    monkeypatch.setattr(
        httpx, "stream",
        lambda m, u, **k: _FakeStream(_tarball_bytes("fake-side-main", with_pyproject=False)),
    )
    job = si._new_job(spec.engine_id)
    si._job_step(job, "fetch_source")["state"] = "running"
    with pytest.raises(si._StepError) as ei:
        si._step_fetch_source(spec, job)
    assert "pyproject.toml" in str(ei.value)
    assert spec.env_var in ei.value.remediation


# ── partial-install repair ─────────────────────────────────────────────────


def test_half_fetched_checkout_is_refetched(monkeypatch):
    """A checkout without pyproject.toml (killed mid-clone) is wiped and
    re-fetched instead of being trusted or corrupting the install."""
    spec = _mk_spec()
    checkout = si.managed_checkout(spec)
    (checkout / "leftover").mkdir(parents=True)
    (checkout / "leftover" / "junk.txt").write_text("stale")
    argvs = []
    monkeypatch.setattr(si.shutil, "which", lambda n: "/usr/bin/git" if n == "git" else None)
    monkeypatch.setattr(si, "_run_logged", _fake_run_logged(argvs))

    job = si._new_job(spec.engine_id)
    si._job_step(job, "fetch_source")["state"] = "running"
    si._step_fetch_source(spec, job)

    assert (checkout / "pyproject.toml").is_file()
    assert not (checkout / "leftover").exists()
    assert any(a[1] == "clone" for a in argvs)


def _write_weights(
    wdir: Path,
    *,
    complete: bool,
    repo_id: str = "Example/Weights",
    revision: str = "",
    config_name: str = "config.yaml",
) -> None:
    """Fabricate a weights dir; ``complete=True`` adds the completion marker
    the installer writes after snapshot_download returns."""
    wdir.mkdir(parents=True, exist_ok=True)
    (wdir / config_name).write_text("model: fake\n")
    (wdir / "weights.safetensors").write_bytes(b"\0" * (6 * 1024 * 1024))
    if complete:
        (wdir / si._WEIGHTS_COMPLETE_MARKER).write_text(
            f"{repo_id}\n{revision}\n",
        )


def test_partial_install_is_not_already_installed(monkeypatch):
    """venv present but weights missing → NOT healthy → a re-run repairs it
    instead of short-circuiting with already_installed."""
    spec = _mk_spec(weights_repo_id="Example/Weights")
    checkout = si.managed_checkout(spec)
    checkout.mkdir(parents=True, exist_ok=True)
    (checkout / "pyproject.toml").write_text("[project]\nname='fake'\n")
    py = si._venv_python(checkout / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("#!fake\n")
    assert si._healthy(spec) is False

    # Complete the weights (incl. the completion marker) and the install
    # (the marker the import probe writes) → healthy flips true.
    _write_weights(checkout / spec.weights_subdir, complete=True)
    assert si._healthy(spec) is False
    (checkout / si._INSTALL_COMPLETE_MARKER).write_text("x\n", encoding="utf-8")
    assert si._healthy(spec) is True


def test_interrupted_multishard_weights_are_not_healthy(monkeypatch):
    """Regression: a killed-mid-download weights dir can hold config.yaml +
    plausible shards, but WITHOUT the completion marker it must stay
    unhealthy so a re-run resumes the download instead of reporting
    already_installed and failing later at model-load time."""
    spec = _mk_spec(weights_repo_id="Example/Weights")
    checkout = si.managed_checkout(spec)
    checkout.mkdir(parents=True, exist_ok=True)
    (checkout / "pyproject.toml").write_text("[project]\nname='fake'\n")
    py = si._venv_python(checkout / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("#!fake\n")
    _write_weights(checkout / spec.weights_subdir, complete=False)
    assert si._weights_present(spec) is False
    assert si._healthy(spec) is False
    monkeypatch.setitem(si.SPECS, "fake-side", spec)
    monkeypatch.setattr(si, "_run_install", lambda s, j: None)
    assert si.start_install("fake-side")["status"] == "started"  # repairs, not skips


def test_weights_step_downloads_via_endpoint_autoselect(monkeypatch):
    """The weights download must ride snapshot_download with the endpoint
    from services.endpoint_race — never a hardcoded huggingface.co URL."""
    spec = _mk_spec(weights_repo_id="Example/Weights")
    wdir = si.managed_checkout(spec) / spec.weights_subdir
    seen = {}

    def fake_snapshot_download(**kwargs):
        seen.update(kwargs)
        Path(kwargs["local_dir"]).mkdir(parents=True, exist_ok=True)
        (Path(kwargs["local_dir"]) / "config.yaml").write_text("ok\n")
        (Path(kwargs["local_dir"]) / "w.safetensors").write_bytes(b"\0" * (6 * 1024 * 1024))

    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)
    from services import endpoint_race
    monkeypatch.setattr(endpoint_race, "effective_endpoint", lambda: "https://hf-mirror.example")
    monkeypatch.setattr("services.token_resolver.resolve", lambda: None)

    job = si._new_job(spec.engine_id)
    si._job_step(job, "fetch_weights")["state"] = "running"
    si._step_fetch_weights(spec, job)

    assert seen["repo_id"] == "Example/Weights"
    assert seen["endpoint"] == "https://hf-mirror.example"
    assert seen["local_dir"] == str(wdir)
    assert si._weights_present(spec)


def test_weights_download_sends_the_bearer_string_not_the_token_record(monkeypatch):
    """#2163: `token_resolver.resolve()` returns a ResolvedToken record, but
    snapshot_download takes `token: str | None` and silently ignores a non-str
    — falling back to huggingface_hub's own ambient token discovery. So gated
    engine weights 401 for a user whose token lives in Voiceym's Settings
    rather than HF's cache. Every other weights test here stubs resolve() to
    None, which is exactly why this went unnoticed."""
    from services.token_resolver import ResolvedToken

    spec = _mk_spec(weights_repo_id="Example/Gated")
    seen = {}

    def fake_snapshot_download(**kwargs):
        seen.update(kwargs)
        Path(kwargs["local_dir"]).mkdir(parents=True, exist_ok=True)
        (Path(kwargs["local_dir"]) / "config.yaml").write_text("ok\n")
        (Path(kwargs["local_dir"]) / "w.safetensors").write_bytes(b"\0" * (6 * 1024 * 1024))

    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)
    monkeypatch.setattr("services.endpoint_race.effective_endpoint", lambda: None)
    monkeypatch.setattr(
        "services.token_resolver.resolve",
        lambda: ResolvedToken(token="hf_gatedsecret", source="app", username="tester"),
    )

    job = si._new_job(spec.engine_id)
    si._job_step(job, "fetch_weights")["state"] = "running"
    si._step_fetch_weights(spec, job)

    assert seen["token"] == "hf_gatedsecret"
    assert isinstance(seen["token"], str)


def test_weights_download_sends_no_token_when_none_resolves(monkeypatch):
    # The other half of the contract: no token anywhere must reach
    # snapshot_download as a real None, never the string "None".
    spec = _mk_spec(weights_repo_id="Example/Open")
    seen = {}

    def fake_snapshot_download(**kwargs):
        seen.update(kwargs)
        Path(kwargs["local_dir"]).mkdir(parents=True, exist_ok=True)
        (Path(kwargs["local_dir"]) / "config.yaml").write_text("ok\n")
        (Path(kwargs["local_dir"]) / "w.safetensors").write_bytes(b"\0" * (6 * 1024 * 1024))

    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)
    monkeypatch.setattr("services.endpoint_race.effective_endpoint", lambda: None)
    monkeypatch.setattr("services.token_resolver.resolve", lambda: None)

    job = si._new_job(spec.engine_id)
    si._job_step(job, "fetch_weights")["state"] = "running"
    si._step_fetch_weights(spec, job)

    assert seen["token"] is None


def test_weights_revision_is_pinned_and_old_marker_forces_upgrade(monkeypatch):
    spec = _mk_spec(
        weights_repo_id="Example/Weights",
        weights_revision="a" * 40,
    )
    wdir = si.managed_checkout(spec) / spec.weights_subdir
    _write_weights(wdir, complete=True)  # legacy default-branch marker
    assert si._weights_present(spec) is False

    seen = {}

    def fake_snapshot_download(**kwargs):
        seen.update(kwargs)
        _write_weights(
            Path(kwargs["local_dir"]), complete=False,
            repo_id=kwargs["repo_id"], revision=kwargs["revision"],
        )

    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)
    monkeypatch.setattr("services.endpoint_race.effective_endpoint", lambda: None)
    monkeypatch.setattr("services.token_resolver.resolve", lambda: None)
    job = si._new_job(spec.engine_id)
    si._job_step(job, "fetch_weights")["state"] = "running"
    si._step_fetch_weights(spec, job)
    assert seen["revision"] == "a" * 40
    assert si._weights_present(spec) is True


def test_weights_step_skips_when_already_present(monkeypatch):
    spec = _mk_spec(weights_repo_id="Example/Weights")
    wdir = si.managed_checkout(spec) / spec.weights_subdir
    _write_weights(wdir, complete=True)
    import huggingface_hub

    def boom(**kw):  # pragma: no cover — must not be reached
        raise AssertionError("snapshot_download must not run when weights exist")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", boom)
    job = si._new_job(spec.engine_id)
    si._job_step(job, "fetch_weights")["state"] = "running"
    si._step_fetch_weights(spec, job)
    assert si._job_step(job, "fetch_weights")["state"] == "done"


# ── already-installed / already-running gating ────────────────────────────


def test_healthy_managed_install_reheals_lost_env_var(monkeypatch):
    """A complete managed install whose env var vanished (prefs.json wiped)
    is re-pointed by start_install instead of being reinstalled — and instead
    of returning already_installed while the engine stays unavailable."""
    spec = _mk_spec(weights_repo_id="Example/Weights")
    monkeypatch.setitem(si.SPECS, "fake-side", spec)
    checkout = si.managed_checkout(spec)
    checkout.mkdir(parents=True, exist_ok=True)
    (checkout / "pyproject.toml").write_text("[project]\nname='fake'\n")
    py = si._venv_python(checkout / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("#!fake\n")
    _write_weights(checkout / spec.weights_subdir, complete=True)
    (checkout / si._INSTALL_COMPLETE_MARKER).write_text("x\n", encoding="utf-8")
    prefs_written = {}
    monkeypatch.setattr("core.prefs.set_", lambda k, v: prefs_written.update({k: v}))
    assert "OMNIVOICE_FAKE_SIDE_DIR" not in os.environ

    res = si.start_install("fake-side")

    assert res["status"] == "already_installed"
    assert os.environ["OMNIVOICE_FAKE_SIDE_DIR"] == str(checkout)
    assert prefs_written == {"env.OMNIVOICE_FAKE_SIDE_DIR": str(checkout)}


def test_start_install_reports_already_installed_for_user_clone(monkeypatch):
    spec = _mk_spec(installed_probe=lambda: True)
    monkeypatch.setitem(si.SPECS, "fake-side", spec)
    monkeypatch.setenv("OMNIVOICE_FAKE_SIDE_DIR", "/home/user/own-clone")
    res = si.start_install("fake-side")
    assert res["status"] == "already_installed"


def test_start_install_reports_already_running(monkeypatch):
    spec = _mk_spec()
    monkeypatch.setitem(si.SPECS, "fake-side", spec)
    ran = threading.Event()
    monkeypatch.setattr(si, "_run_install", lambda s, j: ran.set())
    first = si.start_install("fake-side")
    assert first["status"] == "started"
    # Freeze the job as running to simulate the in-flight window.
    si._jobs["fake-side"]["state"] = "running"
    assert si.start_install("fake-side")["status"] == "already_running"
    assert ran.wait(5)


def test_start_install_unknown_engine_raises_keyerror():
    with pytest.raises(KeyError):
        si.start_install("definitely-not-an-engine")


def test_get_status_synthesizes_state_without_job(monkeypatch):
    spec = _mk_spec()
    monkeypatch.setitem(si.SPECS, "fake-side", spec)
    st = si.get_status("fake-side")
    assert st == {
        "engine_id": "fake-side",
        "installed": False,
        "managed": False,
        "install_dir": None,
        "job": None,
    }


# ── uninstall ──────────────────────────────────────────────────────────────


def test_uninstall_removes_managed_install_and_prefs(monkeypatch):
    spec = _mk_spec()
    monkeypatch.setitem(si.SPECS, "fake-side", spec)
    checkout = si.managed_checkout(spec)
    py = si._venv_python(checkout / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("#!fake\n")
    monkeypatch.setenv("OMNIVOICE_FAKE_SIDE_DIR", str(checkout))
    deleted = []
    monkeypatch.setattr("core.prefs.get", lambda k, d=None: str(checkout))
    monkeypatch.setattr("core.prefs.delete", lambda k: deleted.append(k))

    res = si.uninstall("fake-side")

    assert res["status"] == "uninstalled"
    assert not si.managed_root(spec).exists()
    assert "OMNIVOICE_FAKE_SIDE_DIR" not in os.environ
    assert deleted == ["env.OMNIVOICE_FAKE_SIDE_DIR"]


def test_uninstall_refuses_user_managed_clone(monkeypatch, tmp_path):
    spec = _mk_spec()
    monkeypatch.setitem(si.SPECS, "fake-side", spec)
    user_clone = tmp_path / "my-own-clone"
    user_clone.mkdir()
    monkeypatch.setenv("OMNIVOICE_FAKE_SIDE_DIR", str(user_clone))
    res = si.uninstall("fake-side")
    assert res["status"] == "not_managed"
    assert user_clone.exists()                      # never deleted
    assert os.environ["OMNIVOICE_FAKE_SIDE_DIR"] == str(user_clone)  # never cleared


def test_uninstall_refuses_while_job_running(monkeypatch):
    spec = _mk_spec()
    monkeypatch.setitem(si.SPECS, "fake-side", spec)
    si._jobs["fake-side"] = si._new_job("fake-side")
    assert si.uninstall("fake-side")["status"] == "install_in_progress"


# ── indextts2 spec wiring (the engine this ships for) ─────────────────────


# ── router wiring ──────────────────────────────────────────────────────────


def test_sidecar_routes_never_shadow_literal_engine_routes():
    """Regression: the engines router registers BEFORE literal-path routers
    (e.g. sonitranslate), so a dynamic ``/engines/{engine_id}/install`` here
    would swallow ``POST /engines/sonitranslate/install``. The sidecar
    installer must keep its own literal namespace (/engines/sidecar/…)."""
    from api.routers import engines as engines_router
    install_paths = [
        r.path for r in engines_router.router.routes if "install" in r.path
    ]
    assert install_paths, "sidecar install routes missing"
    for p in install_paths:
        assert not p.startswith("/engines/{"), (
            f"{p} would shadow literal /engines/<x>/install routes registered later"
        )


def test_router_404s_engines_without_installer():
    from fastapi import HTTPException
    from api.routers import engines as engines_router
    with pytest.raises(HTTPException) as ei:
        engines_router.install_sidecar_engine("omnivoice")
    assert ei.value.status_code == 404
    with pytest.raises(HTTPException) as ei:
        engines_router.sidecar_install_status("omnivoice")
    assert ei.value.status_code == 404
    with pytest.raises(HTTPException) as ei:
        engines_router.uninstall_sidecar_engine("omnivoice")
    assert ei.value.status_code == 404


def test_router_uninstall_maps_refusals_to_http_errors(monkeypatch):
    from fastapi import HTTPException
    from api.routers import engines as engines_router
    spec = _mk_spec()
    monkeypatch.setitem(si.SPECS, "fake-side", spec)
    si._jobs["fake-side"] = si._new_job("fake-side")
    with pytest.raises(HTTPException) as ei:
        engines_router.uninstall_sidecar_engine("fake-side")
    assert ei.value.status_code == 409
    si._jobs.pop("fake-side")
    monkeypatch.setenv("OMNIVOICE_FAKE_SIDE_DIR", "/somewhere/else")
    with pytest.raises(HTTPException) as ei:
        engines_router.uninstall_sidecar_engine("fake-side")
    assert ei.value.status_code == 400


# ── isolation: switching engines can never corrupt another engine ─────────
#
# Every one-click engine owns DATA_DIR/engines/<id>/ — its checkout and its
# .venv — and switching the active engine only changes a pref. So going back
# to an engine that worked is safe for exactly as long as no install ever
# writes outside its own root. These tests pin that for every spec, including
# ones added later.

_ALL_SPEC_IDS = sorted(si.SPECS)
_PYTORCH_INDEX = "https://download.pytorch.org/whl/cu128"


def _capture_install_argvs(monkeypatch, family="cuda"):
    argvs = []
    monkeypatch.setattr(si, "_locate_uv", lambda: "/fake/uv")
    monkeypatch.setattr(si, "_host_family", lambda: family)
    monkeypatch.setattr(si, "_run_logged", _fake_run_logged(argvs))
    return argvs


@pytest.mark.parametrize("engine_id", _ALL_SPEC_IDS)
def test_every_spec_installs_only_into_its_own_venv(monkeypatch, engine_id):
    spec = si.get_spec(engine_id)
    argvs = _capture_install_argvs(monkeypatch)
    job = si._new_job(engine_id)

    si._step_create_venv(spec, job)
    si._step_install_deps(spec, job)

    assert si.managed_root(spec) == Path(si.DATA_DIR) / "engines" / engine_id
    venv = si.managed_checkout(spec) / ".venv"
    venv_cmd = next(a for a in argvs if a[1] == "venv")
    assert venv_cmd[2] == str(venv)
    pip = next(a for a in argvs if a[1:3] == ["pip", "install"])
    # The interpreter uv installs into is this engine's venv — never the app's.
    assert pip[3:5] == ["--python", str(si._venv_python(venv))]
    for argv in argvs:
        assert sys.executable not in argv
        assert not any(sys.prefix in part for part in argv)


def test_managed_roots_never_overlap():
    roots = {eid: si.managed_root(si.get_spec(eid)) for eid in _ALL_SPEC_IDS}
    for a, ra in roots.items():
        for b, rb in roots.items():
            if a != b:
                assert ra != rb and ra not in rb.parents and rb not in ra.parents, (a, b)


def test_uninstalling_one_engine_leaves_every_other_engine_intact(monkeypatch):
    for eid in _ALL_SPEC_IDS:
        py = si._venv_python(si.managed_checkout(si.get_spec(eid)) / ".venv")
        py.parent.mkdir(parents=True)
        py.write_text("#!fake\n")
    monkeypatch.setattr("core.prefs.get", lambda k, d=None: None)
    monkeypatch.setattr("core.prefs.delete", lambda k: None)

    assert si.uninstall("voxcpm2")["status"] == "uninstalled"

    assert not si.managed_root(si.get_spec("voxcpm2")).exists()
    for eid in _ALL_SPEC_IDS:
        if eid != "voxcpm2":
            spec = si.get_spec(eid)
            assert si._venv_python(si.managed_checkout(spec) / ".venv").is_file(), eid


# ── per-engine install recipes ────────────────────────────────────────────


@pytest.mark.parametrize("family", ["cuda", "cpu", "rocm", "mps"])
@pytest.mark.parametrize("engine_id", _ALL_SPEC_IDS)
def test_cuda_index_is_added_only_for_cuda_pinned_specs_on_cuda_hosts(
    monkeypatch, engine_id, family
):
    from core.torch_indexes import UV_PIP_CU128_ARGS
    spec = si.get_spec(engine_id)
    argvs = _capture_install_argvs(monkeypatch, family=family)
    si._step_install_deps(spec, si._new_job(engine_id))
    pip = next(a for a in argvs if a[1:3] == ["pip", "install"])
    has_index = _PYTORCH_INDEX in pip
    assert has_index == (family == "cuda" and (spec.uses_cuda_index or bool(spec.torch_pins)))
    if has_index:
        i = pip.index("--extra-index-url")
        assert tuple(pip[i:i + len(UV_PIP_CU128_ARGS)]) == UV_PIP_CU128_ARGS


def test_torch_index_matches_the_apps_own_pytorch_cuda_index():
    """The sidecar index must be the one the app's own torch comes from."""
    import tomllib
    from core.torch_indexes import PYTORCH_CU128_INDEX_URL
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    indexes = tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["uv"]["index"]
    cuda = next(ix for ix in indexes if ix["name"] == "pytorch-cuda")
    assert PYTORCH_CU128_INDEX_URL == cuda["url"] == _PYTORCH_INDEX


@pytest.mark.parametrize("engine_id", _ALL_SPEC_IDS)
def test_verify_probe_runs_in_the_engines_venv_and_compiles(monkeypatch, engine_id):
    spec = si.get_spec(engine_id)
    # The venv, and so the checkout, exist by the time verify runs.
    si.managed_checkout(spec).mkdir(parents=True)
    ran = []

    def fake_run(argv, **kwargs):
        ran.append(argv)
        return SimpleNamespace(returncode=0, stderr=b"", stdout=b"")

    monkeypatch.setattr(si.subprocess, "run", fake_run)
    si._step_verify(spec, si._new_job(engine_id))
    checkout = si.managed_checkout(spec)
    assert ran[0][0] == str(si._venv_python(checkout / ".venv"))
    code = ran[0][2]
    compile(code, "<probe>", "exec")  # a Windows path must not break the literal
    assert "{checkout" not in code
    if engine_id == "confucius4-tts":
        assert repr(str(checkout)) in code


# ── host gates: no Install button that can only fail ─────────────────────


_EVERY_ENGINE = {"voxcpm2", "chatterbox", "indic-parler", "indic-tts"}


@pytest.mark.parametrize(
    ("family", "platform", "machine", "expected"),
    [
        ("cuda", "linux", "x86_64", _EVERY_ENGINE),
        ("cuda", "win32", "AMD64", _EVERY_ENGINE),
        ("cpu", "win32", "AMD64", _EVERY_ENGINE),
        ("mps", "darwin", "arm64", _EVERY_ENGINE),
        # Intel Mac: PyTorch publishes no build these engines can use.
        ("cpu", "darwin", "x86_64", set()),
    ],
)
def test_installable_engine_ids_follow_the_host(monkeypatch, family, platform, machine, expected):
    import platform as platform_mod
    monkeypatch.setattr(si, "_host_family", lambda: family)
    monkeypatch.setattr(si.sys, "platform", platform)
    monkeypatch.setattr(platform_mod, "machine", lambda: machine)
    assert si.installable_engine_ids() == frozenset(expected)


def test_a_host_probe_that_raises_counts_as_unsupported():
    def boom():
        raise RuntimeError("probe exploded")

    spec = _mk_spec(host_supported=boom)
    ok, why = si.host_support(spec)
    assert not ok
    assert spec.docs_path in why and "exploded" not in why


def _intel_mac(monkeypatch):
    import platform as platform_mod
    monkeypatch.setattr(si.sys, "platform", "darwin")
    monkeypatch.setattr(platform_mod, "machine", lambda: "x86_64")


def test_start_install_refuses_an_unsupported_host(monkeypatch):
    _intel_mac(monkeypatch)
    with pytest.raises(si.HostUnsupported, match="Intel"):
        si.start_install("voxcpm2")
    assert "voxcpm2" not in si._jobs


def test_router_maps_unsupported_host_to_409(monkeypatch):
    from fastapi import HTTPException
    from api.routers import engines as engines_router
    _intel_mac(monkeypatch)
    with pytest.raises(HTTPException) as ei:
        engines_router.install_sidecar_engine("chatterbox")
    assert ei.value.status_code == 409
    assert "Intel" in ei.value.detail


def test_list_backends_offers_install_only_where_it_can_work(monkeypatch):
    from services import tts_backend
    monkeypatch.setattr(si, "_host_family", lambda: "cpu")
    monkeypatch.setattr(si.sys, "platform", "win32")
    rows = {r["id"]: r for r in tts_backend.list_backends()}
    assert rows["chatterbox"]["one_click_install"] is True
    _intel_mac(monkeypatch)
    rows = {r["id"]: r for r in tts_backend.list_backends()}
    assert rows["chatterbox"]["one_click_install"] is False


def test_engine_venv_python_needs_a_real_interpreter(monkeypatch, tmp_path):
    monkeypatch.delenv("OMNIVOICE_FAKE_SIDE_DIR", raising=False)
    assert si.engine_venv_python("OMNIVOICE_FAKE_SIDE_DIR") is None
    monkeypatch.setenv("OMNIVOICE_FAKE_SIDE_DIR", str(tmp_path))
    assert si.engine_venv_python("OMNIVOICE_FAKE_SIDE_DIR") is None  # no venv yet
    py = si._venv_python(tmp_path / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("#!fake\n")
    # An interpreter without the marker is a failed or unfinished install.
    assert si.engine_venv_python("OMNIVOICE_FAKE_SIDE_DIR") is None
    (tmp_path / si._INSTALL_COMPLETE_MARKER).write_text("x\n", encoding="utf-8")
    assert si.engine_venv_python("OMNIVOICE_FAKE_SIDE_DIR") == py


# The root of each pinned upstream commit, as GitHub lists it (2026-09-10).
_UPSTREAM_ROOT_FILES = {
    "chatterbox": ("pyproject.toml", "README.md", "LICENSE", "src/chatterbox/tts.py"),
    "indic-parler": ("pyproject.toml", "setup.py", "README.md", "LICENSE",
                     "parler_tts/modeling_parler_tts.py"),
}


@pytest.mark.parametrize("engine_id", sorted(_UPSTREAM_ROOT_FILES))
def test_a_real_upstream_layout_passes_source_validation(monkeypatch, engine_id):
    """Source validation accepts the layout each pinned upstream commit has."""
    spec = si.get_spec(engine_id)

    def fake_git(job, argv, *, timeout, env=None):
        if argv[1] == "clone":
            checkout = Path(argv[-1])
            for rel in _UPSTREAM_ROOT_FILES[engine_id]:
                (checkout / rel).parent.mkdir(parents=True, exist_ok=True)
                (checkout / rel).write_text("x\n")
        return 0

    def no_tarball(*args, **kwargs):
        pytest.fail("a valid clone fell back to the source tarball")

    monkeypatch.setattr(si.shutil, "which", lambda n: "/usr/bin/git" if n == "git" else None)
    monkeypatch.setattr(si, "_run_logged", fake_git)
    monkeypatch.setattr(si, "_fetch_tarball", no_tarball)
    # Submodule trees come from their own tarballs; not what this test covers.
    monkeypatch.setattr(si, "_ensure_extra_sources", lambda spec, job, checkout: None)

    job = si._new_job(engine_id)
    si._step_fetch_source(spec, job)

    assert si._job_step(job, "fetch_source")["detail"] == "git clone"
    assert si._source_present(spec, si.managed_checkout(spec))


def test_a_failed_dependency_install_is_repaired_not_reported_installed(monkeypatch):
    """A venv whose dependency install died halfway still has its interpreter.
    Counting that as installed made a retry answer already_installed, and the
    engine then failed at its first import."""
    spec = _mk_spec(repo_url="", tarball_url="", has_source=False)
    monkeypatch.setitem(si.SPECS, "fake-side", spec)
    monkeypatch.setattr(si, "_locate_uv", lambda: "/fake/uv")
    monkeypatch.setattr(si, "disk_free_bytes", lambda p: 100 * _GIB)
    monkeypatch.setattr("core.prefs.set_", lambda k, v: None)
    _stub_verify_ok(monkeypatch)
    argvs = []
    ok_run = _fake_run_logged(argvs)

    def pip_fails(job, argv, *, timeout, env=None):
        rc = ok_run(job, argv, timeout=timeout, env=env)
        return 1 if argv[1:3] == ["pip", "install"] else rc

    # A complete install is healthy.
    monkeypatch.setattr(si, "_run_logged", ok_run)
    assert _run(spec)["state"] == "succeeded"
    assert si._healthy(spec)

    # A reinstall whose dependency step fails is not, though the venv remains.
    monkeypatch.setattr(si, "_run_logged", pip_fails)
    assert _run(spec)["state"] == "failed"
    assert si._venv_python(si.managed_checkout(spec) / ".venv").is_file()
    assert not si._healthy(spec)

    # And the next run repairs it.
    monkeypatch.setattr(si, "_run_logged", ok_run)
    assert _run(spec)["state"] == "succeeded"
    assert si._healthy(spec)


@pytest.mark.parametrize(
    ("family", "platform", "suffix", "index"),
    [
        ("cuda", "win32", "+cu128", "https://download.pytorch.org/whl/cu128"),
        ("cuda", "linux", "+cu128", "https://download.pytorch.org/whl/cu128"),
        ("cpu", "win32", "+cpu", "https://download.pytorch.org/whl/cpu"),
        ("rocm", "linux", "+cpu", "https://download.pytorch.org/whl/cpu"),
        ("mps", "darwin", "", None),
    ],
)
def test_torch_pins_follow_the_host(monkeypatch, family, platform, suffix, index):
    """voxcpm leaves torch unpinned, and resolving it with the CUDA index
    paired PyPI's newest torch (CPU-only on Windows) with a CUDA torchaudio.
    The spec pins the pair; the host decides which build of it."""
    spec = si.get_spec("voxcpm2")
    assert spec.torch_pins
    argvs = _capture_install_argvs(monkeypatch, family=family)
    monkeypatch.setattr(si.sys, "platform", platform)

    si._step_install_deps(spec, si._new_job("voxcpm2"))

    pip = next(a for a in argvs if a[1:3] == ["pip", "install"])
    for pin in spec.torch_pins:
        assert f"{pin}{suffix}" in pip
    if index:
        assert pip.count("--extra-index-url") == 1
        assert pip[pip.index("--extra-index-url") + 1] == index
    else:
        assert "--extra-index-url" not in pip


# ── ROCm hosts take a ROCm torch, not the upstream's CUDA build (#2371) ────


# ── A pre-#2371 managed install is offered the repair on a ROCm host ───────

_CUDA_TORCH_VERSION_PY = (
    "from typing import Optional\n"
    "__version__ = '2.8.0+cu128'\n"
    "cuda: Optional[str] = '12.8'\n"
    "hip: Optional[str] = None\n"
)
_ROCM_TORCH_VERSION_PY = (
    "from typing import Optional\n"
    "__version__ = '2.8.0+rocm6.4'\n"
    "cuda: Optional[str] = None\n"
    "hip: Optional[str] = '6.4.43482'\n"
)


# ── Submodule trees, partial weights, and optional post-install data ──────


def _submodule_tarball() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"x = 1\n"
        info = tarfile.TarInfo("Sub-abc123/sub/__init__.py")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_a_submodule_tree_is_fetched_at_its_pinned_revision(monkeypatch):
    """Neither a depth-1 clone nor GitHub's source tarball includes git
    submodules, and CosyVoice imports Matcha-TTS from one."""
    import httpx

    extra = si.ExtraSource(
        path="third_party/Sub", revision="abc123",
        tarball_url="https://example.test/sub.tar.gz", required_path="sub/__init__.py",
    )
    spec = _mk_spec(extra_sources=(extra,))
    urls = []

    def fake_stream(method, url, **kw):
        urls.append(url)
        return _FakeStream(_submodule_tarball() if url == extra.tarball_url
                           else _tarball_bytes("fake-side-main"))

    monkeypatch.setattr(si.shutil, "which", lambda n: None)
    monkeypatch.setattr(httpx, "stream", fake_stream)

    si._step_fetch_source(spec, si._new_job(spec.engine_id))
    sub = si.managed_checkout(spec) / "third_party" / "Sub"
    assert (sub / "sub" / "__init__.py").is_file()
    assert urls == [spec.tarball_url, extra.tarball_url]

    # Present at the pinned revision: nothing is fetched again.
    urls.clear()
    si._step_fetch_source(spec, si._new_job(spec.engine_id))
    assert urls == []

    # At another revision: only the submodule is fetched again.
    (sub / ".voiceym_source_revision").write_text("old\n", encoding="utf-8")
    si._step_fetch_source(spec, si._new_job(spec.engine_id))
    assert urls == [extra.tarball_url]


def test_only_the_listed_weight_files_are_downloaded(monkeypatch):
    import huggingface_hub

    spec = _mk_spec(
        weights_repo_id="org/model", weights_revision="rev1", weights_subdir="w",
        weights_config_names=("m.yaml",), weights_allow_patterns=("m.yaml", "llm.pt"),
    )
    seen = {}

    def fake_snapshot(**kw):
        seen.update(kw)
        w = Path(kw["local_dir"])
        w.mkdir(parents=True, exist_ok=True)
        (w / "m.yaml").write_text("x")
        (w / "llm.pt").write_bytes(b"\0" * (6 * 1024 * 1024))

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot)
    monkeypatch.setattr("services.endpoint_race.effective_endpoint", lambda: None)
    monkeypatch.setattr("services.token_resolver.resolve", lambda: None)

    si._step_fetch_weights(spec, si._new_job(spec.engine_id))

    assert seen["allow_patterns"] == ["m.yaml", "llm.pt"]
    assert si._weights_present(spec)


def test_weights_from_an_earlier_run_do_not_prove_the_dependencies_finished():
    spec = _mk_spec(weights_repo_id="org/model", weights_revision="r", weights_subdir="w",
                    weights_config_names=("m.yaml",))
    checkout = si.managed_checkout(spec)
    py = si._venv_python(checkout / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("#!fake\n")
    (checkout / "pyproject.toml").write_text("[project]\n")
    w = checkout / "w"
    w.mkdir()
    (w / "m.yaml").write_text("x")
    (w / "llm.pt").write_bytes(b"\0" * (6 * 1024 * 1024))
    (w / si._WEIGHTS_COMPLETE_MARKER).write_text("org/model\nr\n0\n", encoding="utf-8")
    assert si._weights_present(spec)
    assert not si._healthy(spec)
    (checkout / si._INSTALL_COMPLETE_MARKER).write_text("x\n", encoding="utf-8")
    assert si._healthy(spec)


def _tarball_with_absolute_symlink() -> bytes:
    """Shaped like the pinned Matcha-TTS tarball: a package plus a `data` link
    to a folder on its author's machine."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"x = 1\n"
        info = tarfile.TarInfo("Matcha-abc/matcha/__init__.py")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("Matcha-abc/data")
        link.type = tarfile.SYMTYPE
        link.linkname = "/home/someone/Projects/Grad-TTS/data"
        tf.addfile(link)
    return buf.getvalue()


def test_a_tarball_with_an_absolute_link_still_extracts(monkeypatch, tmp_path):
    """The stdlib "data" filter raises AbsoluteLinkError on such a link, which
    aborted the Matcha-TTS fetch and with it the CosyVoice install."""
    import httpx

    monkeypatch.setattr(httpx, "stream", lambda method, url, **kw: _FakeStream(_tarball_with_absolute_symlink()))
    dest = tmp_path / "third_party" / "Matcha-TTS"
    dest.parent.mkdir(parents=True)
    job = si._new_job("fake-side")

    si._download_and_extract(job, "https://example.test/m.tar.gz", dest, tmp_path, "OMNIVOICE_FAKE_SIDE_DIR")

    assert (dest / "matcha" / "__init__.py").is_file()
    assert not (dest / "data").exists() and not (dest / "data").is_symlink()


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_a_failed_dependency_install_names_the_windows_path_limit(monkeypatch, platform):
    """openai-whisper (a CosyVoice dependency) builds from source, and under a
    long cache path its build fails on Windows' 260-character limit with a
    bare "No such file or directory"."""
    spec = _mk_spec()
    monkeypatch.setattr(si, "_locate_uv", lambda: "/fake/uv")
    monkeypatch.setattr(si, "_run_logged", lambda job, argv, *, timeout, env=None: 1)
    monkeypatch.setattr(si.sys, "platform", platform)
    with pytest.raises(si._StepError) as err:
        si._step_install_deps(spec, si._new_job(spec.engine_id))
    assert ("LongPathsEnabled" in err.value.remediation) == (platform == "win32")


def test_all_sidecar_recipes_pin_python():
    for spec in si.SPECS.values():
        assert '--python' in spec.venv_args, spec.engine_id


def test_incompatible_linked_venv_is_not_deleted(monkeypatch, tmp_path):
    spec = _mk_spec(venv_args=('--python', '3.11'), compatible_python=('3.11',))
    checkout = si.managed_checkout(spec)
    checkout.mkdir(parents=True)
    external = tmp_path / 'external'
    py = si._venv_python(external)
    py.parent.mkdir(parents=True)
    py.write_text('user interpreter')
    try:
        (checkout / '.venv').symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip('host does not permit directory symlinks')
    monkeypatch.setattr(si.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        returncode=0, stdout='3.14\n', stderr=''))
    with pytest.raises(si._StepError, match='linked outside'):
        si._step_create_venv(spec, si._new_job(spec.engine_id))
    assert py.read_text() == 'user interpreter'
    assert (checkout / '.venv').is_symlink()


@pytest.mark.parametrize('failure', ['timeout', 'invalid', 'exit'])
def test_unproven_venv_is_not_destroyed(monkeypatch, failure):
    spec = _mk_spec(venv_args=('--python', '3.11'), compatible_python=('3.11',))
    py = si._venv_python(si.managed_checkout(spec) / '.venv')
    py.parent.mkdir(parents=True)
    py.write_text('user interpreter')

    def probe(*args, **kwargs):
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(args[0], 15)
        return SimpleNamespace(returncode=1 if failure == 'exit' else 0,
                               stdout='invalid', stderr='')

    monkeypatch.setattr(si.subprocess, 'run', probe)
    with pytest.raises(si._StepError, match='Could not check') as error:
        si._step_create_venv(spec, si._new_job(spec.engine_id))
    assert str(py) not in str(error.value)
    assert py.read_text() == 'user interpreter'


@pytest.mark.parametrize("version", ["3.10", "3.12"])
def test_unspecified_compatibility_does_not_rebuild_working_venv(monkeypatch, version):
    spec = _mk_spec(venv_args=("--python", "3.11"))
    monkeypatch.setattr(si.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=version, stderr=""))
    assert si._existing_venv_compatible(spec, Path("existing-python"))


@pytest.mark.parametrize('engine', ['indic-tts'])
def test_runtime_rejects_old_managed_recipe_but_preserves_external_installs(monkeypatch, tmp_path, engine):
    """Recipe upgrades invalidate managed markers while preserving external venvs."""
    spec = si.SPECS[engine]
    checkout = si.managed_checkout(spec)
    py = si._venv_python(checkout / '.venv')
    py.parent.mkdir(parents=True)
    py.write_text('#!fake\n')
    marker = checkout / si._INSTALL_COMPLETE_MARKER
    marker.write_text(spec.probe_module + '\n')
    monkeypatch.setenv(spec.env_var, str(checkout))
    assert si.engine_venv_python(spec.env_var) is None
    assert py.exists() and marker.exists()
    marker.write_text(f'{spec.probe_module}\n{spec.install_revision}\n')
    assert si.engine_venv_python(spec.env_var) == py
    external = tmp_path / 'external'
    external_py = si._venv_python(external / '.venv')
    external_py.parent.mkdir(parents=True)
    external_py.write_text('#!fake\n')
    (external / si._INSTALL_COMPLETE_MARKER).write_text('external-version\n')
    monkeypatch.setenv(spec.env_var, str(external))
    assert si.engine_venv_python(spec.env_var) == external_py


def test_corrupt_non_utf8_markers_read_as_absent_not_crash(monkeypatch):
    """#2634: a marker with undecodable bytes must mean 'not installed', not raise."""
    spec = _mk_spec(source_revision="rev", weights_repo_id="Example/Weights")
    checkout = si.managed_checkout(spec)
    checkout.mkdir(parents=True, exist_ok=True)
    (checkout / "pyproject.toml").write_text("[project]\nname='fake'\n")
    (checkout / si._SOURCE_REVISION_MARKER).write_bytes(b"\xff\xfe\x80rev")
    assert si._source_present(spec, checkout) is False

    wdir = checkout / spec.weights_subdir
    wdir.mkdir(parents=True, exist_ok=True)
    (wdir / si._WEIGHTS_COMPLETE_MARKER).write_bytes(b"\xff\xfe\x80")
    assert si._weights_present(spec) is False

    extra_dir = checkout / "extra"
    extra_dir.mkdir()
    (extra_dir / "needed.txt").write_text("x")
    (extra_dir / si._SOURCE_REVISION_MARKER).write_bytes(b"\xff\xfe\x80")
    extra = si.ExtraSource("extra", "rev", "https://example.invalid/x.tar.gz", "needed.txt")
    assert si._extra_source_present(extra, extra_dir) is False


def test_engines_dir_env_relocates_managed_roots(monkeypatch, tmp_path):
    """#2683: OMNIVOICE_ENGINES_DIR moves engine venvs off the data drive."""
    spec = next(iter(si.SPECS.values()))
    default_root = si.managed_root(spec)
    assert default_root == Path(si.DATA_DIR) / "engines" / spec.engine_id
    elsewhere = tmp_path / "bigdrive"
    monkeypatch.setenv("OMNIVOICE_ENGINES_DIR", str(elsewhere))
    assert si.managed_root(spec) == elsewhere / spec.engine_id


def test_indic_tts_pins_a_torch_coqui_can_import():
    """coqui-tts raises at import on torch 2.9+ without torchcodec, so an
    unpinned resolve (torch 2.14) failed every Indic-TTS install at verify."""
    spec = si.SPECS["indic-tts"]
    pins = dict(p.split("==") for p in spec.torch_pins)
    assert set(pins) == {"torch", "torchaudio"}
    major, minor = (int(x) for x in pins["torch"].split(".")[:2])
    assert (major, minor) < (2, 9)
    assert spec.install_revision  # existing failed installs re-run with the pin
