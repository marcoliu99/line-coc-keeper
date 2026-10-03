"""Explicit model setup downloads only on request and installs complete local files."""

import io
import tarfile


def test_explicit_layout_setup_installs_one_model_and_is_idempotent(
    monkeypatch, tmp_path
):
    from scripts import setup_paddle_layout

    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as bundle:
        for name in ("inference.json", "inference.pdiparams", "inference.yml"):
            content = b"official archive boundary fixture"
            info = tarfile.TarInfo(f"PP-DocLayoutV3/{name}")
            info.size = len(content)
            bundle.addfile(info, io.BytesIO(content))
    urls = []

    def download(url, timeout):
        urls.append(url)
        return io.BytesIO(archive.getvalue())

    monkeypatch.setattr(setup_paddle_layout.urllib.request, "urlopen", download)
    destination = setup_paddle_layout.prepare_model(tmp_path)
    assert destination == tmp_path / "PP-DocLayoutV3"
    assert all(
        (destination / name).read_bytes() == b"official archive boundary fixture"
        for name in ("inference.json", "inference.pdiparams", "inference.yml")
    )
    assert setup_paddle_layout.prepare_model(tmp_path) == destination
    assert len(urls) == 1 and "PP-DocLayoutV3_infer.tar" in urls[0]


def test_incomplete_setup_archive_preserves_existing_cache(monkeypatch, tmp_path):
    import pytest

    from scripts import setup_paddle_layout

    destination = tmp_path / "PP-DocLayoutV3"
    destination.mkdir()
    original = destination / "inference.yml"
    original.write_bytes(b"existing incomplete cache")
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as bundle:
        content = b"new manifest without weights"
        info = tarfile.TarInfo("PP-DocLayoutV3/inference.yml")
        info.size = len(content)
        bundle.addfile(info, io.BytesIO(content))
    monkeypatch.setattr(setup_paddle_layout.urllib.request, "urlopen",
                        lambda *_a, **_k: io.BytesIO(archive.getvalue()))
    with pytest.raises(ValueError, match="Incomplete"):
        setup_paddle_layout.prepare_model(tmp_path)
    assert original.read_bytes() == b"existing incomplete cache"
    assert not (destination / "inference.pdiparams").exists()
