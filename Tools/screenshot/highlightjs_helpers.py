#!/usr/bin/env python3
import argparse
import base64
import html
import http.server
import importlib.util
import json
import logging
import mimetypes
import os
import re
import shutil
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image


DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPRODUCE_ROOT = Path(
    os.getenv("GUIREPAIR_REPO_PATH") or (DEFAULT_PROJECT_ROOT.parent / "Reproduce_Scenario")
) / "test" / "highlightjs"

def load_build_cmd_module(project_root: Path):
    module_path = project_root / "Code" / "build_cmd.py"
    spec = importlib.util.spec_from_file_location("build_cmd", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load build_cmd.py from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass
class CommandResult:
    cmd: List[str]
    returncode: int
    stdout: str
    stderr: str


def run_command(cmd: List[str], cwd: Optional[Path] = None) -> CommandResult:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return CommandResult(
        cmd=cmd,
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
    )


def write_log(log_path: Path, result: CommandResult) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    body = [
        f"$ {' '.join(result.cmd)}",
        f"returncode={result.returncode}",
        "",
        "stdout:",
        result.stdout,
        "",
        "stderr:",
        result.stderr,
    ]
    log_path.write_text("\n".join(body), encoding="utf-8")


def build_capture_cmd(
    script_path: Path,
    repo_dir: Path,
    instance_id: str,
    out_path: Path,
    build_cmd: str,
    *,
    capture_mode: str = "",
    force_template: bool = False,
) -> List[str]:
    cmd = [
        "node",
        str(script_path),
        "--repo",
        str(repo_dir),
        "--instance-id",
        instance_id,
        "--html",
        "code.html",
        "--out",
        str(out_path),
    ]
    if capture_mode:
        cmd.extend(["--mode", capture_mode])
    if force_template:
        cmd.extend(["--force-template", "true"])
    if build_cmd:
        cmd.extend(["--build-cmd", build_cmd])
    return cmd


def find_browser_executable() -> Optional[str]:
    candidates = [
        os.getenv("GUIREPAIR_CHROME"),
        os.getenv("CHROME_PATH"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("chrome"),
        shutil.which("msedge"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    return None


class QuietHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        return


def capture_png_manual_chrome(
    repo_dir: Path,
    out_path: Path,
    log_path: Path,
    *,
    timeout_seconds: int = 20,
    viewport: Tuple[int, int] = (2400, 1600),
) -> CommandResult:
    browser = find_browser_executable()
    if not browser:
        result = CommandResult(
            cmd=["manual-chrome"],
            returncode=127,
            stdout="",
            stderr="no chrome/chromium executable found",
        )
        write_log(log_path, result)
        return result

    out_path.parent.mkdir(parents=True, exist_ok=True)
    repo_dir = repo_dir.resolve()
    server_cwd = os.getcwd()
    os.chdir(repo_dir)
    httpd = None
    proc = None
    stdout_text = ""
    stderr_text = ""
    returncode = 1
    cmd = []
    try:
        httpd = socketserver.TCPServer(("127.0.0.1", 0), QuietHTTPRequestHandler)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        page_url = f"http://127.0.0.1:{port}/code.html"
        cmd = [
            browser,
            "--headless",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-crash-reporter",
            "--remote-debugging-port=0",
            f"--window-size={viewport[0]},{viewport[1]}",
            "--virtual-time-budget=2500",
            f"--screenshot={out_path}",
            page_url,
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        deadline = time.time() + timeout_seconds
        ok = False
        while time.time() < deadline:
            if out_path.is_file() and out_path.stat().st_size > 0:
                ok = True
                break
            if proc.poll() is not None:
                break
            time.sleep(0.5)

        if proc.poll() is None:
            proc.kill()
        try:
            stdout_text, stderr_text = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.terminate()
            stdout_text, stderr_text = proc.communicate(timeout=5)

        if ok and out_path.is_file() and out_path.stat().st_size > 0:
            returncode = 0
        else:
            returncode = proc.returncode if proc.returncode is not None else 1
            if not stderr_text:
                stderr_text = f"screenshot file missing after {timeout_seconds}s"
    finally:
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass
        os.chdir(server_cwd)

    result = CommandResult(
        cmd=cmd or ["manual-chrome"],
        returncode=returncode,
        stdout=stdout_text or "",
        stderr=stderr_text or "",
    )
    write_log(log_path, result)
    return result

HIGHLIGHTJS_TEMPLATE2_INSTANCE_IDS = frozenset(
    {
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
)

HIGHLIGHTJS_TEMPLATE1_INSTANCE_IDS = frozenset(
    {
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
)


def resolve_template_candidates(instance_id: str) -> list[Path]:
    template_root = Path(__file__).resolve().parent / "code_template" / "highlightjs"
    preferred_name = "template_highlightjs2.html" if instance_id in HIGHLIGHTJS_TEMPLATE2_INSTANCE_IDS else "template_highlightjs1.html"
    preferred_path = template_root / preferred_name
    fallback_path = template_root / "template_highlightjs1.html"
    if preferred_path == fallback_path:
        return [preferred_path]
    return [preferred_path, fallback_path]

def render_template_batch_summary_markdown(records: List[Dict], summary_path: Path) -> None:
    lines = [
        "# Highlight.js Strict Batch Summary",
        "",
        "| Instance | Baseline | Patch1 | Notes |",
        "| --- | --- | --- | --- |",
    ]
    for record in records:
        note_bits = []
        if record.get("baseline_error"):
            note_bits.append(f"baseline: {record['baseline_error']}")
        if record.get("patch_apply_error"):
            note_bits.append(f"apply: {record['patch_apply_error']}")
        if record.get("patch1_error"):
            note_bits.append(f"patch1: {record['patch1_error']}")
        notes = " ; ".join(note_bits) if note_bits else ""
        lines.append(
            f"| {record['instance_id']} | {record['baseline_status']} | {record['patch1_status']} | {notes} |"
        )
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def template_batch_main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Batch rerun highlight.js baseline and patch1 with strict screenshot capture.")
    parser.add_argument(
        "--project-root",
        default=str(DEFAULT_PROJECT_ROOT),
        help="Workflow root that contains Code/, Result/, and Tools/.",
    )
    parser.add_argument(
        "--result-root",
        default="Result/trace/o3/result/test/highlightjs",
        help="Relative path from project root to the result directory that contains patch_diffs.",
    )
    parser.add_argument(
        "--reproduce-root",
        default=str(DEFAULT_REPRODUCE_ROOT),
        help="Path to the reproduce scenario directory. Defaults to the shared sibling Reproduce_Scenario.",
    )
    parser.add_argument(
        "--output-root",
        default="Tools/screenshot/generated/highlightjs_template_batch",
        help="Relative path from project root where rerun outputs should be stored.",
    )
    parser.add_argument(
        "--skip-instance",
        action="append",
        default=[],
        help="Instance ids to skip. Can be passed multiple times.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional cap on processed instances after skips. 0 means no cap.",
    )
    parser.add_argument(
        "--capture-mode",
        default="manual-chrome",
        help="manual-chrome or a mode understood by highlightjs_capture_from_bug_info.cjs",
    )
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).resolve()
    result_root = (project_root / args.result_root).resolve()
    reproduce_root = (project_root / args.reproduce_root).resolve()
    output_root = (project_root / args.output_root).resolve()
    screenshot_script = Path(__file__).resolve().parent / "highlightjs_capture_from_bug_info.cjs"

    if not result_root.is_dir():
        raise RuntimeError(f"result_root does not exist: {result_root}")
    if not reproduce_root.is_dir():
        raise RuntimeError(f"reproduce_root does not exist: {reproduce_root}")
    if not screenshot_script.is_file():
        raise RuntimeError(f"screenshot script not found: {screenshot_script}")

    build_cmd_module = load_build_cmd_module(project_root)
    skip_instances = set(args.skip_instance or [])

    output_root.mkdir(parents=True, exist_ok=True)
    records: List[Dict] = []

    instance_dirs = sorted(p for p in result_root.iterdir() if p.is_dir())
    processed = 0

    for instance_dir in instance_dirs:
        instance_id = instance_dir.name
        if not instance_id.startswith("highlightjs__"):
            continue
        if instance_id in skip_instances:
            continue
        if args.limit and processed >= args.limit:
            break

        processed += 1
        record: Dict = {
            "instance_id": instance_id,
            "baseline_status": "pending",
            "patch1_status": "pending",
        }
        records.append(record)

        source_repo = reproduce_root / instance_id / "REPO" / "highlight.js"
        patch1_diff = instance_dir / "patch_diffs" / "changes_1.diff"
        instance_out_root = output_root / instance_id
        work_repo = instance_out_root / "repo"
        capture_out = instance_out_root / "out"

        if instance_out_root.exists():
            shutil.rmtree(instance_out_root)
        capture_out.mkdir(parents=True, exist_ok=True)
        print(f"[{processed}] {instance_id}")

        if not source_repo.is_dir():
            record["baseline_status"] = "skipped"
            record["patch1_status"] = "skipped"
            record["baseline_error"] = f"missing source repo: {source_repo}"
            print("  skip: missing source repo")
            continue

        shutil.copytree(source_repo, work_repo)

        baseline_png = capture_out / "baseline.png"
        baseline_build_cmd = build_cmd_module.make_build_cmd(str(work_repo))
        if args.capture_mode == "manual-chrome":
            build_result = run_command(["/bin/zsh", "-lc", baseline_build_cmd], cwd=work_repo)
            write_log(capture_out / "baseline.build.log", build_result)
            if build_result.returncode != 0:
                record["baseline_status"] = "failed"
                record["patch1_status"] = "skipped"
                record["baseline_error"] = f"build failed rc={build_result.returncode}"
                print(f"  baseline build failed rc={build_result.returncode}")
                continue
            baseline_result = capture_png_manual_chrome(work_repo, baseline_png, capture_out / "baseline.log")
        else:
            baseline_cmd = build_capture_cmd(
                screenshot_script,
                work_repo,
                instance_id,
                baseline_png,
                baseline_build_cmd,
                capture_mode=args.capture_mode,
                force_template=True,
            )
            baseline_result = run_command(baseline_cmd, cwd=project_root)
            write_log(capture_out / "baseline.log", baseline_result)

        if baseline_result.returncode != 0 or not baseline_png.is_file():
            record["baseline_status"] = "failed"
            record["patch1_status"] = "skipped"
            record["baseline_error"] = f"capture failed rc={baseline_result.returncode}"
            print(f"  baseline failed rc={baseline_result.returncode}")
            continue

        record["baseline_status"] = "ok"
        print("  baseline ok")

        if not patch1_diff.is_file():
            record["patch1_status"] = "skipped"
            record["patch_apply_error"] = f"missing patch diff: {patch1_diff}"
            print("  patch1 skipped: missing diff")
            continue

        patch_apply_result = run_command(["git", "apply", str(patch1_diff)], cwd=work_repo)
        write_log(capture_out / "patch1_apply.log", patch_apply_result)
        if patch_apply_result.returncode != 0:
            record["patch1_status"] = "skipped"
            record["patch_apply_error"] = f"git apply failed rc={patch_apply_result.returncode}"
            print(f"  patch1 skipped: git apply failed rc={patch_apply_result.returncode}")
            continue

        patch1_png = capture_out / "patch1.png"
        patch1_build_cmd = build_cmd_module.make_build_cmd(str(work_repo))
        if args.capture_mode == "manual-chrome":
            build_result = run_command(["/bin/zsh", "-lc", patch1_build_cmd], cwd=work_repo)
            write_log(capture_out / "patch1.build.log", build_result)
            if build_result.returncode != 0:
                record["patch1_status"] = "failed"
                record["patch1_error"] = f"build failed rc={build_result.returncode}"
                print(f"  patch1 build failed rc={build_result.returncode}")
                continue
            patch1_result = capture_png_manual_chrome(work_repo, patch1_png, capture_out / "patch1.log")
        else:
            patch1_cmd = build_capture_cmd(
                screenshot_script,
                work_repo,
                instance_id,
                patch1_png,
                patch1_build_cmd,
                capture_mode=args.capture_mode,
                force_template=False,
            )
            patch1_result = run_command(patch1_cmd, cwd=project_root)
            write_log(capture_out / "patch1.log", patch1_result)

        if patch1_result.returncode != 0 or not patch1_png.is_file():
            record["patch1_status"] = "failed"
            record["patch1_error"] = f"capture failed rc={patch1_result.returncode}"
            print(f"  patch1 failed rc={patch1_result.returncode}")
            continue

        record["patch1_status"] = "ok"
        print("  patch1 ok")

    summary = {
        "project_root": str(project_root),
        "result_root": str(result_root),
        "reproduce_root": str(reproduce_root),
        "output_root": str(output_root),
        "processed_instances": processed,
        "records": records,
        "counts": {
            "baseline_ok": sum(1 for r in records if r["baseline_status"] == "ok"),
            "baseline_failed": sum(1 for r in records if r["baseline_status"] == "failed"),
            "patch1_ok": sum(1 for r in records if r["patch1_status"] == "ok"),
            "patch1_failed": sum(1 for r in records if r["patch1_status"] == "failed"),
            "patch1_skipped": sum(1 for r in records if r["patch1_status"] == "skipped"),
        },
    }

    (output_root / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    render_template_batch_summary_markdown(records, output_root / "summary.md")
    print(json.dumps(summary["counts"], ensure_ascii=False))
    print(output_root / "summary.json")
    return 0

FENCED_BLOCK_RE = re.compile(
    r"(?ms)(?P<fence>`{3,})(?P<lang>[^\n`]*)\r?\n(?P<code>.*?)(?:\r?\n(?P=fence))"
)
CODE_BLOCK_RE = re.compile(
    r"(?P<prefix><pre\b[^>]*>\s*<code\b[^>]*>)(?P<body>[\s\S]*?)(?P<suffix></code>\s*</pre>)",
    flags=re.IGNORECASE,
)
CLASS_RE = re.compile(r'(\bclass\s*=\s*)(["\'])([^"\']*)(\2)', flags=re.IGNORECASE)
LANG_HEADER_RE = re.compile(r"^\(([^)]+)\)")
WHICH_LANG_RE = re.compile(
    r"\*\*Which language seems to have the issue\?\*\*\s*[\r\n]+([^\r\n]+)",
    flags=re.IGNORECASE,
)
LANGUAGE_ALIASES = {
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

def load_validation_tools_module(project_root: Path):
    module_path = project_root / "Tools" / "validation" / "validation_tools.py"
    project_root_str = str(project_root)
    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)
    spec = importlib.util.spec_from_file_location("validation_tools", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load validation_tools.py from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def resolve_template_path(instance_id: str) -> Path:
    candidates = resolve_template_candidates(instance_id)
    if not candidates:
        raise RuntimeError(f"no template candidates for {instance_id}")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError(f"template missing for {instance_id}: {candidates}")


def normalize_language_for_code_class(language: str) -> str:
    lang = (language or "").strip().lower()
    if lang.startswith("language-"):
        lang = lang[len("language-") :]
    lang = re.sub(r"[^a-z0-9_+\-]", "", lang)
    return lang or "plain"


def normalize_highlightjs_language(language: str) -> str:
    lang = normalize_language_for_code_class(language)
    if lang == "plain":
        lang = ""
    return LANGUAGE_ALIASES.get(lang, lang)


def extract_markdown_code_blocks(text: str, depth: int = 0) -> List[Tuple[str, str]]:
    if not text or depth > 2:
        return []

    blocks: List[Tuple[str, str]] = []
    seen = set()
    for match in FENCED_BLOCK_RE.finditer(text):
        lang = (match.group("lang") or "").strip()
        code = (match.group("code") or "").strip("\n\r")
        if code:
            key = (code, lang)
            if key not in seen:
                seen.add(key)
                blocks.append(key)
            if "```" in code:
                for nested_code, nested_lang in extract_markdown_code_blocks(code, depth + 1):
                    nested_key = (nested_code, nested_lang)
                    if nested_key in seen:
                        continue
                    seen.add(nested_key)
                    blocks.append(nested_key)
    return blocks


def extract_language_candidates(text: str) -> List[str]:
    if not text:
        return []

    candidates: List[str] = []
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


def rewrite_code_open_tag_language(code_open_tag: str, language: str) -> str:
    lang = normalize_language_for_code_class(language)
    m = CLASS_RE.search(code_open_tag or "")
    if not m:
        return re.sub(r">\s*$", f' class="language-{lang}">', code_open_tag, count=1)

    classes = [c for c in re.split(r"\s+", m.group(3).strip()) if c]
    classes = [c for c in classes if not c.startswith("language-")]
    classes.append(f"language-{lang}")
    new_class = " ".join(classes)
    return (
        code_open_tag[: m.start()]
        + f"{m.group(1)}{m.group(2)}{new_class}{m.group(2)}"
        + code_open_tag[m.end() :]
    )


def replace_code_block_html(base_html: str, code_text: str, language: str) -> str:
    m = CODE_BLOCK_RE.search(base_html)
    if not m:
        raise RuntimeError("template has no <pre><code> block")

    new_prefix = m.group("prefix")
    if language:
        new_prefix = re.sub(
            r"<code\b[^>]*>",
            lambda mm: rewrite_code_open_tag_language(mm.group(0), language),
            new_prefix,
            count=1,
            flags=re.IGNORECASE,
        )

    escaped_code = html.escape(code_text or "", quote=False)
    return base_html[: m.start()] + new_prefix + escaped_code + m.group("suffix") + base_html[m.end() :]


def infer_language_from_problem_statement(problem_statement: str) -> str:
    text = str(problem_statement or "").strip()
    m = LANG_HEADER_RE.match(text)
    if m:
        chunk = m.group(1)
        parts = [part.strip() for part in re.split(r"[,/]", chunk) if part.strip()]
        if parts:
            return normalize_highlightjs_language(parts[0])

    m = WHICH_LANG_RE.search(text)
    if m:
        for candidate in extract_language_candidates(m.group(1).strip()):
            if candidate:
                return candidate

    return ""


def choose_problem_statement_code(problem_statement: str) -> Tuple[str, str, int]:
    blocks = extract_markdown_code_blocks(problem_statement or "")
    if not blocks:
        return "", "", -1

    inferred_language = infer_language_from_problem_statement(problem_statement)

    for idx, (code, lang) in enumerate(blocks):
        if "<span" in code or "hljs-" in code:
            continue
        if "```" in code:
            continue

        normalized = normalize_highlightjs_language(lang)
        if not normalized:
            normalized = inferred_language
        return code.strip("\n\r"), normalized, idx

    return "", inferred_language, -1


def flatten_image_asset_values(value) -> List[str]:
    values: List[str] = []
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            values.append(stripped)
    elif isinstance(value, list):
        for item in value:
            values.extend(flatten_image_asset_values(item))
    elif isinstance(value, dict):
        for item in value.values():
            values.extend(flatten_image_asset_values(item))
    return values


def parse_problem_statement_image_sources(raw_image_assets) -> List[str]:
    payload = raw_image_assets
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return flatten_image_asset_values(payload)

    candidates: List[str] = []
    if isinstance(payload, dict):
        if "problem_statement" in payload:
            candidates.extend(flatten_image_asset_values(payload.get("problem_statement")))
        else:
            candidates.extend(flatten_image_asset_values(payload))
    else:
        candidates.extend(flatten_image_asset_values(payload))

    deduped: List[str] = []
    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    return deduped


def find_local_problem_statement_images(instance_root: Path) -> List[str]:
    image_dir = instance_root / "IMAGE"
    if not image_dir.is_dir():
        return []

    candidates = []
    for path in sorted(image_dir.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}:
            continue
        candidates.append(str(path.resolve()))
    return candidates


def infer_image_type_from_source(source: str, content_type: str = "") -> str:
    if content_type:
        normalized = content_type.split(";", 1)[0].strip().lower()
        if normalized.startswith("image/"):
            subtype = normalized.split("/", 1)[1]
            if subtype == "jpeg":
                return "jpeg"
            if subtype:
                return subtype

    guess, _ = mimetypes.guess_type(source)
    if guess and guess.startswith("image/"):
        subtype = guess.split("/", 1)[1]
        if subtype:
            return subtype

    suffix = Path(urllib.parse.urlparse(source).path).suffix.lower().lstrip(".")
    if suffix in {"png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"}:
        return "jpeg" if suffix == "jpg" else suffix
    return "png"


def build_image_payload_from_source(source: str, instance_root: Path, llm_args, timeout_seconds: int, logger) -> Dict:
    parsed = urllib.parse.urlparse(source)
    if parsed.scheme in {"http", "https"}:
        request = urllib.request.Request(
            source,
            headers={"User-Agent": "VisualRepair/1.0"},
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            data = response.read()
            content_type = response.headers.get("Content-Type", "")
        image_type = infer_image_type_from_source(source, content_type)
    else:
        local_path = Path(source)
        if not local_path.is_absolute():
            local_path = (instance_root / source).resolve()
        data = local_path.read_bytes()
        image_type = infer_image_type_from_source(str(local_path))

    encoded = base64.b64encode(data).decode("utf-8")
    if "claude" in str(getattr(llm_args, "base_model", "")).lower():
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": f"image/{image_type}",
                "data": encoded,
            },
        }

    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:image/{image_type};base64,{encoded}",
        },
    }


def build_problem_statement_image_payloads(
    image_sources: List[str],
    instance_root: Path,
    llm_args,
    timeout_seconds: int,
    logger,
) -> Tuple[List[Dict], List[str]]:
    payloads: List[Dict] = []
    errors: List[str] = []
    for source in image_sources:
        try:
            payloads.append(
                build_image_payload_from_source(source, instance_root, llm_args, timeout_seconds, logger)
            )
        except FileNotFoundError as exc:
            errors.append(f"{source}: {exc}")
        except urllib.error.URLError as exc:
            errors.append(f"{source}: {exc}")
        except Exception as exc:
            errors.append(f"{source}: {exc}")
    return payloads, errors


def write_instance_specific_code_html(
    instance_id: str,
    bug_info_path: Path,
    repo_dir: Path,
    *,
    use_llm_fallback: bool,
    validation_tools_module,
    llm_args,
    llm_timeout_seconds: int,
    logger,
) -> Dict:
    data = json.loads(bug_info_path.read_text(encoding="utf-8", errors="replace"))
    problem_statement = data.get("problem_statement", "")
    code_text, language, block_index = choose_problem_statement_code(problem_statement)
    source = "problem_statement_deterministic" if code_text else "none"
    token_usage = {
        "base_model": getattr(llm_args, "base_model", "") if llm_args else "",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "skipped": True,
    }
    image_sources = parse_problem_statement_image_sources(data.get("image_assets"))
    local_image_sources = find_local_problem_statement_images(bug_info_path.parent)
    if local_image_sources:
        image_sources = local_image_sources
    image_payload_errors: List[str] = []

    if not code_text and use_llm_fallback and validation_tools_module is not None and llm_args is not None:
        payloads, image_payload_errors = build_problem_statement_image_payloads(
            image_sources,
            bug_info_path.parent,
            llm_args,
            llm_timeout_seconds,
            logger,
        )
        if payloads:
            extracted_code, extracted_language, token_usage = validation_tools_module.extract_code_from_images_via_llm(
                problem_statement,
                payloads,
                llm_args,
                logger,
            )
            if not extracted_code and len(payloads) > 1:
                for payload in payloads:
                    extracted_code, extracted_language, token_usage = validation_tools_module.extract_code_from_images_via_llm(
                        problem_statement,
                        [payload],
                        llm_args,
                        logger,
                    )
                    if extracted_code:
                        break
            if extracted_code:
                code_text = extracted_code
                language = normalize_highlightjs_language(extracted_language) or infer_language_from_problem_statement(problem_statement)
                block_index = -1
                source = "image_code_llm"
            else:
                source = "image_code_llm_attempted"

    if not code_text:
        reason = "no usable markdown code block in problem_statement"
        if use_llm_fallback:
            reason = "no usable markdown code block and llm image extraction returned no code"
        return {
            "status": "skipped",
            "reason": reason,
            "language": language,
            "block_index": block_index,
            "code_len": 0,
            "source": source,
            "token_usage": token_usage,
            "image_sources": image_sources,
            "image_payload_errors": image_payload_errors,
        }

    template_path = resolve_template_path(instance_id)
    template_html = template_path.read_text(encoding="utf-8", errors="replace")
    final_html = replace_code_block_html(template_html, code_text, language)
    code_html_path = repo_dir / "code.html"
    code_html_path.write_text(final_html, encoding="utf-8")
    return {
        "status": "ok",
        "reason": "",
        "language": language or "plain",
        "block_index": block_index,
        "code_len": len(code_text),
        "template_path": str(template_path),
        "code_html_path": str(code_html_path),
        "source": source,
        "token_usage": token_usage,
        "image_sources": image_sources,
        "image_payload_errors": image_payload_errors,
    }


def read_diagnostics(diag_path: Path) -> Dict:
    if not diag_path.is_file():
        return {}
    return json.loads(diag_path.read_text(encoding="utf-8", errors="replace"))


def white_ratio(image_path: Path) -> float:
    img = Image.open(image_path).convert("RGB")
    total = img.size[0] * img.size[1]
    white = sum(1 for r, g, b in img.getdata() if r > 245 and g > 245 and b > 245)
    return white / total


def render_instance_specific_summary_markdown(records: List[Dict], summary_path: Path) -> None:
    lines = [
        "# Highlight.js Instance-Specific Strict Batch Summary",
        "",
        "| Instance | Code HTML | Baseline | Patch1 | Lang | Span0 | Span1 | White0 | White1 | Notes |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for record in records:
        notes = []
        for key in ("code_html_reason", "baseline_error", "patch_apply_error", "patch1_error"):
            value = record.get(key)
            if value:
                notes.append(value)
        lines.append(
            "| {instance_id} | {code_html_status} | {baseline_status} | {patch1_status} | {language} | {span0} | {span1} | {white0} | {white1} | {notes} |".format(
                instance_id=record["instance_id"],
                code_html_status=record["code_html_status"],
                baseline_status=record["baseline_status"],
                patch1_status=record["patch1_status"],
                language=record.get("language", ""),
                span0=record.get("baseline_span_count", ""),
                span1=record.get("patch1_span_count", ""),
                white0=record.get("baseline_white_ratio", ""),
                white1=record.get("patch1_white_ratio", ""),
                notes=" ; ".join(notes),
            )
        )
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def instance_specific_batch_main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Batch rerun highlight.js screenshots using instance-specific code.html built from problem_statement code blocks.")
    parser.add_argument("--project-root", default=str(DEFAULT_PROJECT_ROOT))
    parser.add_argument("--result-root", default="Result/trace/o3/result/test/highlightjs")
    parser.add_argument("--reproduce-root", default=str(DEFAULT_REPRODUCE_ROOT))
    parser.add_argument("--output-root", default="Tools/screenshot/generated/highlightjs_instance_specific_batch")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--instance", action="append", default=[])
    parser.add_argument("--skip-instance", action="append", default=[])
    parser.add_argument("--use-llm-fallback", action="store_true")
    parser.add_argument("--base-model", default=os.getenv("OPENAI_MODEL") or "o3")
    parser.add_argument("--openai-api-key", default=os.getenv("OPENAI_API_KEY") or "")
    parser.add_argument("--openai-base-url", default=os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE") or "")
    parser.add_argument("--wait-time-after-api-request", type=int, default=0)
    parser.add_argument("--llm-image-timeout-seconds", type=int, default=60)
    parser.add_argument("--capture-mode", default=os.getenv("GUIREPAIR_HIGHLIGHTJS_CAPTURE_MODE") or "manual-chrome")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    project_root = Path(args.project_root).resolve()
    result_root = (project_root / args.result_root).resolve()
    reproduce_root = (project_root / args.reproduce_root).resolve()
    output_root = (project_root / args.output_root).resolve()
    screenshot_script = Path(__file__).resolve().parent / "highlightjs_capture_from_bug_info.cjs"
    build_cmd_module = load_build_cmd_module(project_root)
    validation_tools_module = load_validation_tools_module(project_root) if args.use_llm_fallback else None
    logger = logging.getLogger("highlightjs_batch")
    only_instances = set(args.instance or [])
    skipped_instances = set(args.skip_instance or [])

    output_root.mkdir(parents=True, exist_ok=True)
    records: List[Dict] = []
    processed = 0

    for instance_dir in sorted(result_root.iterdir()):
        instance_id = instance_dir.name
        if not instance_dir.is_dir() or not instance_id.startswith("highlightjs__"):
            continue
        if only_instances and instance_id not in only_instances:
            continue
        if instance_id in skipped_instances:
            continue
        if args.limit and processed >= args.limit:
            break

        processed += 1
        print(f"[{processed}] {instance_id}")

        record: Dict = {
            "instance_id": instance_id,
            "code_html_status": "pending",
            "baseline_status": "pending",
            "patch1_status": "pending",
        }
        records.append(record)

        source_repo = reproduce_root / instance_id / "REPO" / "highlight.js"
        bug_info_path = reproduce_root / instance_id / "bug_info.json"
        patch1_diff = instance_dir / "patch_diffs" / "changes_1.diff"
        instance_out_root = output_root / instance_id
        work_repo = instance_out_root / "repo"
        capture_out = instance_out_root / "out"

        if instance_out_root.exists():
            shutil.rmtree(instance_out_root)
        capture_out.mkdir(parents=True, exist_ok=True)

        if not source_repo.is_dir() or not bug_info_path.is_file():
            record["code_html_status"] = "skipped"
            record["baseline_status"] = "skipped"
            record["patch1_status"] = "skipped"
            record["code_html_reason"] = "missing source repo or bug_info.json"
            print("  skip: missing source repo or bug_info")
            continue

        shutil.copytree(source_repo, work_repo)

        code_html_meta = write_instance_specific_code_html(
            instance_id,
            bug_info_path,
            work_repo,
            use_llm_fallback=args.use_llm_fallback,
            validation_tools_module=validation_tools_module,
            llm_args=args,
            llm_timeout_seconds=args.llm_image_timeout_seconds,
            logger=logger,
        )
        (instance_out_root / "code_html_meta.json").write_text(
            json.dumps(code_html_meta, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        record["code_html_status"] = code_html_meta["status"]
        record["code_html_reason"] = code_html_meta.get("reason", "")
        record["language"] = code_html_meta.get("language", "")
        record["code_block_index"] = code_html_meta.get("block_index", -1)
        record["code_len"] = code_html_meta.get("code_len", 0)
        record["template_path"] = code_html_meta.get("template_path", "")
        record["code_html_source"] = code_html_meta.get("source", "")
        token_usage = code_html_meta.get("token_usage") or {}
        record["code_html_token_total"] = token_usage.get("total_tokens", 0)
        record["code_html_token_prompt"] = token_usage.get("prompt_tokens", 0)
        record["code_html_token_completion"] = token_usage.get("completion_tokens", 0)
        record["problem_statement_image_count"] = len(code_html_meta.get("image_sources") or [])
        if code_html_meta.get("image_payload_errors"):
            record["code_html_reason"] = (
                (record["code_html_reason"] + " | ") if record["code_html_reason"] else ""
            ) + "; ".join(code_html_meta["image_payload_errors"])
        if code_html_meta["status"] != "ok":
            record["baseline_status"] = "skipped"
            record["patch1_status"] = "skipped"
            print(f"  code_html skipped: {record['code_html_reason']}")
            continue
        print(
            "  code_html ok "
            f"source={record['code_html_source']} "
            f"lang={record['language']} "
            f"block={record['code_block_index']} "
            f"tokens={record['code_html_token_total']}"
        )

        baseline_png = capture_out / "baseline.png"
        baseline_build_cmd = build_cmd_module.make_build_cmd(str(work_repo))
        if args.capture_mode == "manual-chrome":
            build_result = run_command(["/bin/zsh", "-lc", baseline_build_cmd], cwd=work_repo)
            write_log(capture_out / "baseline.build.log", build_result)
            if build_result.returncode != 0:
                record["baseline_status"] = "failed"
                record["patch1_status"] = "skipped"
                record["baseline_error"] = f"build failed rc={build_result.returncode}"
                print(f"  baseline build failed rc={build_result.returncode}")
                continue
            baseline_result = capture_png_manual_chrome(work_repo, baseline_png, capture_out / "baseline.log")
        else:
            baseline_result = run_command(
                build_capture_cmd(
                    screenshot_script,
                    work_repo,
                    instance_id,
                    baseline_png,
                    baseline_build_cmd,
                    args.capture_mode,
                ),
                cwd=project_root,
            )
            write_log(capture_out / "baseline.log", baseline_result)
        if baseline_result.returncode != 0 or not baseline_png.is_file():
            record["baseline_status"] = "failed"
            record["patch1_status"] = "skipped"
            record["baseline_error"] = f"capture failed rc={baseline_result.returncode}"
            print(f"  baseline failed rc={baseline_result.returncode}")
            continue

        record["baseline_status"] = "ok"
        baseline_diag = read_diagnostics(capture_out / "baseline.png.diagnostics.json")
        record["baseline_span_count"] = baseline_diag.get("pageState", {}).get("totalSpanCount") if baseline_diag else None
        record["baseline_white_ratio"] = round(white_ratio(baseline_png), 4)
        print(f"  baseline ok spans={record['baseline_span_count']} white={record['baseline_white_ratio']}")

        if not patch1_diff.is_file():
            record["patch1_status"] = "skipped"
            record["patch_apply_error"] = "missing changes_1.diff"
            print("  patch1 skipped: missing diff")
            continue

        patch_apply_result = run_command(["git", "apply", str(patch1_diff)], cwd=work_repo)
        write_log(capture_out / "patch1_apply.log", patch_apply_result)
        if patch_apply_result.returncode != 0:
            record["patch1_status"] = "skipped"
            record["patch_apply_error"] = f"git apply failed rc={patch_apply_result.returncode}"
            print(f"  patch1 skipped: git apply failed rc={patch_apply_result.returncode}")
            continue

        patch1_png = capture_out / "patch1.png"
        patch1_build_cmd = build_cmd_module.make_build_cmd(str(work_repo))
        if args.capture_mode == "manual-chrome":
            build_result = run_command(["/bin/zsh", "-lc", patch1_build_cmd], cwd=work_repo)
            write_log(capture_out / "patch1.build.log", build_result)
            if build_result.returncode != 0:
                record["patch1_status"] = "failed"
                record["patch1_error"] = f"build failed rc={build_result.returncode}"
                print(f"  patch1 build failed rc={build_result.returncode}")
                continue
            patch1_result = capture_png_manual_chrome(work_repo, patch1_png, capture_out / "patch1.log")
        else:
            patch1_result = run_command(
                build_capture_cmd(
                    screenshot_script,
                    work_repo,
                    instance_id,
                    patch1_png,
                    patch1_build_cmd,
                    args.capture_mode,
                ),
                cwd=project_root,
            )
            write_log(capture_out / "patch1.log", patch1_result)
        if patch1_result.returncode != 0 or not patch1_png.is_file():
            record["patch1_status"] = "failed"
            record["patch1_error"] = f"capture failed rc={patch1_result.returncode}"
            print(f"  patch1 failed rc={patch1_result.returncode}")
            continue

        record["patch1_status"] = "ok"
        patch1_diag = read_diagnostics(capture_out / "patch1.png.diagnostics.json")
        record["patch1_span_count"] = patch1_diag.get("pageState", {}).get("totalSpanCount") if patch1_diag else None
        record["patch1_white_ratio"] = round(white_ratio(patch1_png), 4)
        print(f"  patch1 ok spans={record['patch1_span_count']} white={record['patch1_white_ratio']}")

    summary = {
        "project_root": str(project_root),
        "result_root": str(result_root),
        "reproduce_root": str(reproduce_root),
        "output_root": str(output_root),
        "processed_instances": processed,
        "records": records,
        "counts": {
            "code_html_ok": sum(1 for r in records if r["code_html_status"] == "ok"),
            "code_html_skipped": sum(1 for r in records if r["code_html_status"] == "skipped"),
            "code_html_from_problem_statement": sum(1 for r in records if r.get("code_html_source") == "problem_statement_deterministic"),
            "code_html_from_llm": sum(1 for r in records if r.get("code_html_source") == "image_code_llm"),
            "baseline_ok": sum(1 for r in records if r["baseline_status"] == "ok"),
            "baseline_failed": sum(1 for r in records if r["baseline_status"] == "failed"),
            "patch1_ok": sum(1 for r in records if r["patch1_status"] == "ok"),
            "patch1_failed": sum(1 for r in records if r["patch1_status"] == "failed"),
            "patch1_skipped": sum(1 for r in records if r["patch1_status"] == "skipped"),
            "baseline_with_zero_spans": sum(1 for r in records if r.get("baseline_span_count") == 0),
        },
    }

    (output_root / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    render_instance_specific_summary_markdown(records, output_root / "summary.md")
    print(json.dumps(summary["counts"], ensure_ascii=False))
    print(output_root / "summary.json")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    command = argv[0] if argv else 'instance-batch'
    sub_argv = argv[1:] if argv else []

    if command in {'template-batch', 'template'}:
        return template_batch_main(sub_argv)
    if command in {'instance-batch', 'instance', 'instance-specific-batch'}:
        return instance_specific_batch_main(sub_argv)

    print(
        'Usage: highlightjs_helpers.py [template-batch|instance-batch] [args...]',
        file=sys.stderr,
    )
    return 2


if __name__ == '__main__':
    sys.exit(main())
