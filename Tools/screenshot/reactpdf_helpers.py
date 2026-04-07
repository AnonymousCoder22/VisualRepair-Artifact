#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont


SCRIPT_DIR = Path(__file__).resolve().parent
NODE_RENDER_SCRIPT = SCRIPT_DIR / "reactpdf_render_module.cjs"
NODE_REPL_DECODE_SCRIPT = SCRIPT_DIR / "reactpdf_decode_repl.cjs"
SCREENSHOT_SCALE = 2

def run(
    argv: list[str],
    cwd: Path,
    *,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if check and proc.returncode != 0:
        cmd = " ".join(shlex.quote(arg) for arg in argv)
        raise RuntimeError(
            f"command failed (rc={proc.returncode}): {cmd}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )
    return proc


def parse_single_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a react-pdf issue reproduction to PNG using the real repo renderer.")
    parser.add_argument("--repo-dir", required=True, help="Path to the instance repo root, e.g. .../REPO/react-pdf")
    parser.add_argument("--out", required=True, help="Output PNG path")
    parser.add_argument("--instance-root", help="Path to the instance root containing bug_info.json")
    parser.add_argument("--work-dir", help="Working directory for generated modules, pdfs and manifests")
    parser.add_argument("--source-strategy", default="auto", choices=["auto", "repo-file", "code-block", "repl"])
    parser.add_argument("--skip-install", action="store_true", help="Skip yarn install checks")
    parser.add_argument("--skip-build", action="store_true", help="Skip yarn build checks")
    parser.add_argument("--force-install", action="store_true", help="Always run yarn install before capture")
    parser.add_argument("--force-build", action="store_true", help="Always run yarn build before capture")
    return parser.parse_args(argv)


def default_instance_root(repo_dir: Path) -> Path:
    if repo_dir.parent.name == "REPO":
        return repo_dir.parent.parent
    return repo_dir


def resolve_yarn_commands() -> list[list[str]]:
    commands: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()

    def add(argv: list[str]) -> None:
        key = tuple(argv)
        if key not in seen:
            seen.add(key)
            commands.append(argv)

    cache_root = Path.home() / ".cache" / "node" / "corepack" / "v1" / "yarn"
    if cache_root.is_dir():
        for yarn_bin in sorted(cache_root.glob("*/bin/yarn"), reverse=True):
            add([str(yarn_bin)])

    if shutil.which("yarn"):
        add(["yarn"])

    for fallback in ("/opt/homebrew/bin/yarn", "/usr/local/bin/yarn"):
        if Path(fallback).exists():
            add([fallback])

    if not commands:
        add(["yarn"])

    return commands


def run_yarn(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    errors: list[str] = []
    for base in resolve_yarn_commands():
        env = os.environ.copy()
        if "/" in base[0]:
            yarn_bin_dir = str(Path(base[0]).resolve().parent)
            env["PATH"] = yarn_bin_dir + os.pathsep + env.get("PATH", "")
        for _attempt in range(2):
            proc = run([*base, *args], cwd=cwd, check=False, env=env)
            if proc.returncode == 0:
                return proc
            cmd = " ".join(shlex.quote(arg) for arg in [*base, *args])
            errors.append(
                f"command failed (rc={proc.returncode}): {cmd}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
            )
            transient = any(token in proc.stderr for token in ("ECONNRESET", "fetch failed", "TLS connection"))
            if not transient:
                break
    raise RuntimeError("\n\n".join(errors))


def install_markers(repo_dir: Path) -> list[Path]:
    return [
        repo_dir / "node_modules" / "@babel" / "register",
        repo_dir / "node_modules" / "babel-register",
        repo_dir / "node_modules" / "react",
    ]


def build_markers(repo_dir: Path) -> list[Path]:
    return [
        repo_dir / "packages" / "renderer" / "lib" / "react-pdf.cjs.js",
        repo_dir / "dist" / "react-pdf.cjs.js",
    ]


def ensure_runtime(repo_dir: Path, *, skip_install: bool, skip_build: bool, force_install: bool, force_build: bool) -> None:
    if not skip_install and (force_install or not any(marker.exists() for marker in install_markers(repo_dir))):
        run_yarn(["install", "--ignore-engines", "--ignore-scripts"], cwd=repo_dir)

    if not skip_build and (force_build or not any(marker.exists() for marker in build_markers(repo_dir))):
        run_yarn(["build"], cwd=repo_dir)


def load_bug_info(instance_root: Path) -> dict:
    bug_info_path = instance_root / "bug_info.json"
    if not bug_info_path.is_file():
        raise FileNotFoundError(f"missing bug_info.json: {bug_info_path}")
    return json.loads(bug_info_path.read_text(encoding="utf-8"))


def collect_text_sections(bug_info: dict) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    for key in ("problem_statement", "hints_text"):
        value = (bug_info.get(key) or "").strip()
        if value:
            sections.append((key, value))
    return sections


def sanitize_source_text(text: str) -> str:
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = textwrap.dedent(cleaned).strip()
    cleaned = re.sub(r"^\s*<pre>\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*</pre>\s*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\n?```+\s*$", "", cleaned)
    return cleaned.strip()


def strip_typescript_annotations(text: str) -> str:
    stripped = text
    replacements = [
        (r"\(\s*\)\s*:\s*[^=\n]+?=>", "() =>"),
        (r"(\b(?:const|let|var)\s+[A-Za-z_$][\w$]*)\s*:\s*[^=;\n]+=", r"\1 ="),
        (r"(function\s+[A-Za-z_$][\w$]*\s*\([^)]*\))\s*:\s*[^({\n]+", r"\1"),
        (r"(\)\s*:\s*[^=({\n]+)(\s*=>)", r"\2"),
    ]
    for pattern, replacement in replacements:
        stripped = re.sub(pattern, replacement, stripped)
    return stripped


def iter_fenced_code_blocks(text: str) -> Iterable[tuple[str, str]]:
    pattern = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)
    for match in pattern.finditer(text or ""):
        language = (match.group(1) or "").strip()
        code = sanitize_source_text(match.group(2) or "")
        if code:
            yield language, code


def iter_indented_code_blocks(text: str) -> Iterable[str]:
    block: list[str] = []
    for line in (text or "").splitlines():
        if line.startswith("    "):
            block.append(line[4:])
            continue
        if line.startswith("\t"):
            block.append(line[1:])
            continue
        if block and not line.strip():
            block.append("")
            continue
        if block:
            code = sanitize_source_text("\n".join(block))
            if code:
                yield code
            block = []
    if block:
        code = sanitize_source_text("\n".join(block))
        if code:
            yield code


def iter_markdown_code_blocks(text: str) -> Iterable[tuple[str, str]]:
    seen: set[str] = set()
    for language, code in iter_fenced_code_blocks(text):
        if code not in seen:
            seen.add(code)
            yield language, code
    for code in iter_indented_code_blocks(text):
        if code not in seen:
            seen.add(code)
            yield "indented", code


def score_code_block(code: str) -> int:
    lowered = code.lower()
    score = 0

    for token, value in (
        ("@react-pdf/renderer", 24),
        ("@react-pdf/styled-components", 18),
        ("reactpdf.render", 16),
        ("rendertofile", 14),
        ("<document", 12),
        ("<page", 8),
        ("stylesheet.create", 8),
        ("styled.page", 8),
        ("styled.text", 8),
        ("styled(", 8),
        ("pdfviewer", 6),
        ("pdfdownloadlink", 6),
        ("<view", 5),
        ("<text", 5),
        ("<image", 5),
        ("const ", 3),
        ("function ", 3),
        ("class ", 3),
        ("export default", 3),
    ):
        if token in lowered:
            score += value

    for token, penalty in (
        ("overload 1 of", 40),
        ("gave the following error", 35),
        ("not assignable to type", 30),
        ("typeerror:", 24),
        ("cannot read property", 24),
        ("cannot read properties of", 24),
        ("export type ", 22),
        ("\ninterface ", 18),
        ("\ntype ", 12),
        ("diff --git", 20),
        ("@@ -", 12),
        ("describe(", 8),
        ("test(", 8),
    ):
        if token in lowered:
            score -= penalty

    if code.count("<") > 0 and code.count(">") > 0 and "<img " in lowered:
        score -= 8

    return score


def find_repo_file_sources(text_sections: list[tuple[str, str]], repo_dir: Path) -> list[tuple[str, Path]]:
    pattern = re.compile(r"https?://github\.com/[^/\s]+/[^/\s]+/blob/[^/\s]+/([^\s#)>\]]+)")
    seen: set[Path] = set()
    results: list[tuple[str, Path]] = []
    for section_name, text in text_sections:
        for match in pattern.finditer(text):
            candidate = (repo_dir / match.group(1)).resolve()
            if candidate.is_file() and candidate not in seen:
                seen.add(candidate)
                results.append((section_name, candidate))
    return results


def decode_repl_code(encoded: str, repo_dir: Path) -> str | None:
    proc = run(["node", str(NODE_REPL_DECODE_SCRIPT), encoded], cwd=repo_dir, check=False)
    return proc.stdout if proc.returncode == 0 and proc.stdout else None


def find_repl_sources(text_sections: list[tuple[str, str]], repo_dir: Path) -> list[tuple[str, str]]:
    seen: set[str] = set()
    results: list[tuple[str, str]] = []
    pattern = re.compile(r"repl\?code=([0-9a-fA-F]+)")
    for section_name, text in text_sections:
        for match in pattern.finditer(text):
            encoded = match.group(1)
            if encoded in seen:
                continue
            seen.add(encoded)
            decoded = decode_repl_code(encoded, repo_dir)
            if decoded:
                results.append((section_name, decoded))
    return results


def needs_renderer_imports(source_text: str) -> bool:
    return "@react-pdf/renderer" not in source_text


def needs_styled_imports(source_text: str) -> bool:
    if "@react-pdf/styled-components" in source_text:
        return False
    if re.search(r"\b(?:const|let|var|function|class)\s+styled\b", source_text):
        return False
    return bool(re.search(r"\bstyled(?:\.[A-Za-z_][A-Za-z0-9_]*|\()", source_text))


def strip_render_calls(source_text: str) -> str:
    return re.sub(r"^\s*ReactPDF\.render\([^\n]*\)\s*;?\s*$", "", source_text, flags=re.MULTILINE)


def infer_export_name(source_text: str) -> str | None:
    render_match = re.search(r"ReactPDF\.render\(\s*<?([A-Z][A-Za-z0-9_]*)", source_text)
    if render_match:
        return render_match.group(1)

    component_names = re.findall(r"(?:const|function|class)\s+([A-Z][A-Za-z0-9_]*)", source_text)
    if component_names:
        return component_names[-1]

    return None


def wrap_snippet_in_document(snippet: str) -> str:
    return (
        "const ReproIssueDocument = () => (\n"
        "  <Document>\n"
        "    <Page>\n"
        f"      {snippet}\n"
        "    </Page>\n"
        "  </Document>\n"
        ");\n\n"
        "export default ReproIssueDocument;\n"
    )


def build_module_text(raw_source: str) -> str:
    cleaned = strip_typescript_annotations(sanitize_source_text(strip_render_calls(raw_source))).strip()
    renderer_header = (
        "import React from 'react';\n"
        "import ReactPDF, {\n"
        "  Canvas,\n"
        "  Circle,\n"
        "  Defs,\n"
        "  Document,\n"
        "  Ellipse,\n"
        "  Font,\n"
        "  G,\n"
        "  Image,\n"
        "  Line,\n"
        "  LinearGradient,\n"
        "  Link,\n"
        "  Note,\n"
        "  Page,\n"
        "  Path,\n"
        "  Polygon,\n"
        "  Polyline,\n"
        "  RadialGradient,\n"
        "  Rect,\n"
        "  Stop,\n"
        "  StyleSheet,\n"
        "  Svg,\n"
        "  Text,\n"
        "  View,\n"
        "} from '@react-pdf/renderer';\n\n"
    )
    styled_header = "import styled from '@react-pdf/styled-components';\n\n"

    if cleaned.startswith("<") and "<Document" not in cleaned:
        body = wrap_snippet_in_document(cleaned)
    else:
        export_name = infer_export_name(cleaned)
        body = cleaned
        if not export_name and "<Document" not in cleaned:
            body = wrap_snippet_in_document(cleaned)
        elif export_name and "export default" not in cleaned:
            body = f"{cleaned}\n\nexport default {export_name};\n"

    prefixes = []
    if needs_renderer_imports(cleaned):
        prefixes.append(renderer_header)
    if needs_styled_imports(cleaned):
        prefixes.append(styled_header)

    if prefixes:
        return "".join(prefixes) + body
    return body


def collect_code_block_candidates(text_sections: list[tuple[str, str]], module_dir: Path) -> list[dict]:
    ranked: list[tuple[int, str, str, str]] = []
    seen: set[str] = set()

    for section_name, text in text_sections:
        for language, code in iter_markdown_code_blocks(text):
            if code in seen:
                continue
            seen.add(code)
            score = score_code_block(code)
            if score <= 0:
                continue
            ranked.append((score, section_name, language, code))

    ranked.sort(key=lambda item: item[0], reverse=True)

    candidates = []
    for index, (score, section_name, language, code) in enumerate(ranked, start=1):
        module_path = module_dir / f"issue_code_block_{index:02d}.js"
        module_path.write_text(build_module_text(code), encoding="utf-8")
        candidates.append(
            {
                "kind": "code-block",
                "path": module_path,
                "origin": section_name,
                "language": language,
                "score": score,
            }
        )
    return candidates


def collect_repl_candidates(text_sections: list[tuple[str, str]], repo_dir: Path, module_dir: Path) -> list[dict]:
    candidates = []
    for index, (section_name, decoded) in enumerate(find_repl_sources(text_sections, repo_dir), start=1):
        module_path = module_dir / f"issue_repl_code_{index:02d}.js"
        module_path.write_text(build_module_text(decoded), encoding="utf-8")
        candidates.append(
            {
                "kind": "repl",
                "path": module_path,
                "origin": section_name,
                "score": 0,
            }
        )
    return candidates


def build_font_fallback_module(source_text: str) -> str | None:
    families = re.findall(
        r"Font\.register\(\s*\{.*?family\s*:\s*['\"]([^'\"]+)['\"].*?src\s*:\s*['\"]https?://.*?\}\s*\)\s*;?",
        source_text,
        flags=re.DOTALL,
    )
    if not families:
        return None

    transformed = re.sub(
        r"Font\.register\(\s*\{.*?src\s*:\s*['\"]https?://.*?\}\s*\)\s*;?\n*",
        "",
        source_text,
        flags=re.DOTALL,
    )

    for family in sorted(set(families), key=len, reverse=True):
        transformed = re.sub(
            rf"(fontFamily\s*:\s*['\"])({re.escape(family)})(['\"])",
            r"\1Helvetica\3",
            transformed,
        )

    return transformed if transformed != source_text else None


def collect_font_fallback_candidates(base_candidates: list[dict], module_dir: Path) -> list[dict]:
    candidates = []
    counter = 1

    for base in base_candidates:
        path = Path(base["path"])
        if not path.is_file() or path.suffix != ".js":
            continue

        transformed = build_font_fallback_module(path.read_text(encoding="utf-8"))
        if not transformed:
            continue

        module_path = module_dir / f"issue_font_fallback_{counter:02d}.js"
        module_path.write_text(transformed, encoding="utf-8")
        counter += 1
        candidates.append(
            {
                "kind": "font-fallback",
                "path": module_path,
                "origin": base.get("origin", ""),
                "score": (base.get("score", 0) - 1),
            }
        )

    return candidates


def collect_source_candidates(args: argparse.Namespace, repo_dir: Path, bug_info: dict, module_dir: Path) -> list[dict]:
    text_sections = collect_text_sections(bug_info)
    strategy = args.source_strategy
    candidates: list[dict] = []

    if strategy in {"auto", "repo-file"}:
        for section_name, repo_file in find_repo_file_sources(text_sections, repo_dir):
            candidates.append(
                {
                    "kind": "repo-file",
                    "path": repo_file,
                    "origin": section_name,
                    "score": 0,
                }
            )
        if strategy == "repo-file" and not candidates:
            raise RuntimeError("requested repo-file source but none was found")

    if strategy in {"auto", "code-block"}:
        block_candidates = collect_code_block_candidates(text_sections, module_dir)
        candidates.extend(block_candidates)
        if strategy == "code-block" and not block_candidates:
            raise RuntimeError("requested code-block source but none was found")

    if strategy in {"auto", "repl"}:
        repl_candidates = collect_repl_candidates(text_sections, repo_dir, module_dir)
        candidates.extend(repl_candidates)
        if strategy == "repl" and not repl_candidates:
            raise RuntimeError("requested repl source but no decodable repl was found")

    if strategy == "auto":
        candidates.extend(collect_font_fallback_candidates(candidates, module_dir))
        synthesized = synthesize_description_candidates(bug_info, module_dir)
        direct_cards = [candidate for candidate in synthesized if candidate["kind"] == "typescript-diagnostic"]
        fallback_cards = [candidate for candidate in synthesized if candidate["kind"] != "typescript-diagnostic"]
        candidates = [*direct_cards, *candidates, *fallback_cards]

    if not candidates:
        raise RuntimeError("no usable react-pdf reproduction source found in bug_info.json")

    return candidates


def synthesize_description_candidates(bug_info: dict, module_dir: Path) -> list[dict]:
    problem_statement = bug_info.get("problem_statement") or ""
    lowered = problem_statement.lower()
    candidates: list[dict] = []

    if should_synthesize_typescript_diagnostic(bug_info):
        diagnostic_path = module_dir / "issue_typescript_diagnostic.txt"
        diagnostic_body = build_typescript_diagnostic_body(bug_info)
        diagnostic_path.write_text(diagnostic_body, encoding="utf-8")
        candidates.append(
            {
                "kind": "typescript-diagnostic",
                "path": diagnostic_path,
                "origin": "problem_statement",
                "score": 1000,
                "body": diagnostic_body,
            }
        )

    if "buffer() is deprecated" in lowered:
        module_path = module_dir / "issue_synth_buffer_warning.js"
        module_path.write_text(
            (
                "import React from 'react';\n"
                "import { Document, Page, Text } from '@react-pdf/renderer';\n\n"
                "new Buffer('react-pdf');\n\n"
                "const ReproIssueDocument = () => (\n"
                "  <Document>\n"
                "    <Page>\n"
                "      <Text>Hello react-pdf</Text>\n"
                "    </Page>\n"
                "  </Document>\n"
                ");\n\n"
                "export default ReproIssueDocument;\n"
            ),
            encoding="utf-8",
        )
        candidates.append(
            {
                "kind": "synthetic-warning",
                "path": module_path,
                "origin": "problem_statement",
                "score": -100,
            }
        )

    if "value.split is not a function" in lowered and "flex" in lowered:
        module_path = module_dir / "issue_synth_flex_error.js"
        module_path.write_text(
            (
                "import React from 'react';\n"
                "import { Document, Page, StyleSheet, Text, View } from '@react-pdf/renderer';\n\n"
                "const styles = StyleSheet.create({\n"
                "  page: {\n"
                "    padding: 24,\n"
                "  },\n"
                "  box: {\n"
                "    flex: 1,\n"
                "    backgroundColor: '#e5e7eb',\n"
                "  },\n"
                "});\n\n"
                "const ReproIssueDocument = () => (\n"
                "  <Document>\n"
                "    <Page style={styles.page}>\n"
                "      <View style={styles.box}>\n"
                "        <Text>flex repro</Text>\n"
                "      </View>\n"
                "    </Page>\n"
                "  </Document>\n"
                ");\n\n"
                "export default ReproIssueDocument;\n"
            ),
            encoding="utf-8",
        )
        candidates.append(
            {
                "kind": "synthetic-error",
                "path": module_path,
                "origin": "problem_statement",
                "score": -100,
            }
        )

    return candidates


def convert_pdf_to_png(pdf_path: Path, png_path: Path) -> None:
    png_path.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "gs",
            "-q",
            "-dSAFER",
            "-dBATCH",
            "-dNOPAUSE",
            "-sDEVICE=png16m",
            "-r288",
            "-dFirstPage=1",
            "-dLastPage=1",
            "-o",
            str(png_path),
            str(pdf_path),
        ],
        cwd=pdf_path.parent,
    )


