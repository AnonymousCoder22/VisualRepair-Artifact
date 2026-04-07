import os
import json
import html as html_lib
import re
import subprocess
import time
from pathlib import Path

from PIL import Image

from Code.llm_io_stream import openai_chat, claude_chat, extract_json_from_text
from Code.model_utils import is_claude_model


"""
Merged support module for:
1. Screenshot capture / fallback helpers.
2. Validation `code.html` generation helpers.
3. Deterministic runtime script selection and HTML rewrite helpers.

Supported validation screenshot/runtime families in this repository revision:
- PrismJS
- highlight.js
- Chart.js
- marked.js
- react-pdf
"""


SCREENSHOT_TARGETS = (
    {
        "key": "prism",
        "mode_env": "GUIREPAIR_PRISM_SCREENSHOT_MODE",
        "mode_default": "playwright",
        "match": lambda repo_basename, instance_id: repo_basename == "prism",
    },
    {
        "key": "highlightjs",
        "mode_env": "GUIREPAIR_HIGHLIGHTJS_SCREENSHOT_MODE",
        "mode_default": "playwright",
        "match": lambda repo_basename, instance_id: repo_basename == "highlight.js",
    },
    {
        "key": "chartjs",
        "mode_env": "GUIREPAIR_CHARTJS_SCREENSHOT_MODE",
        "mode_default": "playwright",
        "match": lambda repo_basename, instance_id: repo_basename == "Chart.js",
    },
    {
        "key": "markedjs",
        "mode_env": "GUIREPAIR_MARKEDJS_SCREENSHOT_MODE",
        "mode_default": "playwright",
        "match": lambda repo_basename, instance_id: repo_basename == "marked",
    },
    {
        "key": "reactpdf",
        "mode_env": "GUIREPAIR_REACTPDF_SCREENSHOT_MODE",
        "mode_default": "",
        "match": lambda repo_basename, instance_id: repo_basename == "react-pdf",
    },
)

HIGHLIGHTJS_TEMPLATE2_INSTANCE_IDS = {
    "highlightjs__highlight.js-2684",
    "highlightjs__highlight.js-2703",
    "highlightjs__highlight.js-2704",
    "highlightjs__highlight.js-2726",
    "highlightjs__highlight.js-2727",
    "highlightjs__highlight.js-2740",
    "highlightjs__highlight.js-2750",
    "highlightjs__highlight.js-2765",
    "highlightjs__highlight.js-2785",
    "highlightjs__highlight.js-2811",
    "highlightjs__highlight.js-2897",
    "highlightjs__highlight.js-2899",
    "highlightjs__highlight.js-2927",
    "highlightjs__highlight.js-2932",
    "highlightjs__highlight.js-2958",
    "highlightjs__highlight.js-2960",
    "highlightjs__highlight.js-2969",
    "highlightjs__highlight.js-2972",
    "highlightjs__highlight.js-3000",
    "highlightjs__highlight.js-3018",
}

HIGHLIGHTJS_TEMPLATE1_INSTANCE_IDS = {
    "highlightjs__highlight.js-3070",
    "highlightjs__highlight.js-3154",
    "highlightjs__highlight.js-3203",
    "highlightjs__highlight.js-3207",
    "highlightjs__highlight.js-3212",
    "highlightjs__highlight.js-3249",
    "highlightjs__highlight.js-3278",
    "highlightjs__highlight.js-3287",
    "highlightjs__highlight.js-3301",
    "highlightjs__highlight.js-3312",
    "highlightjs__highlight.js-3316",
    "highlightjs__highlight.js-3367",
    "highlightjs__highlight.js-3381",
    "highlightjs__highlight.js-3411",
    "highlightjs__highlight.js-3438",
    "highlightjs__highlight.js-3457",
    "highlightjs__highlight.js-3516",
    "highlightjs__highlight.js-3559",
    "highlightjs__highlight.js-3644",
}


def project_root_from_validation_dir() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def project_root_path_from_validation_dir() -> Path:
    return Path(project_root_from_validation_dir())


def tools_screenshot_root_from_validation_dir() -> Path:
    return project_root_path_from_validation_dir() / "Tools" / "screenshot"


def resolve_validation_screenshot_script_path(repo_basename: str) -> str:
    screenshot_root = tools_screenshot_root_from_validation_dir()
    script_candidates = {
        "prism": [
            screenshot_root / "prismjs_capture_from_bug_info.cjs",
        ],
        "highlight.js": [
            screenshot_root / "highlightjs_capture_from_bug_info.cjs",
        ],
        "Chart.js": [
            screenshot_root / "chartjs_capture_from_bug_info.cjs",
        ],
        "marked": [
            screenshot_root / "markedjs_capture_from_bug_info.cjs",
        ],
        "react-pdf": [
            screenshot_root / "reactpdf_capture_from_bug_info.cjs",
        ],
    }

    for candidate in script_candidates.get(repo_basename, []):
        if candidate.is_file():
            return str(candidate)
    return ""


def make_token_usage_record(args=None, *, base_model: str = "", skipped: bool = True) -> dict:
    resolved_base_model = base_model or getattr(args, "base_model", "")
    return {
        "base_model": resolved_base_model,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "skipped": bool(skipped),
    }


def merge_token_usage_record(target: dict, source: dict) -> dict:
    if not isinstance(target, dict) or not isinstance(source, dict):
        return target
    target["prompt_tokens"] = int(target.get("prompt_tokens") or 0) + int(source.get("prompt_tokens") or 0)
    target["completion_tokens"] = int(target.get("completion_tokens") or 0) + int(source.get("completion_tokens") or 0)
    target["total_tokens"] = int(target.get("total_tokens") or 0) + int(source.get("total_tokens") or 0)
    if not target.get("base_model") and source.get("base_model"):
        target["base_model"] = source.get("base_model")
    target["skipped"] = bool(int(target.get("total_tokens") or 0) == 0)
    return target


# Shared file/process helpers
def read_file(file_path):
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as file:
        return file.read()


def save_file(file_path, data, encoding='utf-8'):
    try:
        with open(file_path, 'w', encoding=encoding) as file:
            file.write(data)
    except UnicodeEncodeError as e:
        print(f"Unicode encode error: {e}")
        return "Unicode encode error"


def run_process(argv, cwd: str, timeout_seconds: int = 300):
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
        return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()
    except subprocess.TimeoutExpired as e:
        return 124, "", f"TIMEOUT after {timeout_seconds}s: {e}"
    except FileNotFoundError as e:
        return 127, "", f"NOT FOUND: {e}"
    except Exception as e:
        return 1, "", f"ERROR: {e}"


def resolve_screenshot_target(instance_id, instance_repo_path):
    repo_basename = os.path.basename(os.path.normpath(instance_repo_path))
    for target in SCREENSHOT_TARGETS:
        if target["match"](repo_basename, instance_id):
            return repo_basename, target
    return repo_basename, None


