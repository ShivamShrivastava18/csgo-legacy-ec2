import pathlib
import subprocess

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "bot" / "server" / "csgo-boot.sh"


def run_script(tmp_path, tag_values):
    cfg = tmp_path / "csgoserver.cfg"
    cfg.write_text('gslt="TOKEN"\ndefaultmap="de_dust2"\n')
    tag_script = tmp_path / "tags.sh"
    lines = ["#!/bin/bash", 'case "$1" in']
    for key, value in tag_values.items():
        lines.append(f'  {key}) echo "{value}" ;;')
    lines.append("  *) exit 1 ;;")
    lines.append("esac")
    tag_script.write_text("\n".join(lines) + "\n")
    tag_script.chmod(0o755)
    env = {
        "CSGO_CFG": str(cfg),
        "CSGO_TAG_CMD": str(tag_script),
        "CSGO_SKIP_START": "1",
        "PATH": "/usr/bin:/bin",
    }
    subprocess.run(["bash", str(SCRIPT)], env=env, check=True, capture_output=True)
    return cfg.read_text()


def test_applies_tags(tmp_path):
    out = run_script(tmp_path, {"map": "de_nuke", "mode": "deathmatch", "tickrate": "128"})
    assert 'defaultmap="de_nuke"' in out
    assert 'tickrate="128"' in out
    assert 'gametype="1"' in out
    assert 'gamemode="2"' in out
    assert out.count("defaultmap=") == 1
    assert 'gslt="TOKEN"' in out


def test_defaults_when_tags_missing(tmp_path):
    out = run_script(tmp_path, {})
    assert 'defaultmap="de_mirage"' in out
    assert 'tickrate="64"' in out
    assert 'gametype="0"' in out
    assert 'gamemode="1"' in out


def test_script_passes_bash_syntax_check():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