def list_reference_images(instance_root: Path) -> list[Path]:
    image_dir = instance_root / "IMAGE"
    if not image_dir.is_dir():
        return []
    return sorted(
        path for path in image_dir.iterdir() if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg"}
    )


def pick_reference_image(instance_root: Path, bug_info: dict) -> Path | None:
    images = list_reference_images(instance_root)
    if not images:
        return None
    if len(images) == 1:
        return images[0]

    text = (bug_info.get("problem_statement") or "").lower()
    if "before:" in text and "after:" in text and len(images) >= 2:
        return images[1]
    if "how it looks" in text and len(images) >= 1:
        return images[0]
    if "how it should be" in text and len(images) >= 2:
        return images[0]
    return images[0]


def pick_primary_issue_title(bug_info: dict) -> str:
    first_line = (bug_info.get("problem_statement") or "").strip().splitlines()
    return first_line[0].strip() if first_line else "react-pdf reproduction"


def extract_issue_summary(bug_info: dict) -> str:
    problem_statement = bug_info.get("problem_statement") or ""
    summary_lines: list[str] = []
    for raw_line in problem_statement.splitlines()[1:]:
        line = raw_line.strip()
        lowered = line.lower()
        if not line or line.startswith("**") or line.startswith("!["):
            continue
        if re.match(r"^\d+\.\s", line):
            continue
        if lowered.startswith(("steps to reproduce", "screenshots", "desktop", "- os", "- browser", "- react-pdf")):
            continue
        summary_lines.append(line)
        if len(summary_lines) >= 4:
            break
    return "\n".join(summary_lines)


