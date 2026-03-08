from app.services import select_download_options


def test_select_download_options_prefers_progressive_and_muxed() -> None:
    info = {
        "formats": [
            {"format_id": "137", "height": 1080, "ext": "mp4", "vcodec": "avc1", "acodec": "none", "fps": 30},
            {"format_id": "248", "height": 1080, "ext": "webm", "vcodec": "vp9", "acodec": "none", "fps": 30},
            {"format_id": "22", "height": 720, "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "fps": 30},
            {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a", "abr": 128},
            {"format_id": "251", "ext": "webm", "vcodec": "none", "acodec": "opus", "abr": 160},
        ]
    }

    options = select_download_options(info)
    selectors = [item.selector for item in options]

    assert "22" in selectors
    assert "137+251" in selectors
    assert "248+251" in selectors
    assert "251" in selectors


def test_select_download_options_marks_streams_downloadable_when_ffmpeg_exists(monkeypatch) -> None:
    monkeypatch.setattr("app.services.shell_shutil.which", lambda command: "/usr/bin/ffmpeg" if command == "ffmpeg" else None)
    info = {
        "formats": [
            {"format_id": "hls-720", "height": 720, "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "fps": 30, "protocol": "m3u8_native"},
        ]
    }

    options = select_download_options(info)

    assert len(options) == 1
    assert options[0].downloadable is True
    assert options[0].delivery == "stream"
    assert options[0].delivery_label == "HLS m3u8"


def test_select_download_options_disables_mux_without_ffmpeg(monkeypatch) -> None:
    monkeypatch.setattr("app.services.shell_shutil.which", lambda _command: None)
    info = {
        "formats": [
            {"format_id": "137", "height": 1080, "ext": "mp4", "vcodec": "avc1", "acodec": "none", "fps": 30, "protocol": "https"},
            {"format_id": "251", "ext": "webm", "vcodec": "none", "acodec": "opus", "abr": 160, "protocol": "https"},
        ]
    }

    options = select_download_options(info)

    assert len(options) == 2
    muxed = next(item for item in options if "+" in item.selector)
    assert muxed.downloadable is False
    assert "ffmpeg" in (muxed.disabled_reason or "")