def run_screenshot(argv: list[str], output_png_path: str, logger, timeout_seconds: int = 180) -> bool:
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        logger.warning(f"Screenshot capture timed out after {timeout_seconds}s: {' '.join(argv)}")
        return False
    except Exception as e:
        logger.warning(f"Screenshot capture failed to execute: {e}")
        return False

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        msg = stderr if stderr else stdout
        if msg:
            logger.warning(f"Screenshot command failed (rc={result.returncode}): {msg}")
        else:
            logger.warning(f"Screenshot command failed (rc={result.returncode}) with empty output.")
        return False

    try:
        if os.path.isfile(output_png_path) and os.path.getsize(output_png_path) > 0:
            return True
        logger.warning(f"Screenshot command succeeded but output missing/empty: {output_png_path}")
        return False
    except Exception:
        logger.warning(f"Screenshot command succeeded but failed to stat output: {output_png_path}")
        return False


def check_nonempty_file(path: str) -> bool:
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except Exception:
        return False


def check_capture_ui_screenshot(instance_id, instance_repo_path, output_png_path, logger, html_file=None) -> bool:
    try:
        os.makedirs(os.path.dirname(output_png_path), exist_ok=True)
    except Exception:
        pass

    output_png_path = os.path.abspath(output_png_path)
    repo_basename, target = resolve_screenshot_target(instance_id, instance_repo_path)
    if not target:
        return False

    script_path = resolve_validation_screenshot_script_path(repo_basename)
    if not script_path or not os.path.isfile(script_path):
        logger.warning(f"Screenshot script not found: {script_path}")
        return False

    mode_env = target.get("mode_env")
    screenshot_mode = os.getenv(mode_env, target.get("mode_default", "")) if mode_env else ""
    argv = ["node", script_path, "--repo", instance_repo_path, "--out", output_png_path]
    if screenshot_mode:
        argv.extend(["--mode", screenshot_mode])
    if instance_id:
        argv.extend(["--instance-id", str(instance_id)])
    if html_file:
        argv.extend(["--html", html_file])
    if repo_basename == "react-pdf":
        argv.append("--force-build")

    logger.info(f"Capture screenshot: {' '.join(argv)}")
    return run_screenshot(argv, output_png_path, logger)


def capture_ui_screenshot_with_retry(instance_id, instance_repo_path, output_png_path, logger, html_file=None) -> bool:
    try:
        max_attempts = int(float(os.getenv("GUIREPAIR_SCREENSHOT_MAX_ATTEMPTS", "3")))
    except Exception:
        max_attempts = 3
    try:
        retry_sleep = float(os.getenv("GUIREPAIR_SCREENSHOT_RETRY_SLEEP_SECONDS", "1"))
    except Exception:
        retry_sleep = 1.0

    if max_attempts < 1:
        max_attempts = 1

    for attempt in range(1, max_attempts + 1):
        try:
            if os.path.isfile(output_png_path):
                os.remove(output_png_path)
        except Exception:
            pass

        ok = check_capture_ui_screenshot(
            instance_id,
            instance_repo_path,
            output_png_path,
            logger,
            html_file=html_file,
        )
        if ok and check_nonempty_file(output_png_path):
            return True

        logger.warning(f"Screenshot attempt {attempt}/{max_attempts} failed for {output_png_path}")
        if attempt < max_attempts and retry_sleep > 0:
            time.sleep(retry_sleep)

    return check_nonempty_file(output_png_path)


def write_placeholder_png(output_png_path: str, logger, reason: str = "") -> bool:
    try:
        os.makedirs(os.path.dirname(output_png_path), exist_ok=True)
    except Exception:
        pass
    try:
        img = Image.new("RGB", (16, 16), (255, 255, 255))
        img.save(output_png_path, format="PNG")
        if reason:
            logger.warning(f"Wrote placeholder PNG: {output_png_path} ({reason})")
        else:
            logger.warning(f"Wrote placeholder PNG: {output_png_path}")
        return True
    except Exception as e:
        logger.warning(f"Failed to write placeholder PNG {output_png_path}: {e}")
        return False


def check_png_from_baseline_or_placeholder(output_png_path: str, baseline_png_path: str, logger, reason: str = "") -> bool:
    if check_nonempty_file(output_png_path):
        return True

    if check_nonempty_file(baseline_png_path):
        try:
            with open(baseline_png_path, "rb") as src, open(output_png_path, "wb") as dst:
                dst.write(src.read())
            if check_nonempty_file(output_png_path):
                if reason:
                    logger.warning(f"Filled missing PNG by copying baseline: {output_png_path} ({reason})")
                else:
                    logger.warning(f"Filled missing PNG by copying baseline: {output_png_path}")
                return True
        except Exception as e:
            logger.warning(f"Failed to copy baseline PNG to {output_png_path}: {e}")

    return write_placeholder_png(output_png_path, logger, reason=reason)


def parse_patch_key_num(sample_key: str) -> float:
    try:
        return float(sample_key.split("/")[0])
    except Exception:
        return -1.0


def pick_patch_key_for_index(patches_edits_dict, patch_no: int):
    candidates = []
    for key in patches_edits_dict.keys():
        num = parse_patch_key_num(key)
        if int(num) == int(patch_no):
            candidates.append((num, key))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])
    for num, key in candidates:
        if abs(num - float(patch_no)) < 1e-9:
            return key
    return candidates[0][1]


def resolve_prism_code_template_path(logger) -> str:
    candidates = [
        os.path.join(project_root_from_validation_dir(), "Tools", "screenshot", "code_template", "PrismJS", "template_prismjs.html"),
    ]
    seen = set()
    for p in candidates:
        norm = os.path.normpath(p)
        if norm in seen:
            continue
        seen.add(norm)
        if os.path.isfile(norm):
            return norm
    logger.warning(f"No prism template found in candidates: {candidates}")
    return ""


def resolve_highlightjs_code_template_candidates(instance_id: str) -> list[str]:
    template_root = tools_screenshot_root_from_validation_dir() / "code_template" / "highlightjs"
    if str(instance_id or "") in HIGHLIGHTJS_TEMPLATE2_INSTANCE_IDS:
        preferred_name = "template_highlightjs2.html"
    else:
        preferred_name = "template_highlightjs1.html"
    return [
        str(template_root / preferred_name),
        str(template_root / "template_highlightjs1.html"),
    ]


def resolve_chartjs_code_template_candidates() -> list[str]:
    return [
        str(tools_screenshot_root_from_validation_dir() / "code_template" / "chartjs" / "template_chartjs.html"),
    ]


def extract_first_markdown_code_block(text: str):
    blocks = extract_markdown_code_blocks(text)
    if not blocks:
        return "", ""
    return blocks[0]