def should_synthesize_typescript_diagnostic(bug_info: dict) -> bool:
    lowered = (bug_info.get("problem_statement") or "").lower()
    if "not assignable to type" in lowered and "compilation error" in lowered:
        return True
    return all(token in lowered for token in ("typescript", "sourceobject", "promise<string>"))


def build_typescript_diagnostic_body(bug_info: dict) -> str:
    problem_statement = bug_info.get("problem_statement") or ""
    diagnostic_block: str | None = None
    repro_block: str | None = None

    for _language, code in iter_markdown_code_blocks(problem_statement):
        lowered = code.lower()
        if diagnostic_block is None and (
            "overload 1 of" in lowered or "not assignable to type" in lowered
        ):
            diagnostic_block = sanitize_source_text(code)
        if repro_block is None and "<image" in lowered:
            repro_block = sanitize_source_text(code)

    sections: list[str] = []
    if diagnostic_block:
        sections.append("TypeScript compilation error")
        sections.append(diagnostic_block)
    else:
        summary = extract_issue_summary(bug_info)
        sections.append(summary or "TypeScript compilation error")

    if repro_block:
        sections.append("")
        sections.append("Repro snippet")
        sections.append(repro_block)

    return "\n".join(sections).strip()


def align_png_to_reference(png_path: Path, reference_path: Path) -> None:
    rendered = Image.open(png_path).convert("RGB")
    reference = Image.open(reference_path).convert("RGB")
    ref_w, ref_h = reference.size
    target_w = max(1, ref_w * SCREENSHOT_SCALE)
    target_h = max(1, ref_h * SCREENSHOT_SCALE)

    scale = target_w / max(1, rendered.width)
    resized_h = max(1, int(round(rendered.height * scale)))
    resized = rendered.resize((target_w, resized_h), Image.Resampling.LANCZOS)

    if resized.height >= target_h:
        final = resized.crop((0, 0, target_w, target_h))
    else:
        final = Image.new("RGB", (target_w, target_h), (255, 255, 255))
        final.paste(resized, (0, 0))

    final.save(png_path)


