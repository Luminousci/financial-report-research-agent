from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_cmd_launcher_is_present_without_exe_launcher() -> None:
    launcher = ROOT / "启动本地分析台.cmd"

    assert launcher.is_file()
    content = launcher.read_text(encoding="utf-8")
    assert "fin_agent.cli serve" in content
    assert "--open-browser" in content
    assert not (ROOT / "启动分析台.exe").exists()


def test_local_portal_uses_native_launcher_only() -> None:
    portal = (ROOT / "本地运行入口.html").read_text(encoding="utf-8")

    assert "启动本地分析台.cmd" in portal
    assert "Anaconda启动分析台.py" not in portal
    assert "启动本地演示.cmd" not in portal
    assert not (ROOT / "启动本地演示.cmd").exists()
