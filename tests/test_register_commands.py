import register_commands


def test_command_tree_shape():
    cmds = register_commands.build_commands()
    assert len(cmds) == 1
    csgo = cmds[0]
    assert csgo["name"] == "csgo"
    subs = {o["name"]: o for o in csgo["options"]}
    assert set(subs) == {"start", "stop", "status", "map"}
    start_opts = {o["name"]: o for o in subs["start"]["options"]}
    assert set(start_opts) == {"map", "mode", "tickrate"}
    assert len(start_opts["map"]["choices"]) == 12
    assert {"name": "de_dust2", "value": "de_dust2"} in start_opts["map"]["choices"]
    assert [c["value"] for c in start_opts["tickrate"]["choices"]] == ["64", "128"]
    assert subs["map"]["options"][0]["required"] is True


def test_load_env(tmp_path):
    f = tmp_path / ".env"
    f.write_text("A=1\n\n# comment\nB=two words\n")
    assert register_commands.load_env(f) == {"A": "1", "B": "two words"}