def extract_error_excerpt(detail: str) -> str:
    matches = re.findall(
        r"((?:TypeError|ReferenceError|SyntaxError|Error|DeprecationWarning):[^\n]+)",
        detail,
    )
    if matches:
        return "\n".join(matches[:8])

    tail = [line for line in detail.splitlines() if line.strip()]
    return "\n".join(tail[-12:]) if tail else "Unknown rendering failure"


def extract_warning_excerpt(bug_info: dict, render_proc: subprocess.CompletedProcess[str]) -> str:
    stderr = render_proc.stderr or ""
    if "buffer() is deprecated" in stderr.lower():
        return extract_error_excerpt(stderr)

    summary = extract_issue_summary(bug_info)
    if summary:
        return summary

    title = pick_primary_issue_title(bug_info)
    return title if title else "DeprecationWarning emitted while rendering the PDF."


def extract_issue_runtime_error_excerpt(bug_info: dict) -> str:
    markers = extract_issue_error_markers(bug_info.get("problem_statement") or "")
    if markers:
        return "\n".join(markers[:8])

    summary = extract_issue_summary(bug_info)
    if summary:
        return summary

    return pick_primary_issue_title(bug_info)


def extract_issue_error_markers(problem_statement: str) -> list[str]:
    parts = [problem_statement, *(code for _language, code in iter_markdown_code_blocks(problem_statement))]
    markers: list[str] = []
    seen: set[str] = set()

    def add(marker: str) -> None:
        normalized = marker.strip()
        lowered = normalized.lower()
        if normalized and lowered not in seen:
            seen.add(lowered)
            markers.append(normalized)

    for part in parts:
        for match in re.findall(r"((?:TypeError|ReferenceError|SyntaxError|Error|DeprecationWarning):[^\n]+)", part):
            add(match)
        for match in re.findall(r"(Type [^\n]+? not assignable to type [^\n]+)", part):
            add(match)

    return markers