def extract_markdown_code_blocks(text: str, _depth: int = 0) -> list[tuple[str, str]]:
    if not text or _depth > 2:
        return []

    block_re = re.compile(
        r"(?ms)(?P<fence>`{3,})(?P<lang>[^\n`]*)\r?\n(?P<code>.*?)(?:\r?\n(?P=fence))"
    )
    blocks = []
    seen = set()
    for m in block_re.finditer(text):
        lang = (m.group("lang") or "").strip()
        code = (m.group("code") or "").strip("\n\r")
        if code:
            key = (code, lang)
            if key not in seen:
                seen.add(key)
                blocks.append(key)
            if "```" in code:
                for nested_code, nested_lang in extract_markdown_code_blocks(code, _depth + 1):
                    nested_key = (nested_code, nested_lang)
                    if nested_key in seen:
                        continue
                    seen.add(nested_key)
                    blocks.append(nested_key)
    return blocks


def parse_json_payload_from_text(text_payload):
    if isinstance(text_payload, dict):
        return text_payload
    if not isinstance(text_payload, str):
        return {}

    parsed = extract_json_from_text(text_payload)
    if isinstance(parsed, dict):
        return parsed

    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text_payload, flags=re.IGNORECASE)
    candidate = fenced.group(1).strip() if fenced else text_payload.strip()
    try:
        loaded = json.loads(candidate)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


