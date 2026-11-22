from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 20_000_000
FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG"}


class InvalidImage(Exception):
    pass


def decode_image(body: bytes | bytearray, content_type: str, turns: int, crop: int):
    try:
        with Image.open(BytesIO(body), formats=list(FORMATS.values())) as source:
            width, height = source.size
            if (source.format != FORMATS[content_type] or
                    min(width, height) < 8 or max(width, height) > 8192 or
                    width * height > MAX_PIXELS or getattr(source, "is_animated", False)):
                raise InvalidImage()
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGB")
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as exc:
        raise InvalidImage() from exc
    for _ in range(turns):
        image = image.transpose(Image.Transpose.ROTATE_270)
    if crop:
        width, height = image.size
        dx, dy = width * crop // 100, height * crop // 100
        image = image.crop((dx, dy, width - dx, height - dy))
    return image


class PaddleEngine:
    def __init__(self):
        from paddleocr import PPStructureV3

        self.pipeline = PPStructureV3(
            device="cpu", engine="onnxruntime",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            use_region_detection=False,
            use_chart_recognition=False,
            use_seal_recognition=False,
            use_formula_recognition=False,
            text_detection_model_name="PP-OCRv6_tiny_det",
            text_recognition_model_name="PP-OCRv6_tiny_rec",
            wired_table_structure_recognition_model_name="SLANet_plus",
            wireless_table_structure_recognition_model_name="SLANet_plus",
        )

    def analyze(self, image) -> dict:
        import numpy as np

        # Paddle's ndarray interface expects BGR. No image filename or persisted copy.
        bgr = np.ascontiguousarray(np.asarray(image)[:, :, ::-1])
        output = iter(self.pipeline.predict(bgr))
        page = next(output, None)
        return {} if page is None else page.json.get("res") or {}