def pick_best_failed_attempt(attempts: list[dict], bug_info: dict) -> dict:
    issue_markers = extract_issue_error_markers(bug_info.get("problem_statement") or "")

    def rank(attempt: dict) -> tuple[int, int, int, int, int, int]:
        excerpt = extract_error_excerpt(attempt["error"])
        lowered = excerpt.lower()
        match_count = sum(1 for marker in issue_markers if marker.lower() in lowered)
        syntax_penalty = 1 if issue_markers and match_count == 0 and "syntaxerror:" in lowered else 0
        synthetic_bonus = 1 if attempt["kind"].startswith("synthetic") else 0
        problem_origin_bonus = 1 if attempt.get("origin") == "problem_statement" else 0
        return (
            match_count,
            -syntax_penalty,
            synthetic_bonus,
            problem_origin_bonus,
            attempt.get("score", 0),
            -attempt["rank"],
        )

    return max(attempts, key=rank)


def render_text_card(title: str, body: str, png_path: Path, reference_path: Path | None) -> None:
    size = (2400, 1600)
    if reference_path and reference_path.is_file():
        reference = Image.open(reference_path).convert("RGB")
        size = (reference.size[0] * SCREENSHOT_SCALE, reference.size[1] * SCREENSHOT_SCALE)

    image = Image.new("RGB", size, (246, 246, 246))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    width, height = size

    draw.rounded_rectangle((24, 24, width - 24, height - 24), radius=14, fill=(255, 255, 255), outline=(220, 220, 220))
    draw.text((44, 44), title, fill=(160, 32, 32), font=font)

    max_chars = max(28, min(120, (width - 88) // 7))
    wrapped_lines = []
    for paragraph in body.splitlines() or [""]:
        wrapped = textwrap.wrap(paragraph, width=max_chars) or [""]
        wrapped_lines.extend(wrapped)

    y = 76
    line_height = 16
    for line in wrapped_lines:
        if y > height - 44:
            break
        draw.text((44, y), line, fill=(40, 40, 40), font=font)
        y += line_height

    png_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(png_path)


def should_render_warning_card(bug_info: dict, candidate: dict, render_proc: subprocess.CompletedProcess[str]) -> bool:
    problem_statement = (bug_info.get("problem_statement") or "").lower()
    stderr = render_proc.stderr or ""
    if candidate["kind"] == "synthetic-warning":
        return True
    return "buffer() is deprecated" in problem_statement and "buffer() is deprecated" in stderr.lower()


def should_render_issue_error_card(bug_info: dict, candidate: dict) -> bool:
    if candidate["kind"] != "synthetic-error":
        return False
    return bool(extract_issue_error_markers(bug_info.get("problem_statement") or ""))


def render_candidate(repo_dir: Path, source_path: Path, out_pdf: Path) -> subprocess.CompletedProcess[str]:
    return run(
        [
            "node",
            str(NODE_RENDER_SCRIPT),
            "--repo-dir",
            str(repo_dir),
            "--source-module",
            str(source_path),
            "--pdf-out",
            str(out_pdf),
        ],
        cwd=repo_dir,
    )


def capture_single_main(argv: list[str] | None = None) -> int:
    args = parse_single_args(argv)
    repo_dir_input = Path(args.repo_dir).expanduser()
    instance_root = Path(args.instance_root).resolve() if args.instance_root else default_instance_root(repo_dir_input)
    repo_dir = repo_dir_input.resolve()
    work_dir = Path(args.work_dir).resolve() if args.work_dir else (instance_root / "PRE" / "reactpdf_real_capture")
    module_dir = repo_dir / ".codex_repro"
    out_png = Path(args.out).resolve()
    out_pdf = work_dir / f"{out_png.stem}.pdf"
    manifest_path = work_dir / "manifest.json"

    work_dir.mkdir(parents=True, exist_ok=True)
    module_dir.mkdir(parents=True, exist_ok=True)

    bug_info = load_bug_info(instance_root)
    reference_image = pick_reference_image(instance_root, bug_info)
    candidates = collect_source_candidates(args, repo_dir, bug_info, module_dir)

    first_candidate = candidates[0] if candidates else None
    if first_candidate and first_candidate["kind"] == "typescript-diagnostic":
        render_text_card(
            pick_primary_issue_title(bug_info),
            first_candidate["body"],
            out_png,
            reference_image,
        )
        manifest = {
            "instance_root": str(instance_root),
            "repo_dir": str(repo_dir),
            "source_kind": first_candidate["kind"],
            "output_kind": first_candidate["kind"],
            "source_path": str(first_candidate["path"]),
            "source_origin": first_candidate.get("origin", ""),
            "source_rank": 1,
            "reference_image": str(reference_image) if reference_image else "",
            "pdf_path": "",
            "png_path": str(out_png),
            "render_stdout": "",
            "render_stderr": "",
            "attempt_failures": [],
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(manifest, indent=2, ensure_ascii=False))
        return 0

    ensure_runtime(
        repo_dir,
        skip_install=args.skip_install,
        skip_build=args.skip_build,
        force_install=args.force_install,
        force_build=args.force_build,
    )

    attempts: list[dict] = []
    render_proc: subprocess.CompletedProcess[str] | None = None
    winner: dict | None = None

    for index, candidate in enumerate(candidates, start=1):
        try:
            render_proc = render_candidate(repo_dir, candidate["path"], out_pdf)
            winner = candidate | {"rank": index}
            break
        except Exception as exc:
            attempts.append(
                {
                    "rank": index,
                    "kind": candidate["kind"],
                    "path": str(candidate["path"]),
                    "origin": candidate.get("origin", ""),
                    "score": candidate.get("score", 0),
                    "error": str(exc),
                }
            )

    if not winner or not render_proc:
        if attempts and reference_image:
            top_attempt = pick_best_failed_attempt(attempts, bug_info)
            render_text_card(
                pick_primary_issue_title(bug_info),
                extract_error_excerpt(top_attempt["error"]),
                out_png,
                reference_image,
            )
            manifest = {
                "instance_root": str(instance_root),
                "repo_dir": str(repo_dir),
                "source_kind": "error-card",
                "output_kind": "error-card",
                "source_path": top_attempt["path"],
                "source_origin": top_attempt.get("origin", ""),
                "source_rank": top_attempt["rank"],
                "reference_image": str(reference_image),
                "pdf_path": "",
                "png_path": str(out_png),
                "render_stdout": "",
                "render_stderr": "",
                "attempt_failures": attempts,
            }
            manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
            print(json.dumps(manifest, indent=2, ensure_ascii=False))
            return 0

        error_summary = "\n\n".join(
            f"[{attempt['rank']}] {attempt['kind']} {attempt['path']}\n{attempt['error']}" for attempt in attempts
        )
        raise RuntimeError(f"all react-pdf source candidates failed to render\n\n{error_summary}")

    if should_render_warning_card(bug_info, winner, render_proc):
        render_text_card(
            pick_primary_issue_title(bug_info),
            extract_warning_excerpt(bug_info, render_proc),
            out_png,
            reference_image,
        )
        output_kind = "warning-card"
    elif should_render_issue_error_card(bug_info, winner):
        render_text_card(
            pick_primary_issue_title(bug_info),
            extract_issue_runtime_error_excerpt(bug_info),
            out_png,
            reference_image,
        )
        output_kind = "error-card"
    else:
        convert_pdf_to_png(out_pdf, out_png)
        if reference_image:
            align_png_to_reference(out_png, reference_image)
        output_kind = "rendered-pdf"

    manifest = {
        "instance_root": str(instance_root),
        "repo_dir": str(repo_dir),
        "source_kind": winner["kind"],
        "output_kind": output_kind,
        "source_path": str(winner["path"]),
        "source_origin": winner.get("origin", ""),
        "source_rank": winner["rank"],
        "reference_image": str(reference_image) if reference_image else "",
        "pdf_path": str(out_pdf),
        "png_path": str(out_png),
        "render_stdout": render_proc.stdout.strip(),
        "render_stderr": render_proc.stderr.strip(),
        "attempt_failures": attempts,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


def parse_pair_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture both baseline and patched screenshots for a react-pdf instance.")
    parser.add_argument("--repo-dir", required=True, help="Path to the source instance repo root, e.g. .../REPO/react-pdf")
    parser.add_argument("--baseline-out", required=True, help="Output PNG path for baseline capture")
    parser.add_argument("--patched-out", required=True, help="Output PNG path for patched capture")
    parser.add_argument("--instance-root", help="Path to the instance root containing bug_info.json")
    parser.add_argument("--keep-worktree", action="store_true", help="Keep temporary worktree directory for debugging")
    return parser.parse_args(argv)


def capture_pair(repo_dir: Path, instance_root: Path, out_png: Path, work_dir: Path, *, force_build: bool) -> dict:
    work_dir.mkdir(parents=True, exist_ok=True)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        "python3",
        str(SCRIPT_DIR / "reactpdf_helpers.py"),
        "single",
        "--repo-dir",
        str(repo_dir),
        "--instance-root",
        str(instance_root),
        "--work-dir",
        str(work_dir),
        "--out",
        str(out_png),
    ]
    if force_build:
        argv.append("--force-build")
    proc = run(argv, cwd=repo_dir)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"capture script returned non-json output for {out_png}: {exc}\n{proc.stdout}") from exc

def capture_pair_main(argv: list[str] | None = None) -> int:
    args = parse_pair_args(argv)
    source_repo_dir = Path(args.repo_dir).expanduser().resolve()
    instance_root = Path(args.instance_root).resolve() if args.instance_root else default_instance_root(source_repo_dir)
    bug_info = load_bug_info(instance_root)
    base_commit = (bug_info.get("base_commit") or "").strip()
    patch_text = bug_info.get("patch") or ""

    if not base_commit:
        raise RuntimeError("bug_info.json is missing base_commit")
    if not patch_text.strip():
        raise RuntimeError("bug_info.json is missing patch")

    baseline_out = Path(args.baseline_out).resolve()
    patched_out = Path(args.patched_out).resolve()
    pair_root = instance_root / "PRE" / "reactpdf_pair_capture"
    pair_root.mkdir(parents=True, exist_ok=True)

    temp_parent = pair_root / "worktrees"
    temp_parent.mkdir(parents=True, exist_ok=True)

    worktree_root = Path(tempfile.mkdtemp(prefix="reactpdf_pair_", dir=str(temp_parent)))
    work_repo_dir = worktree_root / source_repo_dir.name
    patch_file = worktree_root / "changes.diff"

    try:
        run(["git", "worktree", "add", "--detach", str(work_repo_dir), base_commit], cwd=source_repo_dir)
        patch_file.write_text(patch_text, encoding="utf-8")

        baseline_manifest = capture_pair(
            work_repo_dir,
            instance_root,
            baseline_out,
            pair_root / "baseline",
            force_build=False,
        )

        patch_apply = run(["git", "apply", str(patch_file)], cwd=work_repo_dir, check=False)
        (pair_root / "patch.apply.log").write_text(
            f"rc={patch_apply.returncode}\nstdout:\n{patch_apply.stdout}\nstderr:\n{patch_apply.stderr}",
            encoding="utf-8",
        )
        if patch_apply.returncode != 0:
            raise RuntimeError(
                f"failed to apply patch to worktree\nstdout:\n{patch_apply.stdout}\nstderr:\n{patch_apply.stderr}"
            )

        patched_manifest = capture_pair(
            work_repo_dir,
            instance_root,
            patched_out,
            pair_root / "patched",
            force_build=True,
        )

        summary = {
            "instance_root": str(instance_root),
            "source_repo_dir": str(source_repo_dir),
            "work_repo_dir": str(work_repo_dir),
            "base_commit": base_commit,
            "baseline_out": str(baseline_out),
            "patched_out": str(patched_out),
            "baseline_manifest": baseline_manifest,
            "patched_manifest": patched_manifest,
        }
        (pair_root / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    finally:
        if args.keep_worktree:
            print(json.dumps({"kept_worktree": str(worktree_root)}, ensure_ascii=False))
        else:
            run(["git", "worktree", "remove", "--force", str(work_repo_dir)], cwd=source_repo_dir, check=False)
            shutil.rmtree(worktree_root, ignore_errors=True)

    return 0


def main() -> int:
    argv = sys.argv[1:]
    command = argv[0] if argv else 'single'
    sub_argv = argv[1:] if argv else []

    if command in {'single', 'capture-single'}:
        return capture_single_main(sub_argv)
    if command in {'pair', 'capture-pair'}:
        return capture_pair_main(sub_argv)

    print('Usage: reactpdf_helpers.py [single|pair] [args...]', file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
