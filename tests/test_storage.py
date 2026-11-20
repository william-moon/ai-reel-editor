import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from reel_editor import storage  # noqa: E402


def test_local_roundtrip(tmp_path):
    s = storage.LocalStorage(str(tmp_path / "root"))
    s.setup()
    (tmp_path / "root" / "Raw Videos" / "clip.mp4").write_bytes(b"v" * 100)
    (tmp_path / "root" / "Raw Videos" / "clip.jpg").write_bytes(b"i")
    assert s.list_new() == ["clip.mp4"] and s.thumb_for("clip.mp4") == "clip.jpg"
    assert s.stable("clip.mp4", wait=0)
    local = s.fetch("clip.mp4", str(tmp_path / "job"))
    out = tmp_path / "job" / "clip - edited.mp4"
    out.write_bytes(b"e")
    assert s.deliver([str(out)])[0].endswith(os.path.join("Finished Videos", "clip - edited.mp4"))
    s.archive("clip.mp4")
    assert s.list_new() == [] and sorted(os.listdir(s.done)) == ["clip.jpg", "clip.mp4"]
    assert os.path.exists(local)


@pytest.mark.skipif(not shutil.which("rclone"), reason="rclone not installed")
def test_drive_adapter_against_rclone_local_remote(tmp_path, monkeypatch):
    """the Google Drive adapter's rclone commands, run for real against an rclone 'local' remote
    (same code path, no Google account needed)"""
    conf = tmp_path / "rclone.conf"
    monkeypatch.setenv("RCLONE_CONFIG", str(conf))
    base = tmp_path / "fake-drive"
    base.mkdir()
    subprocess.run(["rclone", "config", "create", "testdrive", "alias", f"remote={base}"], check=True,
                   capture_output=True)
    d = storage.DriveStorage("testdrive")
    assert d.configured()
    d.setup()                                    # remote exists -> only creates the folders
    assert (base / "Raw Videos").is_dir() and (base / "Finished Videos").is_dir()
    (base / "Raw Videos" / "talk.mov").write_bytes(b"v" * 50)
    (base / "Raw Videos" / "talk.png").write_bytes(b"i")
    assert d.list_new() == ["talk.mov"] and d.thumb_for("talk.mov") == "talk.png"
    local = d.fetch("talk.mov", str(tmp_path / "job"))
    assert open(local, "rb").read() == b"v" * 50
    out = tmp_path / "job" / "talk - edited.mp4"
    out.write_bytes(b"e")
    d.deliver([str(out)])
    assert (base / "Finished Videos" / "talk - edited.mp4").exists()
    d.archive("talk.mov")
    assert sorted(os.listdir(base / "Raw Videos" / "Done")) == ["talk.mov", "talk.png"]
