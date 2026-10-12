"""Activated Hermes uses PM's interpreter even beside an older checkout venv."""

from __future__ import annotations

import json
from pathlib import Path
import shlex
import shutil
import subprocess

import pytest

from tests.pm.activation_support import (
    bash, bash_env, fake_store, isolated_checkout, posix, powershell, spawnable_python,
)

pytestmark = pytest.mark.real_machine_home


def _checkout_probe(tmp_path):
    root = isolated_checkout(tmp_path)
    subprocess.run(
        ["git", "init", str(root)], check=True, capture_output=True, timeout=30,
    )
    (root / "hermes").write_text(
        "import json, os, sys\n"
        "print(json.dumps({'interpreter': os.environ.get('PM_INTERPRETER_CANARY'), "
        "'executable': sys.executable, 'argv': sys.argv[1:], 'cwd': os.getcwd()}))\n"
        "sys.exit(17)\n",
        encoding="utf-8",
    )
    subdir = root / "working directory"
    subdir.mkdir()
    return root, subdir


def _interpreter_canary(path: Path, canary: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = hermes ]; then export PM_INTERPRETER_CANARY={canary}; fi\n'
        f'exec {shlex.quote(str(spawnable_python()))} "$@"\n',
        encoding="utf-8",
    )
    path.chmod(0o755)


def _assert_probe(run, root, subdir):
    assert run.returncode == 0, run.stdout + run.stderr
    probe, status, cwd = run.stdout.splitlines()
    payload = json.loads(probe)
    assert payload["argv"] == ["argument with spaces"]
    assert Path(payload["cwd"]).samefile(root)
    assert status == "17"
    assert Path(cwd).samefile(subdir)
    return payload


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
@pytest.mark.parametrize("legacy_dir", [None, ".venv", "venv"])
@pytest.mark.parametrize("explicit_override", [False, True])
def test_activated_hermes_selects_pm_python_unless_explicitly_overridden(
    tmp_path, shell, legacy_dir, explicit_override,
):
    if shell == "fish" and shutil.which("fish") is None:
        pytest.skip("fish is not installed")
    if shell == "zsh" and shutil.which("zsh") is None:
        pytest.skip("zsh is not installed")
    root, subdir = _checkout_probe(tmp_path)
    store, entry = fake_store(tmp_path)
    _interpreter_canary(entry / "bin" / "python3", "pm")
    if legacy_dir is not None:
        _interpreter_canary(root / legacy_dir / "bin" / "python", "legacy")
    override = tmp_path / "explicit interpreter"
    _interpreter_canary(override, "override")
    env = bash_env(store)
    env.pop("PYTHON", None)
    if shell != "fish":
        script = (
            f'source {shlex.quote(str(root / "activate"))} || exit $?\n'
            + (f'export PYTHON={shlex.quote(str(override))}\n' if explicit_override else "")
            + f'cd {shlex.quote(str(subdir))} || exit $?\n'
            + 'hermes "argument with spaces"\n'
            + 'probe_status=$?\nprintf "%s\\n" "$probe_status" "$PWD"\n'
            + 'deactivate || exit $?\n'
            + 'test -z "${__HERMES_RUNTIME_PYTHON+set}"\n'
        )
        command = [bash(), "-c", script] if shell == "bash" else ["zsh", "-f", "-c", script]
    else:
        script = (
            f'source "{posix(root / "activate.fish")}"; or exit $status\n'
            + (f'set -gx PYTHON "{posix(override)}"\n' if explicit_override else "")
            + f'cd "{posix(subdir)}"; or exit $status\n'
            + 'hermes "argument with spaces"\n'
            + 'set -l probe_status $status\nprintf "%s\\n" $probe_status $PWD\n'
            + 'deactivate; or exit $status\n'
            + 'not set -q __HERMES_RUNTIME_PYTHON\n'
        )
        command = ["fish", "--no-config", "-c", script]
    run = subprocess.run(
        command, cwd=subdir, env=env, capture_output=True, text=True, timeout=60,
        check=False,
    )
    payload = _assert_probe(run, root, subdir)
    assert payload["interpreter"] == ("override" if explicit_override else "pm")


@pytest.mark.platforms("windows")
@pytest.mark.parametrize("explicit_override", [False, True])
def test_powershell_hermes_uses_the_recorded_native_interpreter(tmp_path, explicit_override):
    root, subdir = _checkout_probe(tmp_path)
    store, _ = fake_store(tmp_path)
    native_python = spawnable_python()
    facts_path = store / "facts.json"
    facts = json.loads(facts_path.read_text(encoding="utf-8"))
    facts["packages"]["python"]["entry"] = str(native_python.parent)
    facts_path.write_text(json.dumps(facts), encoding="utf-8")
    env = bash_env(store)
    env.pop("PYTHON", None)
    subprocess.run(
        [str(native_python), "-m", "venv", "--without-pip", str(root / ".venv")],
        check=True, capture_output=True, env=env, timeout=60,
    )
    legacy = root / ".venv" / "Scripts" / "python.exe"
    ps = powershell()
    assert ps, "native Windows test requires PowerShell"
    script = (
        f". '{root / 'activate.ps1'}'\n"
        + (f"$env:PYTHON = '{legacy}'\n" if explicit_override else "")
        + f"Set-Location -LiteralPath '{subdir}'\n"
        + "hermes 'argument with spaces'\n"
        + "Write-Output $LASTEXITCODE\nWrite-Output (Get-Location).Path\n"
    )
    run = subprocess.run(
        [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        cwd=subdir, env=env, capture_output=True, text=True, timeout=60, check=False,
    )
    payload = _assert_probe(run, root, subdir)
    assert Path(payload["executable"]).samefile(legacy if explicit_override else native_python)
