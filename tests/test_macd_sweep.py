import macd_sweep as M


def test_import_opens_no_log_and_creates_no_dir():
    assert M.LOGF is None


def test_log_opens_file_lazily_in_current_outdir(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "OUTDIR", str(tmp_path / "out"))
    monkeypatch.setattr(M, "LOGF", None)
    M.log("hello")
    assert (tmp_path / "out" / "sweep.log").read_text() == "hello\n"
