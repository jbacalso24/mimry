import sys
import threading

import pytest

from mimry import installer


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows blocks deleting open files")
def test_reinstall_waits_out_a_file_held_open_inside_the_skill(tmp_path):
    installer._install_references(tmp_path)
    held = next((tmp_path / "references").iterdir())
    handle = held.open("rb")  # like a scanner reading what was just written
    threading.Timer(0.3, handle.close).start()

    installer._install_references(tmp_path)

    assert sorted(p.name for p in (tmp_path / "references").iterdir()) == sorted(
        installer._REFERENCES
    )
    assert not (tmp_path / "references.old").exists()
    assert not (tmp_path / "references.tmp").exists()
