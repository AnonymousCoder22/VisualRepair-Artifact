import os
import sys
import importlib
import numpy as np
from PIL import Image
from collections import Counter

# ==================================================
# ensure UIED path
# ==================================================
preprocess_root = os.path.abspath(os.path.dirname(__file__))
if preprocess_root not in sys.path:
    sys.path.insert(0, preprocess_root)

# ==================================================
# UIED import
# ==================================================
uied_detect_text = importlib.import_module("UIED.detect_text")
uied_detect_compo = importlib.import_module("UIED.detect_compo")
uied_config = importlib.import_module("UIED.config")


sys.modules["detect_text"] = uied_detect_text
sys.modules["detect_compo"] = uied_detect_compo
sys.modules["config"] = uied_config

import UIED.detect_text.text_detection as text
import UIED.detect_compo.ip_region_proposal as ip


# ==================================================
# Run UIED detection
# ==================================================
def run_uied(image_np):

    key_params = {
        "min-grad": 3,
        "ffl-block": 5,
        "min-ele-area": 5,
        "merge-contained-ele": True,
        "merge-line-to-paragraph": True,
        "remove-bar": True,
    }

    text_result = text.text_detection(image_np)
    compo_result = ip.compo_detection(image_np, key_params, resize_by_height=None)

    return compo_result, text_result


# ==================================================
# Load bounding boxes from UIED results
# ==================================================
def load_uied_boxes(compo_result, text_result):

    boxes = []

    for item in compo_result["compos"]:
        boxes.append((item["column_min"], item["row_min"],
                      item["column_max"], item["row_max"]))

    for item in text_result["texts"]:
        boxes.append((item["column_min"], item["row_min"],
                      item["column_max"], item["row_max"]))

    return boxes


# ==================================================
# fallback text boxes
# ==================================================
def load_text_boxes(text_result):

    boxes = []

    for item in text_result["texts"]:
        boxes.append((item["column_min"], item["row_min"],
                      item["column_max"], item["row_max"]))

    return boxes


# ==================================================
# Build row and column masks
# ==================================================
def build_masks(img_w, img_h, boxes):

    row_mask = np.zeros(img_h, dtype=bool)
    col_mask = np.zeros(img_w, dtype=bool)

    for x1, y1, x2, y2 in boxes:
        row_mask[y1:y2] = True
        col_mask[x1:x2] = True

    return row_mask, col_mask


# ==================================================
# Estimate the background color
# ==================================================
def estimate_background_color(arr, num_samples=300):

    flat = arr.reshape(-1, 3)

    if flat.shape[0] > num_samples:
        idx = np.random.choice(flat.shape[0], num_samples, replace=False)
        samples = flat[idx]
    else:
        samples = flat

    samples = [tuple(p) for p in samples]
    counter = Counter(samples)

    return np.array(counter.most_common(1)[0][0])


# ==================================================
# Check whether rows or columns belong to the background
# ==================================================
def is_background_row(row, bg_color, tol):
    diff = np.abs(row.astype(np.int32) - bg_color)
    return np.all(diff <= tol)


def is_background_col(col, bg_color, tol):
    diff = np.abs(col.astype(np.int32) - bg_color)
    return np.all(diff <= tol)


# ==================================================
# Remove background-only rows and columns
# ==================================================
def remove_horizontal_bg(arr, row_mask, bg_color, tol):

    keep = []

    for y in range(arr.shape[0]):

        if row_mask[y]:
            keep.append(y)
            continue

        if not is_background_row(arr[y], bg_color, tol):
            keep.append(y)

    return arr[keep]


def remove_vertical_bg(arr, col_mask, bg_color, tol):

    keep = []

    for x in range(arr.shape[1]):

        if col_mask[x]:
            keep.append(x)
            continue

        if not is_background_col(arr[:, x], bg_color, tol):
            keep.append(x)

    return arr[:, keep]


# ==================================================
# fallback diff
# ==================================================
def remove_horizontal_diff(arr, row_mask, threshold):

    keep = [0]

    for y in range(1, arr.shape[0]):

        if row_mask[y]:
            keep.append(y)
            continue

        diff = np.abs(arr[y] - arr[y-1]).mean()

        if diff >= threshold:
            keep.append(y)

    return arr[keep]


def remove_vertical_diff(arr, col_mask, threshold):

    keep = [0]

    for x in range(1, arr.shape[1]):

        if col_mask[x]:
            keep.append(x)
            continue

        diff = np.abs(arr[:, x] - arr[:, x-1]).mean()

        if diff >= threshold:
            keep.append(x)

    return arr[:, keep]


# ==================================================
# Main entry point
# ==================================================
def uied_background_crop(image: Image.Image,
                         pixel_tolerance=5,
                         diff_threshold=0.5) -> Image.Image:
    """
    Args:
        image: Input PIL image.
        pixel_tolerance: Allowed deviation from the estimated background color.
        diff_threshold: Fallback difference threshold when background cropping is weak.

    Returns:
        PIL.Image: Cropped image.
    """

    arr = np.array(image.convert("RGB"))

    orig_h, orig_w = arr.shape[:2]

    compo_result, text_result = run_uied(arr)

    boxes = load_uied_boxes(compo_result, text_result)

    row_mask, col_mask = build_masks(orig_w, orig_h, boxes)

    bg_color = estimate_background_color(arr)

    arr_new = remove_horizontal_bg(arr, row_mask, bg_color, pixel_tolerance)
    arr_new = remove_vertical_bg(arr_new, col_mask, bg_color, pixel_tolerance)

    new_h, new_w = arr_new.shape[:2]

    compression_ratio = (orig_h * orig_w) / (new_h * new_w)

    if compression_ratio < 1.05:

        text_boxes = load_text_boxes(text_result)

        row_mask, col_mask = build_masks(orig_w, orig_h, text_boxes)

        arr_new = remove_horizontal_diff(arr, row_mask, diff_threshold)
        arr_new = remove_vertical_diff(arr_new, col_mask, diff_threshold)

    return Image.fromarray(arr_new.astype(np.uint8))
