"""release/honeycomb-connect.{sh,ps1} 的测试：sh 在临时 HOME 下真跑；ps1 有 pwsh 就跑同样的检查，没有只做静态断言。

跑法：python -m pytest -q tools/test_connect.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REL = Path(__file__).resolve().parent.parent / "release"
SH = REL / "honeycomb-connect.sh"
PS1 = REL / "honeycomb-connect.ps1"
START = "# >>> honeycomb terminal title >>>"
END = "# <<< honeycomb terminal title <<<"
TOKEN = "dev-token-SECRET"

BASHRC = "# my bashrc\nalias ll='ls -l'\nPROMPT_COMMAND='echo orig'\nexport FOO=1\n"
DET_CFG = {"enabled": True, "cockpitUrl": "http://x", "pet": {"enabled": True, "url": "http://127.0.0.1:47340/pet-event"}, "zeta": 1, "alpha": [1, 2]}


def _tools_dir(tmp: Path, names: list[str]) -> Path:
    d = tmp / "bin"
    d.mkdir()
    for n in names:
        p = shutil.which(n)
        assert p, n
        (d / n).symlink_to(p)
    return d


BASE_TOOLS = ["sh", "bash", "awk", "grep", "tr", "tail", "cat", "mktemp", "rm", "mkdir", "basename", "uname", "stty", "dirname"]


@pytest.fixture(params=["node", "python3"])
def env(request, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    det = tmp_path / "det"
    det.mkdir()
    pet = tmp_path / "pet"
    pet.mkdir()
    (home / ".bashrc").write_text(BASHRC)
    (det / "ai-detector.json").write_text(json.dumps(DET_CFG, indent=2) + "\n")
    (det / "ai-detector.json").chmod(0o644)
    (pet / "pet-token").write_text("pettok123\n")
    (pet / "config.json").write_text(json.dumps({"pet": "claude", "size": 280}, indent=2) + "\n")
    path = str(_tools_dir(tmp_path, BASE_TOOLS + [request.param]))
    e = {"HOME": str(home), "PATH": path, "SHELL": "/bin/bash", "AI_DETECTOR_HOME": str(det),
         "HONEYCOMB_PET_HOME": str(pet), "HONEYCOMB_DEVICE_TOKEN": TOKEN, "HONEYCOMB_SERVER": "http://hc.example:8800"}
    return e, home, det, pet


def run(e, *args):
    return subprocess.run(["sh", str(SH), *args], env=e, capture_output=True, text=True, stdin=subprocess.DEVNULL)


def test_connect_and_uninstall(env):
    e, home, det, pet = env
    r = run(e)
    assert r.returncode == 0, r.stderr
    assert TOKEN not in r.stdout + r.stderr
    rc = (home / ".bashrc").read_text()
    assert rc.startswith(BASHRC)
    assert rc.count(START) == 1 and rc.count(END) == 1
    assert not (home / ".zshrc").exists()
    d = json.loads((det / "ai-detector.json").read_text())
    assert d["pet"]["token"] == "pettok123"
    assert {k: v for k, v in d.items() if k != "pet"} == {k: v for k, v in DET_CFG.items() if k != "pet"}
    assert {k: v for k, v in d["pet"].items() if k != "token"} == DET_CFG["pet"]
    assert list(d) == list(DET_CFG)  # 键序不变
    c = json.loads((pet / "config.json").read_text())
    assert c == {"pet": "claude", "size": 280, "server": "http://hc.example:8800", "deviceToken": TOKEN}

    for f in (det / "ai-detector.json", pet / "config.json"):
        assert f.stat().st_mode & 0o777 == 0o600, f

    before = {p: p.read_text() for p in (home / ".bashrc", det / "ai-detector.json", pet / "config.json")}
    assert run(e).returncode == 0
    assert {p: p.read_text() for p in before} == before  # 第二次：一字不差

    assert run(e, "--uninstall").returncode == 0
    assert (home / ".bashrc").read_text() == BASHRC
    assert json.loads((det / "ai-detector.json").read_text()) == DET_CFG
    assert json.loads((pet / "config.json").read_text()) == {"pet": "claude", "size": 280}


def test_bash_title_runs_and_keeps_prompt_command(env):
    e, home, *_ = env
    run(e)
    (home / "proj").mkdir()
    script = f'cd "$HOME/proj"; . "$HOME/.bashrc"; eval "$PROMPT_COMMAND"; echo "PC=$PROMPT_COMMAND"'
    r = subprocess.run(["bash", "--norc", "-c", script], env=e, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "\033]0;~/proj\007" in r.stdout
    assert "orig" in r.stdout
    assert "PC=echo orig;__hc_title" in r.stdout


def test_title_strips_control_chars(env):
    e, home, *_ = env
    run(e)
    (home / "a\x1bb").mkdir()
    script = 'cd "$HOME/a"$\'\\033\'"b"; . "$HOME/.bashrc"; eval "$PROMPT_COMMAND"'
    r = subprocess.run(["bash", "--norc", "-c", script], env=e, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.endswith("\033]0;~/ab\007") and r.stdout.count("\033") == 1


def test_new_pet_dir_private(env):
    e, _, _, pet = env
    shutil.rmtree(pet)
    e["HONEYCOMB_PET_HOME"] = str(pet / "sub")
    run(e)
    assert (pet / "sub").stat().st_mode & 0o777 == 0o700
    assert (pet / "sub" / "config.json").stat().st_mode & 0o777 == 0o600


def test_no_rc_creates_by_shell(env):
    e, home, *_ = env
    (home / ".bashrc").unlink()
    e["SHELL"] = "/usr/bin/zsh"
    run(e)
    z = (home / ".zshrc").read_text()
    assert z.startswith(START) and "precmd_functions" in z and not (home / ".bashrc").exists()


def test_marker_replaced_in_place_outside_untouched(env):
    e, home, *_ = env
    (home / ".bashrc").write_text(f"top\n{START}\nOLD\n{END}\nbottom\n")
    run(e)
    lines = (home / ".bashrc").read_text().splitlines()
    assert lines[0] == "top" and lines[-1] == "bottom" and "OLD" not in lines and lines.count(START) == 1


def test_unterminated_marker_refused(env):
    e, home, *_ = env
    (home / ".bashrc").write_text(f"top\n{START}\nOLD\n")
    r = run(e, "--uninstall")
    assert "跳过" in r.stdout and (home / ".bashrc").read_text() == f"top\n{START}\nOLD\n"


def test_no_pet_token_skips(env):
    e, _, det, pet = env
    (pet / "pet-token").unlink()
    r = run(e)
    assert "先启动一次桌宠" in r.stdout
    assert "token" not in json.loads((det / "ai-detector.json").read_text())["pet"]


def test_no_device_token_skips_pet_config(env):
    e, _, _, pet = env
    del e["HONEYCOMB_DEVICE_TOKEN"]
    r = run(e)
    assert "没有设备令牌" in r.stdout
    assert json.loads((pet / "config.json").read_text()) == {"pet": "claude", "size": 280}


def test_invalid_json_untouched(env):
    e, _, det, _ = env
    (det / "ai-detector.json").write_text("{not json")
    r = run(e)
    assert r.returncode == 0 and (det / "ai-detector.json").read_text() == "{not json"


def test_no_json_tool_degrades(tmp_path):
    home = tmp_path / "h"
    home.mkdir()
    (home / ".bashrc").write_text(BASHRC)
    det, pet = tmp_path / "d", tmp_path / "p"
    det.mkdir(); pet.mkdir()
    (det / "ai-detector.json").write_text("{}")
    e = {"HOME": str(home), "PATH": str(_tools_dir(tmp_path, BASE_TOOLS)), "SHELL": "/bin/bash", "AI_DETECTOR_HOME": str(det),
         "HONEYCOMB_PET_HOME": str(pet), "HONEYCOMB_DEVICE_TOKEN": TOKEN}
    r = run(e)
    assert r.returncode == 0 and r.stdout.count("没找到 node 或 python3") == 2
    assert START in (home / ".bashrc").read_text() and (det / "ai-detector.json").read_text() == "{}"
    assert not (pet / "config.json").exists()


def test_bad_arg():
    assert run(dict(os.environ), "--token=x").returncode == 2


# ---- ps1 ----

def test_ps1_static():
    raw = PS1.read_bytes()
    assert raw[:3] == b"\xef\xbb\xbf"
    text = raw.decode("utf-8-sig")
    for s in (START, END, "-Uninstall", "param([switch]$Uninstall)", "HONEYCOMB_DEVICE_TOKEN", "pet.token", "deviceToken", "-AsSecureString", "honeycomb-desktop-pet"):
        assert s in text or s.lstrip("-") in text, s
    assert "Write-Host $tok\n" not in text and "Write-Host $tok " not in text


@pytest.mark.skipif(not shutil.which("pwsh"), reason="本机没有 pwsh")
def test_ps1_roundtrip(tmp_path):
    prof = tmp_path / "profile.ps1"
    det, pet = tmp_path / "det", tmp_path / "pet"
    det.mkdir(); pet.mkdir()
    prof.write_text("# mine\nSet-Alias ll Get-ChildItem\n")
    (det / "ai-detector.json").write_text(json.dumps(DET_CFG, indent=2))
    (pet / "pet-token").write_text("pettok123\n")
    (pet / "config.json").write_text(json.dumps({"pet": "claude"}))
    e = {**os.environ, "HONEYCOMB_PROFILE": str(prof), "AI_DETECTOR_HOME": str(det), "HONEYCOMB_PET_HOME": str(pet),
         "HONEYCOMB_DEVICE_TOKEN": TOKEN, "HONEYCOMB_SERVER": "http://hc.example:8800"}

    def ps(*a):
        r = subprocess.run(["pwsh", "-NoProfile", "-File", str(PS1), *a], env=e, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        assert r.returncode == 0, r.stdout + r.stderr
        assert TOKEN not in r.stdout + r.stderr

    ps(); ps()
    t = prof.read_text()
    assert t.startswith("# mine") and t.count(START) == 1
    d = json.loads((det / "ai-detector.json").read_text())
    assert d["pet"]["token"] == "pettok123" and d["zeta"] == 1 and d["alpha"] == [1, 2] and list(d)[:5] == list(DET_CFG)
    assert json.loads((pet / "config.json").read_text()) == {"pet": "claude", "server": "http://hc.example:8800", "deviceToken": TOKEN}
    ps("-Uninstall")
    assert prof.read_text().replace("\r\n", "\n") == "# mine\nSet-Alias ll Get-ChildItem\n"
    assert json.loads((det / "ai-detector.json").read_text()) == DET_CFG
    assert json.loads((pet / "config.json").read_text()) == {"pet": "claude"}
