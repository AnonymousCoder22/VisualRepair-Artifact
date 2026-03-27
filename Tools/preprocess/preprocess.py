import os
from typing import List
from PIL import Image
import warnings
import logging

from .extract_keyframes import extract_keyframes_from_gif
from .crop import uied_background_crop

os.environ["DISABLE_MODEL_SOURCE_CHECK"] = "True"
warnings.filterwarnings("ignore")
logging.getLogger("easyocr").setLevel(logging.ERROR)

def is_image_file(filename: str):
    ext = filename.lower()
    return ext.endswith((".png", ".jpg", ".jpeg", ".bmp", ".gif"))

def preprocess(
        input_dir: str,
        output_dir: str
):
    os.makedirs(output_dir, exist_ok=True)

    if not os.path.exists(input_dir):
        return

    files = os.listdir(input_dir)
    image_files = [f for f in files if is_image_file(f)]

    if not image_files:
        return

    for filename in image_files:
        path = os.path.join(input_dir, filename)

        # ==================================================
        # Handle GIF inputs
        # ==================================================
        if filename.lower().endswith(".gif"):
            try:
                gif_img = Image.open(path)

                # 1. Save the original GIF.
                gif_save_path = os.path.join(output_dir, filename)
                gif_img.save(gif_save_path)

                # 2. Extract keyframes for downstream processing.
                frames = extract_keyframes_from_gif(gif_img)
                base = os.path.splitext(filename)[0]

                for i, frame in enumerate(frames):
                    frame = frame.convert("RGB") 
                    save_name = f"{base}_frame_{i}.png"
                    save_path = os.path.join(output_dir, save_name)
                    frame.save(save_path)

            except Exception as e:
                print(f"[GIF ERROR] {filename}: {e}")

        # ==================================================
        # Handle non-GIF images with background cropping
        # ==================================================
        else:
            try:
                img = Image.open(path).convert("RGB")
                cropped = uied_background_crop(img)

                save_path = os.path.join(output_dir, filename)
                cropped.save(save_path)

            except Exception as e:
                print(f"[IMG ERROR] {filename}: {e}")
