# gif_keyframes.py
import os
import numpy as np
from typing import List
from PIL import Image, ImageSequence


def preprocess_frame(frame: np.ndarray, downscale: int = 2) -> np.ndarray:
    arr = np.asarray(frame).astype(np.float32) / 255.0
    if arr.ndim == 3:
        arr = arr.mean(axis=2)
    if downscale > 1:
        arr = arr[::downscale, ::downscale]
    return arr


def mae(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.mean(np.abs(a - b)))


def extract_keyframes_from_gif(
    gif_image: Image.Image,
    min_gap: int = 3,
    threshold_k: float = 1.2,
) -> List[Image.Image]:
    """
    Extract representative keyframes from a GIF.

    Args:
        gif_image: GIF image loaded by PIL.
        min_gap: Minimum frame gap between selected keyframes.
        threshold_k: Threshold coefficient for frame-difference selection.

    Returns:
        List[PIL.Image]: Extracted keyframe images.
    """

    raw = []
    proc = []

    for frame in ImageSequence.Iterator(gif_image):
        frame = frame.convert("RGB")
        arr = np.array(frame)
        raw.append(arr)
        proc.append(preprocess_frame(arr))

    if not proc:
        return []

    key_idxs = [0]

    if len(proc) > 1:
        maes = [mae(proc[i], proc[i - 1]) for i in range(1, len(proc))]
        tau = np.mean(maes) + threshold_k * np.std(maes)
        last = 0

        for i in range(1, len(proc)):
            if i - last < min_gap:
                continue
            if mae(proc[i], proc[last]) > tau:
                key_idxs.append(i)
                last = i

    keyframes = [Image.fromarray(raw[idx]) for idx in key_idxs]

    return keyframes