# LLM extraction / generation helpers
def sanitize_extracted_code_text(code_text: str, logger) -> str:
    text = html_lib.unescape(str(code_text or "")).strip()
    if not text:
        return ""

    fenced = re.fullmatch(r"```[^\n`]*\n([\s\S]*?)```", text, flags=re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip("\n")

    code_block_match = re.search(
        r"<pre\b[^>]*>\s*<code\b[^>]*>(?P<body>[\s\S]*?)</code>\s*</pre>",
        text,
        flags=re.IGNORECASE,
    )
    if code_block_match:
        logger.info("IMAGE->code extraction returned HTML code block wrapper; keeping only <pre><code> body.")
        return html_lib.unescape(code_block_match.group("body")).strip("\n")

    whole_html_match = re.search(r"<html\b|<!DOCTYPE\s+html", text, flags=re.IGNORECASE)
    if whole_html_match:
        logger.warning("IMAGE->code extraction returned a full HTML document; discarding wrapper markup.")
        stripped = re.sub(r"<!DOCTYPE[\s\S]*?>", "", text, flags=re.IGNORECASE)
        stripped = re.sub(r"</?(?:html|head|body)\b[^>]*>", "", stripped, flags=re.IGNORECASE)
        return stripped.strip()

    return text


def sanitize_generated_html_text(html_text: str, logger) -> str:
    text = html_lib.unescape(str(html_text or "")).strip()
    if not text:
        return ""

    fenced = re.fullmatch(r"```(?:html)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()

    whole_html_match = re.search(r"<!DOCTYPE\s+html|<html\b", text, flags=re.IGNORECASE)
    if whole_html_match:
        start = whole_html_match.start()
        end_match = re.search(r"</html>\s*$", text, flags=re.IGNORECASE)
        if end_match:
            return text[start:end_match.end()].strip()
        return text[start:].strip()

    if re.search(r"<(?:head|body|pre|code|script|style)\b", text, flags=re.IGNORECASE):
        logger.warning("IMAGE+template->html generation returned HTML without outer <html>; keeping raw HTML fragment.")
        return text

    logger.warning("IMAGE+template->html generation returned non-HTML text; ignoring it.")
    return ""


def generate_html_from_images_with_template_via_llm(
    problem_statement: str,
    image_file_list: list,
    template_html: str,
    args,
    logger,
):
    empty_usage = make_token_usage_record(args, skipped=True)
    if not image_file_list or not template_html:
        return "", empty_usage

    issue_excerpt = (problem_statement or "").strip()
    if len(issue_excerpt) > 4000:
        issue_excerpt = issue_excerpt[:4000] + "\n... (truncated)"

    system_prompt = """
You generate a complete validation HTML file from bug screenshots and a provided HTML template.
Return JSON only with keys:
{
  "html": "the full final HTML document"
}
Rules:
- Output JSON only, no markdown.
- Use the provided template HTML as the base document.
- Return a complete HTML document, preserving the template structure unless the screenshots clearly require changing visible code content or the code language class.
- Keep existing scripts, styles, runtime initialization, and page bootstrap logic from the template unless the screenshots clearly require a different code snippet or code block language.
- Do not omit required script tags or wrapper structure from the template.
- Prefer changing only the code content shown in the screenshots and any directly related language-* class.
- If uncertain, stay close to the template and make the smallest necessary edits.
"""
    user_prompt = [
        {
            "type": "text",
            "text": f"""Generate the final validation HTML by combining the provided template with the code shown in the bug images.
Issue context:
'''
{issue_excerpt}
'''

Template HTML:
```html
{template_html}
```
""",
        }
    ] + list(image_file_list)

    try:
        if is_claude_model(getattr(args, "base_model", "")):
            llm_results, llm_text, token_usage = claude_chat(system_prompt, user_prompt, args, 0.0, 1)
        else:
            llm_results, token_usage = openai_chat(system_prompt, user_prompt, args, 0.0, 1)
    except Exception as e:
        logger.warning(f"IMAGE+template->html generation failed: {e}")
        return "", empty_usage

    if not isinstance(llm_results, dict) or not llm_results:
        return "", token_usage

    first_key = next(iter(llm_results))
    raw_response = llm_results.get(first_key)
    payload = parse_json_payload_from_text(raw_response)

    html_text = ""
    if isinstance(payload, dict):
        html_text = str(payload.get("html", "")).strip()
    if not html_text and isinstance(raw_response, str):
        html_text = raw_response.strip()

    final_html = sanitize_generated_html_text(html_text, logger)
    if not final_html:
        return "", token_usage

    token_usage["skipped"] = False
    return final_html, token_usage


def extract_code_from_images_via_llm(problem_statement: str, image_file_list: list, args, logger):
    empty_usage = make_token_usage_record(args, skipped=True)
    if not image_file_list:
        return "", "", empty_usage

    issue_excerpt = (problem_statement or "").strip()
    if len(issue_excerpt) > 4000:
        issue_excerpt = issue_excerpt[:4000] + "\n... (truncated)"

    system_prompt = """
You extract code snippets from bug screenshots for syntax-highlighting validation.
Return JSON only with keys:
{
  "language": "short language id like sql/javascript/css/plain",
  "code": "the code snippet text shown in the screenshot"
}
Rules:
- Output JSON only, no markdown.
- Return only the raw code snippet text that belongs inside the existing <pre><code>...</code></pre> block.
- Do not return <pre>, <code>, <script>, <html>, <head>, <body>, or any other wrapper tags.
- Do not rewrite or invent any surrounding template/script content outside the visible code snippet.
- DO not change page initialization / bootstrap logic or other runtime scripts; only extract the code snippet text and its language if visible in the screenshots.
- Keep line breaks and indentation in code.
- If uncertain, provide best-effort code and set language to "plain".
"""
    user_prompt = [
        {
            "type": "text",
            "text": f"""Extract the code snippet visible in these bug images.
Issue context:
'''
{issue_excerpt}
'''
""",
        }
    ] + list(image_file_list)

    try:
        if is_claude_model(getattr(args, "base_model", "")):
            llm_results, llm_text, token_usage = claude_chat(system_prompt, user_prompt, args, 0.0, 1)
        else:
            llm_results, token_usage = openai_chat(system_prompt, user_prompt, args, 0.0, 1)
    except Exception as e:
        logger.warning(f"IMAGE->code extraction failed: {e}")
        return "", "", empty_usage

    if not isinstance(llm_results, dict) or not llm_results:
        return "", "", token_usage

    first_key = next(iter(llm_results))
    payload = parse_json_payload_from_text(llm_results.get(first_key))
    if not isinstance(payload, dict):
        return "", "", token_usage

    code_text = str(payload.get("code", "")).strip()
    language = str(payload.get("language", "")).strip()

    if not code_text and isinstance(llm_results.get(first_key), str):
        code_text, maybe_lang = extract_first_markdown_code_block(llm_results.get(first_key))
        if not language:
            language = maybe_lang

    return sanitize_extracted_code_text(code_text, logger), language, token_usage


# Local fallback HTML assembly helpers
def build_code_html_locally_from_template(
    problem_statement: str,
    image_file_list: list,
    template_html: str,
    args,
    logger,
    replace_html_fn,
):
    token_usage = make_token_usage_record(args, skipped=True)
    code_text = ""
    language = ""
    source = "template_default"

    code_text, language, token_usage = extract_code_from_images_via_llm(
        problem_statement,
        image_file_list or [],
        args,
        logger,
    )
    if code_text:
        source = "image"
    else:
        fallback_code, fallback_lang = extract_first_markdown_code_block(problem_statement or "")
        if fallback_code:
            code_text = fallback_code
            language = fallback_lang or language
            source = "problem_statement"

    final_html = template_html
    if code_text:
        final_html = replace_html_fn(template_html, code_text, language, logger)

    return final_html, token_usage, source, len(code_text or "")


def write_code_html_with_llm_then_fallback(
    instance_id,
    code_html_path: str,
    problem_statement: str,
    image_file_list: list,
    template_path: str,
    template_html: str,
    args,
    logger,
    replace_html_fn,
):
    total_token_usage = make_token_usage_record(args, skipped=True)

    final_html, token_usage = generate_html_from_images_with_template_via_llm(
        problem_statement,
        image_file_list or [],
        template_html,
        args,
        logger,
    )
    merge_token_usage_record(total_token_usage, token_usage)
    if final_html:
        save_file(code_html_path, final_html)
        logger.info(
            f"Generated code.html for {instance_id}: {code_html_path} "
            f"(template={template_path}, source=image_plus_template_llm, html_len={len(final_html)})"
        )
        return code_html_path, total_token_usage

    final_html, fallback_token_usage, source, code_len = build_code_html_locally_from_template(
        problem_statement,
        image_file_list or [],
        template_html,
        args,
        logger,
        replace_html_fn,
    )
    merge_token_usage_record(total_token_usage, fallback_token_usage)
    save_file(code_html_path, final_html)
    logger.info(
        f"Generated missing code.html for {instance_id}: {code_html_path} "
        f"(template={template_path}, code_source={source}, code_len={code_len})"
    )
    return code_html_path, total_token_usage


def normalize_language_for_code_class(language: str) -> str:
    lang = (language or "").strip().lower()
    if lang.startswith("language-"):
        lang = lang[len("language-"):]
    lang = re.sub(r"[^a-z0-9_+\-]", "", lang)
    return lang or "plain"


HIGHLIGHTJS_GENERIC_LANGUAGES = {"", "none", "plain", "plaintext", "text", "txt"}
HIGHLIGHTJS_LANGUAGE_ALIASES = {
    "none": "",
    "plain": "",
    "plaintext": "",
    "text": "",
    "txt": "",
    "py": "python",
    "js": "javascript",
    "ts": "typescript",
    "tsql": "sql",
    "shell": "bash",
    "sh": "bash",
    "zsh": "bash",
    "kt": "kotlin",
}
HIGHLIGHTJS_ISSUE_HEADER_RE = re.compile(r"^\(([^)]+)\)")
HIGHLIGHTJS_WHICH_LANG_RE = re.compile(
    r"\*\*Which language seems to have the issue\?\*\*\s*[\r\n]+([^\r\n]+)",
    flags=re.IGNORECASE,
)


def normalize_highlightjs_language(language: str) -> str:
    lang = normalize_language_for_code_class(language)
    if lang == "plain":
        lang = ""
    return HIGHLIGHTJS_LANGUAGE_ALIASES.get(lang, lang)


def extract_highlightjs_language_candidates(text: str) -> list[str]:
    if not text:
        return []

    candidates = []
    seen = set()
    for pattern in (r"`([^`]+)`", r'"([^"]+)"', r"'([^']+)'"):
        for match in re.finditer(pattern, text):
            candidate = normalize_highlightjs_language(match.group(1))
            if candidate in seen:
                continue
            seen.add(candidate)
            candidates.append(candidate)

    for token in re.findall(r"[A-Za-z][A-Za-z0-9_+\-]*", text):
        candidate = normalize_highlightjs_language(token)
        if candidate in seen:
            continue
        seen.add(candidate)
        candidates.append(candidate)
    return candidates


def infer_highlightjs_language_from_problem_statement(problem_statement: str) -> str:
    text = str(problem_statement or "").strip()
    if not text:
        return ""

    header_match = HIGHLIGHTJS_ISSUE_HEADER_RE.match(text)
    if header_match:
        header_bits = [bit.strip() for bit in re.split(r"[,/]", header_match.group(1)) if bit.strip()]
        for candidate in header_bits:
            normalized = normalize_highlightjs_language(candidate)
            if normalized:
                return normalized

    which_lang_match = HIGHLIGHTJS_WHICH_LANG_RE.search(text)
    if which_lang_match:
        for candidate in extract_highlightjs_language_candidates(which_lang_match.group(1)):
            if candidate:
                return candidate

    return ""


def select_highlightjs_problem_statement_code(problem_statement: str):
    blocks = extract_markdown_code_blocks(problem_statement or "")
    if not blocks:
        return "", "", -1

    inferred_language = infer_highlightjs_language_from_problem_statement(problem_statement)
    for idx, (code_text, block_language) in enumerate(blocks):
        code = (code_text or "").strip("\n\r")
        if not code:
            continue

        lowered = code.lower()
        if "<span" in lowered and "hljs-" in lowered:
            continue
        if "```" in code:
            continue

        language = normalize_highlightjs_language(block_language)
        if not language:
            language = inferred_language
        return code, language, idx

    return "", inferred_language, -1


def rewrite_code_open_tag_language(code_open_tag: str, language: str) -> str:
    lang = normalize_language_for_code_class(language)
    class_re = re.compile(r'(\bclass\s*=\s*)(["\'])([^"\']*)(\2)', flags=re.IGNORECASE)
    m = class_re.search(code_open_tag or "")
    if not m:
        return re.sub(r">\s*$", f' class="language-{lang}">', code_open_tag, count=1)

    classes = [c for c in re.split(r"\s+", m.group(3).strip()) if c]
    classes = [c for c in classes if not c.startswith("language-")]
    classes.append(f"language-{lang}")
    new_class = " ".join(classes)
    return (
        code_open_tag[:m.start()]
        + f"{m.group(1)}{m.group(2)}{new_class}{m.group(2)}"
        + code_open_tag[m.end():]
    )


def replace_prism_code_block_html(base_html: str, code_text: str, language: str, logger) -> str:
    if not base_html:
        return base_html

    code_block_re = re.compile(
        r"(?P<prefix><pre\b[^>]*>\s*<code\b[^>]*>)(?P<body>[\s\S]*?)(?P<suffix></code>\s*</pre>)",
        flags=re.IGNORECASE,
    )
    m = code_block_re.search(base_html)
    if not m:
        logger.warning("Template code.html has no <pre><code> block; skipping code replacement.")
        return base_html

    new_prefix = m.group("prefix")
    if language:
        new_prefix = re.sub(
            r"<code\b[^>]*>",
            lambda mm: rewrite_code_open_tag_language(mm.group(0), language),
            new_prefix,
            count=1,
            flags=re.IGNORECASE,
        )

    escaped_code = html_lib.escape(code_text or "", quote=False)
    return base_html[:m.start()] + new_prefix + escaped_code + m.group("suffix") + base_html[m.end():]


def ensure_prism_code_html_for_val(instance_id, instance_repo_path, problem_statement, image_file_list, args, logger):
    code_html_path = os.path.join(instance_repo_path, "code.html")
    token_usage = make_token_usage_record(args, skipped=True)
    if os.path.isfile(code_html_path):
        logger.info(f"Existing code.html found; regenerating and overwriting: {code_html_path}")

    template_path = resolve_prism_code_template_path(logger)
    if not template_path:
        return code_html_path, token_usage

    try:
        template_html = read_file(template_path)
    except Exception as e:
        logger.warning(f"Failed to read template code.html: {template_path}, error={e}")
        return code_html_path, token_usage

    return write_code_html_with_llm_then_fallback(
        instance_id,
        code_html_path,
        problem_statement,
        image_file_list,
        template_path,
        template_html,
        args,
        logger,
        replace_prism_code_block_html,
    )


def resolve_highlightjs_code_template_path(instance_id, logger) -> str:
    if instance_id not in HIGHLIGHTJS_TEMPLATE1_INSTANCE_IDS and instance_id not in HIGHLIGHTJS_TEMPLATE2_INSTANCE_IDS:
        logger.warning(
            f"Highlightjs instance_id not found in template mapping, fallback to template1: {instance_id}"
        )

    seen = set()
    candidates = resolve_highlightjs_code_template_candidates(instance_id)
    for candidate in candidates:
        norm = os.path.normpath(candidate)
        if norm in seen:
            continue
        seen.add(norm)
        if os.path.isfile(norm):
            return norm

    logger.warning(f"No highlightjs code.html template found in candidates: {candidates}")
    return ""


def resolve_chartjs_code_template_path(logger) -> str:
    candidates = resolve_chartjs_code_template_candidates()
    seen = set()
    for p in candidates:
        norm = os.path.normpath(p)
        if norm in seen:
            continue
        seen.add(norm)
        if os.path.isfile(norm):
            return norm
    logger.warning(f"No chartjs code.html template found in candidates: {candidates}")
    return ""


def resolve_markedjs_code_template_path(logger) -> str:
    candidates = [
        os.path.join(project_root_from_validation_dir(), "Tools", "screenshot", "code_template", "markedjs", "template_marked.html"),
    ]
    seen = set()
    for p in candidates:
        norm = os.path.normpath(p)
        if norm in seen:
            continue
        seen.add(norm)
        if os.path.isfile(norm):
            return norm
    logger.warning(f"No markedjs code.html template found in candidates: {candidates}")
    return ""


def ensure_highlightjs_code_html_for_val(instance_id, instance_repo_path, problem_statement, image_file_list, args, logger):
    code_html_path = os.path.join(instance_repo_path, "code.html")
    token_usage = make_token_usage_record(args, skipped=True)
    if os.path.isfile(code_html_path):
        logger.info(f"Existing code.html found; regenerating and overwriting: {code_html_path}")

    template_path = resolve_highlightjs_code_template_path(instance_id, logger)
    if not template_path:
        return code_html_path, token_usage

    try:
        template_html = read_file(template_path)
    except Exception as e:
        logger.warning(f"Failed to read highlightjs template: {template_path}, error={e}")
        return code_html_path, token_usage

    code_text, language, block_index = select_highlightjs_problem_statement_code(problem_statement)
    source = "problem_statement_deterministic"
    if not code_text:
        llm_code_text, llm_language, llm_token_usage = extract_code_from_images_via_llm(
            problem_statement,
            image_file_list or [],
            args,
            logger,
        )
        token_usage = llm_token_usage
        if llm_code_text:
            code_text = llm_code_text
            language = normalize_highlightjs_language(llm_language) or infer_highlightjs_language_from_problem_statement(problem_statement)
            block_index = -1
            source = "image_code_llm"

    final_html = template_html
    if code_text:
        final_html = replace_prism_code_block_html(template_html, code_text, language, logger)
    else:
        logger.warning(f"Highlightjs could not derive code from problem_statement or image extraction: {instance_id}")

    save_file(code_html_path, final_html)
    logger.info(
        f"Generated highlightjs code.html for {instance_id}: {code_html_path} "
        f"(template={template_path}, source={source}, "
        f"lang={language or 'plain'}, block_index={block_index}, code_len={len(code_text or '')})"
    )
    return code_html_path, token_usage


def replace_marked_input_html(base_html: str, markdown_text: str, language: str, logger) -> str:
    if not markdown_text:
        return base_html
    return replace_prism_code_block_html(base_html, markdown_text, language or "markdown", logger)


def normalize_chartjs_inline_script(code_text: str) -> str:
    text = str(code_text or "").strip()
    if not text:
        return ""
    fenced_code, _ = extract_first_markdown_code_block(text)
    if fenced_code:
        text = fenced_code.strip()

    # Chart.js reproduce snippets sometimes use ESM imports even though the
    # template loads the UMD bundle globally.
    text = re.sub(
        r"^\s*import\s+.+?\s+from\s+['\"]chart\.js(?:/auto)?['\"]\s*;?\s*$",
        "",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^\s*Chart\.register\(\s*\.\.\.\s*registerables\s*\)\s*;?\s*$",
        "",
        text,
        flags=re.MULTILINE,
    )

    # Reuse the template canvas/context regardless of the original element id.
    text = re.sub(
        r"document\.getElementById\(\s*['\"][^'\"]+['\"]\s*\)\.getContext\(\s*['\"]2d['\"]\s*\)",
        "window.ctx",
        text,
    )
    text = re.sub(
        r"document\.getElementById\(\s*['\"][^'\"]+['\"]\s*\)",
        "window.canvas",
        text,
    )
    return text.strip()


def replace_chartjs_inline_script_html(base_html: str, code_text: str, language: str, logger) -> str:
    if not base_html:
        return base_html

    normalized = normalize_chartjs_inline_script(code_text)
    if not normalized:
        return base_html

    code_block_re = re.compile(
        r"(?P<prefix><code\b[^>]*\bid=[\"']chart-config-source[\"'][^>]*>)(?P<body>[\s\S]*?)(?P<suffix></code>)",
        flags=re.IGNORECASE,
    )
    m = code_block_re.search(base_html)
    if not m:
        logger.warning("Chart.js template has no #chart-config-source block; falling back to generic code replacement.")
        return replace_prism_code_block_html(base_html, normalized, language or "javascript", logger)

    new_prefix = m.group("prefix")
    if language:
        new_prefix = rewrite_code_open_tag_language(new_prefix, language)

    escaped_code = html_lib.escape(normalized, quote=False)
    return base_html[:m.start()] + new_prefix + escaped_code + m.group("suffix") + base_html[m.end():]


def strip_leading_comment_prelude(text: str) -> str:
    stripped = str(text or "")
    while True:
        updated = re.sub(r"^\s*/\*[\s\S]*?\*/\s*", "", stripped, count=1)
        if updated != stripped:
            stripped = updated
            continue
        updated = re.sub(r"^(?:\s*//[^\n]*\n)+\s*", "", stripped, count=1)
        if updated != stripped:
            stripped = updated
            continue
        break
    return stripped.lstrip()


def looks_like_html_markup(text: str) -> bool:
    stripped = strip_leading_comment_prelude(text)
    return stripped.startswith("<")


def looks_like_html_document(text: str) -> bool:
    stripped = strip_leading_comment_prelude(text)
    return bool(re.search(r"<!DOCTYPE\s+html|<html\b", stripped, flags=re.IGNORECASE))


def wrap_html_fragment(fragment: str) -> str:
    body = str(fragment or "").strip()
    if not body:
        return ""
    return (
        "<!DOCTYPE html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "  <meta charset=\"UTF-8\" />\n"
        "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />\n"
        "</head>\n"
        "<body>\n"
        f"{body}\n"
        "</body>\n"
        "</html>\n"
    )


CHARTJS_RUNTIME_META_FILENAME = "chartjs_runtime_meta.json"


def chartjs_instance_root_from_repo_path(instance_repo_path: str) -> str:
    repo_path = os.path.normpath(instance_repo_path or "")
    return os.path.dirname(os.path.dirname(repo_path))


def load_chartjs_bug_info(instance_repo_path: str, logger) -> dict:
    bug_info_path = os.path.join(chartjs_instance_root_from_repo_path(instance_repo_path), "bug_info.json")
    if not check_nonempty_file(bug_info_path):
        return {}
    try:
        payload = json.loads(read_file(bug_info_path))
        return payload if isinstance(payload, dict) else {}
    except Exception as e:
        logger.warning(f"Failed to read Chart.js bug_info.json from {bug_info_path}: {e}")
        return {}


def load_chartjs_original_image_info(instance_repo_path: str, logger) -> dict:
    image_dir = os.path.join(chartjs_instance_root_from_repo_path(instance_repo_path), "IMAGE")
    if not os.path.isdir(image_dir):
        return {}

    for file_name in sorted(os.listdir(image_dir)):
        lower_name = file_name.lower()
        if not lower_name.endswith(".png"):
            continue
        image_path = os.path.join(image_dir, file_name)
        try:
            with Image.open(image_path) as img:
                width, height = img.size
            return {
                "path": image_path,
                "width": int(width),
                "height": int(height),
            }
        except Exception as e:
            logger.warning(f"Failed to inspect Chart.js original image {image_path}: {e}")
    return {}


def clamp_int(value, lower: int, upper: int) -> int:
    try:
        parsed = int(round(float(value)))
    except Exception:
        parsed = lower
    return max(lower, min(upper, parsed))


def parse_chart_count_from_text(text: str) -> int:
    if not text:
        return 0
    m = re.search(r"(\d+)\s+charts?\b", text, flags=re.IGNORECASE)
    if not m:
        return 0
    try:
        return int(m.group(1))
    except Exception:
        return 0


def looks_like_chartjs_config_object(text: str) -> bool:
    lowered = str(text or "").lower()
    if "type" not in lowered or "data" not in lowered:
        return False
    return any(token in lowered for token in ("datasets", "labels", "options", "scales"))


def extract_balanced_brace_block(text: str, start_index: int) -> str:
    if not text or start_index < 0 or start_index >= len(text) or text[start_index] != "{":
        return ""

    depth = 0
    in_string = ""
    escape = False

    for idx in range(start_index, len(text)):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == in_string:
                in_string = ""
            continue

        if ch in ("'", '"', "`"):
            in_string = ch
            continue
        if ch == "{":
            depth += 1
            continue
        if ch == "}":
            depth -= 1
            if depth == 0:
                return text[start_index:idx + 1]
    return ""


def extract_chartjs_config_object(code_text: str) -> str:
    text = strip_leading_comment_prelude(code_text).strip()
    if not text:
        return ""

    if text.startswith("{"):
        candidate = extract_balanced_brace_block(text, 0)
        if looks_like_chartjs_config_object(candidate):
            return candidate.strip()

    assignment_patterns = [
        r"(?:const|let|var)\s+\w+\s*=\s*",
        r"\b\w+\s*=\s*",
    ]
    for pattern in assignment_patterns:
        for m in re.finditer(pattern, text):
            brace_index = text.find("{", m.end())
            if brace_index < 0:
                continue
            candidate = extract_balanced_brace_block(text, brace_index)
            if looks_like_chartjs_config_object(candidate):
                return candidate.strip()

    chart_index = text.find("new Chart")
    if chart_index >= 0:
        brace_index = text.find("{", chart_index)
        if brace_index >= 0:
            candidate = extract_balanced_brace_block(text, brace_index)
            if looks_like_chartjs_config_object(candidate):
                return candidate.strip()

    return ""


def pick_chartjs_issue_config(problem_statement: str, hints_text: str) -> str:
    best_code = ""
    best_score = -1

    for source_name, raw_text in (
        ("problem_statement", problem_statement or ""),
        ("hints_text", hints_text or ""),
    ):
        for code_block, language in extract_markdown_code_blocks(raw_text):
            candidate = extract_chartjs_config_object(code_block)
            if not candidate:
                continue

            lowered = candidate.lower()
            score = len(candidate)
            if source_name == "problem_statement":
                score += 300
            if "datasets" in lowered:
                score += 200
            if "options" in lowered:
                score += 150
            if "responsive" in lowered:
                score += 80
            if "legend" in lowered:
                score += 60
            if "title" in lowered:
                score += 60
            if language.strip().lower() in {"", "js", "javascript", "json"}:
                score += 20

            if score > best_score:
                best_score = score
                best_code = candidate.strip()

    return best_code


def build_chartjs_runtime_meta(problem_statement: str, hints_text: str, original_image_info: dict) -> dict:
    full_text = "\n".join([
        str(problem_statement or ""),
        str(hints_text or ""),
    ])
    lowered = full_text.lower()

    base_width = clamp_int(original_image_info.get("width") or 1200, 320, 1600)
    base_height = clamp_int(original_image_info.get("height") or 800, 160, 1400)
    orig_width = clamp_int(base_width * 2, 640, 3200)
    orig_height = clamp_int(base_height * 2, 320, 2800)

    duplicate_render = (
        any(token in lowered for token in ("rendered twice", "legend and title again", "title and legend are twice"))
        or (
            "legend" in lowered
            and "title" in lowered
            and any(token in lowered for token in ("twice", "multiple", "again", "redraw"))
        )
    )
    dynamic_stress = duplicate_render or any(
        token in lowered
        for token in (
            "responsive",
            "resize",
            "redraw",
            "browser is under load",
            "browser under load",
            "timing problem",
            "rendering process takes longer",
            "update from a timer",
        )
    )

    chart_count = parse_chart_count_from_text(full_text)
    if dynamic_stress:
        replicate_count = clamp_int(chart_count or 8, 1, 24)
    else:
        replicate_count = 1

    if dynamic_stress and base_width >= 640 and base_height >= 520:
        visible_chart_count = min(4, replicate_count)
    elif dynamic_stress and base_width >= 520 and base_height >= 320:
        visible_chart_count = min(2, replicate_count)
    else:
        visible_chart_count = 1

    visible_columns = 2 if visible_chart_count >= 2 else 1
    visible_rows = max(1, (visible_chart_count + visible_columns - 1) // visible_columns)
    page_padding = 12 if orig_width < 520 else 16
    card_gap = 14 if visible_chart_count >= 2 else 0

    available_width = max(200, orig_width - (page_padding * 2) - (card_gap * max(0, visible_columns - 1)))
    card_width = max(180, available_width // visible_columns)
    available_height = max(140, orig_height - (page_padding * 2) - (card_gap * max(0, visible_rows - 1)))
    per_row_height = max(140, available_height // visible_rows)
    header_height = 64 if visible_chart_count > 1 else 24
    chart_height = max(100, per_row_height - header_height)

    resize_sequence = []
    internal_resize_pulses = []
    if dynamic_stress:
        shrink_width = clamp_int(orig_width - max(24, orig_width // 9), 640, 3200)
        grow_width = clamp_int(orig_width + max(18, orig_width // 12), 640, 3200)
        grow_height = clamp_int(orig_height + max(18, orig_height // 10), 320, 2800)
        resize_sequence = [
            {"width": shrink_width, "height": orig_height, "wait_ms": 120},
            {"width": grow_width, "height": grow_height, "wait_ms": 150},
            {"width": orig_width, "height": orig_height, "wait_ms": 180},
        ]
        internal_resize_pulses = [
            {"width": clamp_int(orig_width - max(18, orig_width // 12), 640, 3200), "height": orig_height, "wait_ms": 90},
            {"width": clamp_int(orig_width + max(12, orig_width // 16), 640, 3200), "height": orig_height, "wait_ms": 120},
        ]

    return {
        "viewport_width": orig_width,
        "viewport_height": orig_height,
        "page_padding": page_padding,
        "card_gap": card_gap,
        "card_width": card_width,
        "chart_height": chart_height,
        "visible_chart_count": visible_chart_count,
        "visible_columns": visible_columns,
        "replicate_count": replicate_count,
        "dynamic_stress": dynamic_stress,
        "issue_kind": "responsive_duplicate_render" if duplicate_render else ("dynamic_stress" if dynamic_stress else "static"),
        "prefer_issue_config": bool(dynamic_stress),
        "dashboard_variants": bool(duplicate_render and visible_chart_count >= 4),
        "show_card_headers": bool(visible_chart_count > 1),
        "page_background": "#f2f2f2" if visible_chart_count > 1 else "#ffffff",
        "card_background": "#ffffff",
        "card_shadow": "0 1px 4px rgba(0, 0, 0, 0.18)" if visible_chart_count > 1 else "none",
        "wait_after_ms": 1200 if dynamic_stress else 350,
        "ready_timeout_ms": 7000 if dynamic_stress else 2500,
        "ready_flag": "__GUIREPAIR_CHARTJS_READY__",
        "resize_sequence": resize_sequence,
        "internal_resize_pulses": internal_resize_pulses,
        "original_image_path": str(original_image_info.get("path") or ""),
    }


def inject_chartjs_runtime_meta_html(html_text: str, runtime_meta: dict) -> str:
    if not html_text:
        return html_text
    if "window.__GUIREPAIR_CHARTJS_META__ =" in html_text:
        return html_text

    payload = json.dumps(runtime_meta or {}, ensure_ascii=False, sort_keys=True).replace("</", "<\\/")
    script_tag = f"<script>window.__GUIREPAIR_CHARTJS_META__ = {payload};</script>"

    head_close = re.search(r"</head>", html_text, flags=re.IGNORECASE)
    if head_close:
        return html_text[:head_close.start()] + script_tag + "\n" + html_text[head_close.start():]

    body_open = re.search(r"<body\b[^>]*>", html_text, flags=re.IGNORECASE)
    if body_open:
        return html_text[:body_open.end()] + "\n" + script_tag + html_text[body_open.end():]

    return script_tag + "\n" + html_text


def write_chartjs_runtime_meta_file(instance_repo_path: str, runtime_meta: dict, logger) -> str:
    runtime_meta_path = os.path.join(instance_repo_path, CHARTJS_RUNTIME_META_FILENAME)
    try:
        save_file(runtime_meta_path, json.dumps(runtime_meta or {}, ensure_ascii=False, indent=2, sort_keys=True))
    except Exception as e:
        logger.warning(f"Failed to write Chart.js runtime meta {runtime_meta_path}: {e}")
        return ""
    return runtime_meta_path


def load_chartjs_reproduce_code(instance_id, args, logger):
    candidates = []

    output_dir = getattr(args, "output_dir", "")
    if output_dir:
        candidates.extend([
            ("output_txt", os.path.join(output_dir, "1-1-5_reproduce_code.txt")),
            ("output_json", os.path.join(output_dir, "1-1-5_reproduce_code.json")),
        ])

    repo_root = getattr(args, "repo_path", "")
    dataset_split = getattr(args, "dataset_split", "")
    if repo_root and dataset_split and instance_id:
        instance_root = os.path.join(repo_root, dataset_split, instance_id.split("__")[0], instance_id)
        candidates.append(("bug_txt", os.path.join(instance_root, "BUG", "reproduce_code.txt")))

    for source_name, path in candidates:
        if not check_nonempty_file(path):
            continue
        try:
            if path.endswith(".json"):
                payload = json.loads(read_file(path))
                if isinstance(payload, dict) and payload:
                    first_key = next(iter(payload))
                    first_value = payload.get(first_key)
                    if isinstance(first_value, dict):
                        code_text = str(first_value.get("reproduce_code", "")).strip()
                    else:
                        code_text = ""
                else:
                    code_text = ""
            else:
                code_text = read_file(path).strip()
        except Exception as e:
            logger.warning(f"Failed to read Chart.js reproduce code from {path}: {e}")
            continue

        if code_text:
            return code_text, f"reproduce_code:{source_name}"

    return "", ""


def build_chartjs_code_html_from_reproduce_or_fallback(
    instance_id,
    instance_repo_path,
    problem_statement,
    image_file_list,
    template_html,
    args,
    logger,
):
    token_usage = make_token_usage_record(args, skipped=True)
    bug_info = load_chartjs_bug_info(instance_repo_path, logger)
    hints_text = str(bug_info.get("hints_text", "") or "")
    original_image_info = load_chartjs_original_image_info(instance_repo_path, logger)
    runtime_meta = build_chartjs_runtime_meta(problem_statement, hints_text, original_image_info)
    issue_config = pick_chartjs_issue_config(problem_statement, hints_text)

    reproduce_code, reproduce_source = load_chartjs_reproduce_code(instance_id, args, logger)
    if runtime_meta.get("prefer_issue_config") and issue_config:
        final_html = replace_chartjs_inline_script_html(
            template_html,
            issue_config,
            "javascript",
            logger,
        )
        final_html = inject_chartjs_runtime_meta_html(final_html, runtime_meta)
        return final_html, token_usage, "issue_config_harness", len(issue_config), runtime_meta

    if reproduce_code:
        raw_code = str(reproduce_code or "")
        if looks_like_html_markup(raw_code):
            html_code = strip_leading_comment_prelude(raw_code)
            final_html = sanitize_generated_html_text(html_code, logger) or html_code
            if final_html and not looks_like_html_document(final_html):
                final_html = wrap_html_fragment(final_html)
            final_html = inject_chartjs_runtime_meta_html(final_html, runtime_meta)
            return final_html, token_usage, reproduce_source, len(html_code), runtime_meta

        normalized = normalize_chartjs_inline_script(raw_code)
        final_html = replace_chartjs_inline_script_html(
            template_html,
            normalized,
            "javascript",
            logger,
        )
        final_html = inject_chartjs_runtime_meta_html(final_html, runtime_meta)
        return final_html, token_usage, reproduce_source, len(normalized), runtime_meta

    if issue_config:
        final_html = replace_chartjs_inline_script_html(
            template_html,
            issue_config,
            "javascript",
            logger,
        )
        final_html = inject_chartjs_runtime_meta_html(final_html, runtime_meta)
        return final_html, token_usage, "issue_config_fallback", len(issue_config), runtime_meta

    final_html, token_usage, source, code_len = build_code_html_locally_from_template(
        problem_statement,
        image_file_list,
        template_html,
        args,
        logger,
        replace_chartjs_inline_script_html,
    )
    final_html = inject_chartjs_runtime_meta_html(final_html, runtime_meta)
    return final_html, token_usage, source, code_len, runtime_meta


def ensure_markedjs_code_html_for_val(instance_id, instance_repo_path, problem_statement, image_file_list, args, logger):
    code_html_path = os.path.join(instance_repo_path, "code.html")
    token_usage = make_token_usage_record(args, skipped=True)
    if os.path.isfile(code_html_path):
        logger.info(f"Existing code.html found; regenerating and overwriting: {code_html_path}")

    template_path = resolve_markedjs_code_template_path(logger)
    if not template_path:
        return code_html_path, token_usage

    try:
        template_html = read_file(template_path)
    except Exception as e:
        logger.warning(f"Failed to read markedjs template: {template_path}, error={e}")
        return code_html_path, token_usage

    return write_code_html_with_llm_then_fallback(
        instance_id,
        code_html_path,
        problem_statement,
        image_file_list,
        template_path,
        template_html,
        args,
        logger,
        replace_marked_input_html,
    )


def ensure_chartjs_code_html_for_val(
    instance_id,
    instance_repo_path,
    problem_statement,
    image_file_list,
    args,
    logger,
    return_metadata: bool = False,
):
    code_html_path = os.path.join(instance_repo_path, "code.html")
    token_usage = make_token_usage_record(args, skipped=True)
    if os.path.isfile(code_html_path):
        logger.info(f"Existing code.html found; regenerating and overwriting: {code_html_path}")

    template_path = resolve_chartjs_code_template_path(logger)
    if not template_path:
        if return_metadata:
            return code_html_path, token_usage, {"source": "", "code_len": 0, "template_path": ""}
        return code_html_path, token_usage

    try:
        template_html = read_file(template_path)
    except Exception as e:
        logger.warning(f"Failed to read chartjs template: {template_path}, error={e}")
        if return_metadata:
            return code_html_path, token_usage, {"source": "", "code_len": 0, "template_path": template_path}
        return code_html_path, token_usage

    final_html, token_usage, source, code_len, runtime_meta = build_chartjs_code_html_from_reproduce_or_fallback(
        instance_id,
        instance_repo_path,
        problem_statement,
        image_file_list,
        template_html,
        args,
        logger,
    )
    save_file(code_html_path, final_html)
    runtime_meta_path = write_chartjs_runtime_meta_file(instance_repo_path, runtime_meta, logger)
    logger.info(
        f"Generated code.html for {instance_id}: {code_html_path} "
        f"(template={template_path}, code_source={source}, code_len={code_len}, "
        f"viewport={runtime_meta.get('viewport_width')}x{runtime_meta.get('viewport_height')}, "
        f"issue_kind={runtime_meta.get('issue_kind')})"
    )
    if return_metadata:
        return code_html_path, token_usage, {
            "source": source,
            "code_len": code_len,
            "template_path": template_path,
            "runtime_meta_path": runtime_meta_path,
            "runtime_meta": runtime_meta,
        }
    return code_html_path, token_usage


def norm_rel_path(p: str) -> str:
    if not p:
        return ""
    if re.match(r'^[a-zA-Z]+://', p):
        return p
    s = str(p).replace("\\", "/")
    if s.startswith("./"):
        s = s[2:]
    if s.startswith("/"):
        s = s[1:]
    return s
