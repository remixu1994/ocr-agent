"""Probe a live OCR Agent using one image generated in memory."""

import io
import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont


def smoke(base_url: str, token: str) -> None:
    deadline = time.monotonic() + 780
    while True:
        try:
            with urlopen(base_url + "/healthz", timeout=2) as response:
                assert json.load(response)["ready"] is True
            break
        except (URLError, TimeoutError, ConnectionError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(3)
    auth = {"Authorization": f"Bearer {token}"}
    with urlopen(Request(base_url + "/v1/health", headers=auth), timeout=10) as response:
        assert json.load(response) == {"ready": True, "schema_version": 1}

    with Image.new("RGB", (1000, 440), "white") as image:
        draw = ImageDraw.Draw(image)
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 72)
        except OSError:
            font = ImageFont.load_default(size=72)
        draw.text((85, 140), "OCR TEST 42", fill="black", font=font)
        with io.BytesIO() as output:
            image.save(output, format="PNG")
            png = output.getvalue()
    path = base_url + "/v1/documents/recognize"
    try:
        urlopen(Request(path, data=png, headers={"Content-Type": "image/png"}), timeout=10).close()
    except HTTPError as exc:
        with exc:
            assert exc.code == 401
    else:
        raise AssertionError("unauthenticated extraction was accepted")
    auth["Content-Type"] = "image/png"
    with urlopen(Request(path, data=png, headers=auth), timeout=120) as response:
        data = json.load(response)
        assert response.headers["Cache-Control"] == "no-store"
    text = "".join(item["text"] for item in data["text_blocks"]).upper().replace(" ", "")
    assert data["engine"] == "PP-StructureV3"
    assert "OCRTEST42" in text, "synthetic text did not match"
    print("OCR Agent synthetic HTTP smoke passed")


if __name__ == "__main__":
    smoke(os.getenv("OCR_SMOKE_URL", "http://127.0.0.1:8080"), os.environ["OCR_API_TOKEN"])
