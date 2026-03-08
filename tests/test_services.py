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
