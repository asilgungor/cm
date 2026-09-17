"""ops/run_server.py saf yardimcilari: uretim benzeri Streamlit komutu, dunya turu komutu, cokus bekleme suresi."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ops import run_server as rs  # noqa: E402


def test_streamlit_command_runs_the_web_app_without_file_watcher():
    cmd = rs.streamlit_command(port=8600, python="python.exe")
    assert cmd[:5] == ["python.exe", "-m", "streamlit", "run", "web_app.py"]
    flags = dict(zip(cmd[5::2], cmd[6::2], strict=True))
    assert flags == {"--server.port": "8600", "--server.headless": "true", "--server.fileWatcherType": "none",
                     "--server.runOnSave": "false", "--browser.gatherUsageStats": "false"}


def test_tick_command_advances_all_shared_worlds():
    assert rs.tick_command(python="python.exe") == ["python.exe", "main.py", "world-tick", "--all"]


def test_console_python_is_used_for_children_of_pythonw(tmp_path):
    pythonw, python = tmp_path / "pythonw.exe", tmp_path / "python.exe"
    pythonw.write_text("")
    assert rs.python_console_exe(str(pythonw)) == str(pythonw)          # python.exe yoksa ayni kalir
    python.write_text("")
    assert rs.python_console_exe(str(pythonw)) == str(python)
    assert rs.python_console_exe(str(python)) == str(python)


def test_restart_delay_backs_off_only_in_a_crash_loop():
    crashes: list[float] = []
    for i in range(rs.CRASH_LIMIT - 1):
        crashes.append(100.0 + i)
        assert rs.restart_delay(crashes, 100.0 + i) == rs.RESTART_DELAY
    crashes.append(110.0)
    assert rs.restart_delay(crashes, 110.0) == rs.CRASH_LOOP_DELAY
    assert rs.restart_delay(crashes, 110.0 + rs.CRASH_WINDOW + 50) == rs.RESTART_DELAY and crashes == []
