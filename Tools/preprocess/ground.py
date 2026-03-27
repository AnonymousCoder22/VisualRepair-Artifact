import os
import re
import json
import base64
import io
import math
from typing import Optional, Tuple, List
from PIL import Image, ImageDraw
from openai import OpenAI

openai_api_key = os.getenv("QWEN_API_KEY")
openai_base_url = os.getenv("QWEN_BASE_URL")

pattern = r"<result>(.*?)</result>"

def clamp(v, lo, hi):
    return max(lo, min(v, hi))

def encode_image(image: Image.Image):
    image = image.convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    img_str = base64.b64encode(buffer.getvalue()).decode()
    return f"data:image/jpeg;base64,{img_str}"

def draw_bboxes(image: Image.Image, bboxes: List[List[int]], save_path="visualized.png"):
    vis = image.copy()
    draw = ImageDraw.Draw(vis)

    colors = ["red", "blue", "green"]

    for i, (x, y, w, h) in enumerate(bboxes):
        x2 = x + w
        y2 = y + h

        draw.rectangle([x, y, x2, y2], outline=colors[i % len(colors)], width=3)
        draw.text((x, y), str(i), fill=colors[i % len(colors)])

    vis.save(save_path)
    return vis

def parse_multi_bbox(resp: str) -> Optional[List[List[int]]]:
    match = re.search(pattern, resp, re.DOTALL)
    if not match:
        return None

    try:
        data = json.loads(match.group(1))

        if isinstance(data[0], int):
            data = [data]

        valid = []
        for box in data:
            if len(box) >= 4:
                valid.append(box[:4])

        return valid[:3] if len(valid) > 0 else None

    except:
        return None


def convert_coordinate_multi(bboxes, coef):
    x_ratio, y_ratio = coef
    return [
        [
            int(x * x_ratio),
            int(y * y_ratio),
            int(w * x_ratio),
            int(h * y_ratio)
        ]
        for x, y, w, h in bboxes
    ]


def generate_multi_scale_crops(
    image: Image.Image,
    bboxes: List[List[int]]
) -> Tuple[List[Image.Image], List[List[int]]]:

    width, height = image.size

    all_crops = []
    all_boxes = []

    scales = {
        "orig": 1.0,
        "zoom_in": math.sqrt(0.5),  
        "zoom_out": math.sqrt(2.0) 
    }

    for (x, y, w, h) in bboxes:

        cx = x + w / 2
        cy = y + h / 2

        for name, s in scales.items():

            new_w = w * s
            new_h = h * s

            if new_w < 5 or new_h < 5:
                continue

            new_x = cx - new_w / 2
            new_y = cy - new_h / 2

            x1 = int(clamp(new_x, 0, width - 1))
            y1 = int(clamp(new_y, 0, height - 1))
            x2 = int(clamp(new_x + new_w, 0, width))
            y2 = int(clamp(new_y + new_h, 0, height))

            if x2 <= x1 or y2 <= y1:
                continue

            if (
                x1 <= 1 and y1 <= 1 and
                x2 >= width - 1 and y2 >= height - 1
            ):
                continue

            crop = image.crop((x1, y1, x2, y2))

            all_crops.append(crop)
            all_boxes.append([x1, y1, x2 - x1, y2 - y1])

    return all_crops, all_boxes


def ground_multi_bbox(
    image: Image.Image,
    problem_statement: str,
    model="qwen3-vl-235b-a22b-instruct"
) -> Tuple[List[Image.Image], List[List[int]]]:
    
    width, height = image.size
    resolution = f"{width}x{height}"
    img_str = encode_image(image)

    prompt = f"""You are a master at analyzing images and code.

    # Task
    I will provide you with a bug report and an image related to the bug (image resolution={resolution}).

    Your job is to analyze the bug description and locate three regions in the image that are relevant to this bug.

    # Requirement
    - Return EXACTLY 3 bounding boxes
    - Each region must be DIFFERENT
    - Avoid overlap
    - Be precise

    #Bug Report:
    '''
    {problem_statement}
    '''

    # Output format

    <result>
    [[x, y, w, h], [x, y, w, h], [x, y, w, h]]
    </result>

    Where:
    x = top-left x
    y = top-left y
    w = width
    h = height
    Coordinates must be normalized to 0~1000.    

    """

    client = OpenAI(api_key=openai_api_key, base_url=openai_base_url)

    resp = client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": img_str}}
            ]
        }],
        temperature=0
    )
    usage = resp.usage
    token_usage = {
        "base_model": model,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens
    }
    bboxes = parse_multi_bbox(resp.choices[0].message.content)
    if bboxes is None:
        return [], [], token_usage

    bboxes = convert_coordinate_multi(bboxes, [width / 1000, height / 1000])

    crops, final_boxes = generate_multi_scale_crops(image, bboxes)

    return crops, final_boxes, token_usage


def process_single_image_folder(
    input_dir: str,
    problem_statement: str,
    output_dir: str
):

    os.makedirs(output_dir, exist_ok=True)

    files = os.listdir(input_dir)
    image_files = [
        f for f in files
        if f.lower().endswith((".png", ".jpg", ".jpeg", ".bmp"))
    ]

    if len(image_files) == 0:
        print("[ERROR] No image found in input_dir")
        return

    if len(image_files) > 1:
        print("[WARNING] More than one image found, using the first one")

    image_name = image_files[0]
    image_path = os.path.join(input_dir, image_name)

    base = os.path.splitext(image_name)[0]

    image = Image.open(image_path).convert("RGB")

    original_save_path = os.path.join(output_dir, image_name)
    image.save(original_save_path)

    crops, bboxes, token_usage = ground_multi_bbox(image, problem_statement)

    if not crops:
        print("[WARNING] No crops generated")
        return

    for i, crop in enumerate(crops):
        save_path = os.path.join(output_dir, f"{base}_crop_{i+1}.png")
        crop.save(save_path)

    vis_path = os.path.join(output_dir, f"{base}_visualized.png")
    draw_bboxes(image, bboxes, vis_path)

    return token_usage