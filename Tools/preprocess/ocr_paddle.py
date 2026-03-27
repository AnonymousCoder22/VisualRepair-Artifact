import os
os.environ["DISABLE_MODEL_SOURCE_CHECK"] = "True"

import cv2
import numpy as np
import warnings
warnings.filterwarnings("ignore", category=UserWarning)

from PIL import Image
from paddleocr import PaddleOCR

OCR_ENGINE = PaddleOCR(lang="ch")


def run_ocr(image: Image.Image) -> str:

    if image is None:
        return ""

    img = np.array(image.convert("RGB"))
    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    bg_color = np.bincount(gray.flatten()).argmax()

    bg_mat = np.full_like(gray, bg_color, dtype=np.uint8)
    diff = cv2.absdiff(gray, bg_mat)
    foreground_mask = (diff > 20).astype(np.uint8) * 255

    uniform = gray.copy()
    uniform[foreground_mask == 255] = 0

    _, binary = cv2.threshold(
        uniform, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )

    h, w = binary.shape
    pad_h = int(h * 0.1)
    pad_w = int(w * 0.1)

    padded = cv2.copyMakeBorder(
        binary,
        pad_h, pad_h, pad_w, pad_w,
        cv2.BORDER_CONSTANT,
        value=255
    )

    padded_color = cv2.cvtColor(padded, cv2.COLOR_GRAY2BGR)

    result = OCR_ENGINE.predict(padded_color)

    if not result or "rec_texts" not in result[0]:
        return ""

    texts = result[0]["rec_texts"]

    return "\n".join(texts)
