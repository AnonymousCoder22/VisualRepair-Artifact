"""VisualRepair prompt catalog (documentation only).

This module collects the prompts used by the different VisualRepair stages in one
place so that reviewers can inspect the complete prompting workflow without
tracing control flow across the repository.  It is intentionally not imported by
the runtime pipeline; the executable prompt builders remain in their original
files.

Placeholders such as ``{problem_statement}`` denote values injected at runtime.
Image inputs are attached as multimodal message parts after/before the textual
user prompt as noted by each entry.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PromptSpec:
    """One reviewer-facing prompt description."""

    stage: str
    purpose: str
    source: str
    inputs: tuple[str, ...]
    system_prompt: str
    user_prompt: str
    image_placement: str = "No image input."
    notes: str = ""


# ---------------------------------------------------------------------------
# Stage 1: Dynamic Test-time Region Focusing (DTRF)
# ---------------------------------------------------------------------------

REGION_GROUNDING = PromptSpec(
    stage="1. DTRF image-region grounding",
    purpose=(
        "Locates three distinct, non-overlapping regions in a screenshot that are "
        "likely related to the reported defect, enabling subsequent multi-scale "
        "cropping and visual focusing."
    ),
    source="Tools/preprocess/ground.py::ground_multi_bbox",
    inputs=("problem_statement", "image_resolution", "bug_image"),
    system_prompt="(The model API receives this as a single user message.)",
    user_prompt=r"""You are a master at analyzing images and code.

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
""",
    image_placement="The source bug image follows the text in the same user message.",
)


# ---------------------------------------------------------------------------
# Stage 2: Image-to-code support (documentation retrieval and reproduction)
# ---------------------------------------------------------------------------

DOCUMENT_LOCALIZATION = PromptSpec(
    stage="2.1 Repository-document localization",
    purpose=(
        "Selects the repository documents needed to understand and reproduce a "
        "visual defect from the issue report, screenshots, and document tree."
    ),
    source="Code/multimodal_trans.py::doc_file_locate_prompt_construction",
    inputs=("problem_statement", "doc_structure", "max_docs_number", "bug_images"),
    system_prompt=r"""The user want to understand and reproduce the issue in the Bug Report. You can find and provide necessary bug-related documents in the Repo Documents to help the user understand and reproduce current issue.
The user will provide the Bug Report (may attach the bug images) and Document Structure. Please describe the bug scenario images, then return all bug related documents for user references.

Return a JSON object with:
{
    "bug_scenario": "Description of the bug scenario.",
    "documents": ["src/path/document_1.js", "src/path/document_2.js"],
    "explanation": "Why these documents are necessary to understand and reproduce the issue."
}
""",
    user_prompt=r"""I will give you the bug related information (i.e., Bug Report and Images) for your references, you need to find all bug image/scenario related documents (at most {max_docs_number} bug-related documents) in the Repo Document Dir.
1. Read the bug report and view the bug scenario images to describe the images and analyze related elements (e.g., Button, TextInput, Background) involved in the bug.
2. Look at the Document Structure to find documents needed to understand and reproduce the issue.
3. Save the relevant documents and explain why each is necessary.

* Bug Report
```markdown
{problem_statement}
```

* Document Structure
```
{doc_structure}
```
""",
    image_placement="Bug images are appended after the textual user prompt.",
    notes=(
        "Claude uses the same task with an explicit JSON-schema reminder for keys "
        "bug_scenario, documents, and explanation."
    ),
)


REPRODUCTION_CODE_GENERATION = PromptSpec(
    stage="2.2 Visual-scenario reproduction",
    purpose=(
        "Generates code that reproduces the visual defect from the issue report, "
        "screenshots, and retrieved documentation; when code reproduction is not "
        "appropriate, it returns a visual analysis instead."
    ),
    source="Code/multimodal_trans.py::reproduce_code_generate_prompt_construction",
    inputs=("problem_statement", "doc_files_content", "bug_images"),
    system_prompt=r"""The user want to reproduce the issue in the Bug Report. You need to read the bug report and related documents to help the user understand and reproduce current issue.
The user will provide the Bug Report (may attach the bug images) and Related Documents. Please describe the bug scenario images, then generate the reproduce code for user references.

Return a JSON object with:
{
    "bug_scenario": "Description of the bug scenario.",
    "reproduce_code": "Code that reproduces the bug scenario, or visual analysis when reproduction code does not apply.",
    "explanation": "Explanation of how the reproduce code was generated."
}
""",
    user_prompt=r"""I will give you the Bug Report and related Documents for your references, you need to generate Reproduce Code to reproduce the bug scenario (i.e., bug images).
1. Read the bug report and view the bug scenario images to describe the bug scenario.
2. Read Related Documents to understand the scenario and learn how to reproduce it.
3. Generate reproduce code for the bug scenario.
   If the images do not show a visual symptom, or reproduction code is unsuitable, output a description and analysis of the visual images.

* Bug Report
```markdown
{problem_statement}
```

* Related Documents
{doc_files_content}
""",
    image_placement="Bug images are appended after the textual user prompt.",
    notes=(
        "Claude uses the same task with an explicit JSON-schema reminder for keys "
        "bug_scenario, reproduce_code, and explanation."
    ),
)


# ---------------------------------------------------------------------------
# Stage 3: Fault localization
# ---------------------------------------------------------------------------

KEYWORD_GENERATION = PromptSpec(
    stage="3.1 Bug-keyword generation",
    purpose=(
        "Extracts search terms from the issue report and screenshots that are likely "
        "to occur in defect-related source code."
    ),
    source="Code/workflow.py::keywords_generation_prompt_construction",
    inputs=("problem_statement", "bug_images"),
    system_prompt=r"""You are a senior software engineer, you excel at analyzing bug reports and finding key information from bug scenario images to help locate bug files.
The user will provide the Bug Report (may attach the bug images). Please analyze the bug scenario images, then return some keywords may appear in the bug code for bug file search and explain why these keywords may in bug files.

EXAMPLE OUTPUT:
{
    "bug_analyze": "Analyze the bug scenario and find key information by looking at the bug images.",
    "bug_keywords": ["keyword_1", "keyword_2", "keyword_3"],
    "explanation": "Explanation of why these keywords may appear in the bug files."
}
""",
    user_prompt=r"""I will give you the bug related information (i.e., Bug Report), you need to analyze what valid information can be obtained from the bug scenario images to help locate the bug files.
1. Read the bug report and view the bug scenario images to analyze the bug scenario.
2. Expand the analysis based on the screenshots and provide keywords that may appear in the bug code for bug-file search.

* Bug Report
'''
{problem_statement}
'''
""",
    image_placement="Bug images are appended after the textual user prompt.",
)


REPOSITORY_FILE_LOCALIZATION = PromptSpec(
    stage="3.2 Repository-level file localization",
    purpose=(
        "Retrieves all potentially defect-related files from the repository tree, "
        "issue report, and screenshots."
    ),
    source="Code/workflow.py::all_file_locate_prompt_construction",
    inputs=("problem_statement", "repo_structure", "bug_images"),
    system_prompt=r"""The user will provide the Bug Report (may attach the bug images) and Repository Structure. Please describe the bug scenario images, then return all bug related files and explain why these files are bug related.

Return a JSON object with:
{
    "bug_scenario": "Description of the bug scenario.",
    "bug_files": ["src/bug_file1.js", "build/bug_file2.js"],
    "explanation": "Explanation of why these files are bug related."
}
""",
    user_prompt=r"""I will give you the bug related information (i.e., Bug Report) for your references, you need to find all suspicious bug related files in the code Repo.
1. Read the bug report and view the bug scenario images to describe and analyze the bug scenario.
2. Look at the Repository Structure to find files that would need to be edited to fix the problem.
3. Save all bug-related files and explain why they are related.

* Bug Report
'''
{problem_statement}
'''

* Repository Structure
'''
{repo_structure}
'''
""",
    image_placement="Bug images are appended after the textual user prompt.",
    notes="Claude adds an explicit JSON type declaration for the three output fields.",
)


KEY_FILE_LOCALIZATION = PromptSpec(
    stage="3.3 Key-file localization over code skeletons",
    purpose=(
        "Uses compressed code skeletons to narrow the files retrieved in the previous "
        "stage to the most likely edit targets."
    ),
    source="Code/workflow.py::key_file_locate_prompt_construction",
    inputs=("problem_statement", "compressed_bug_files", "max_candidate_bug_files", "bug_images"),
    system_prompt=r"""The user will provide the Bug Report (may attach the bug images) and Compressed Bug Files. Please describe the bug scenario images, then return key bug files and explain why these files are bug files.

Compressed Bug Files are skeletons: they retain class/function headers, class fields, method signatures, and module/class comments while omitting full bodies.

Return a JSON object with:
{
    "bug_scenario": "Description of the bug scenario.",
    "bug_files": ["src/bug_file1.js", "components/bug_file2.js"],
    "explanation": "Explanation of why these files are bug files."
}
""",
    user_prompt=r"""I will give you the bug related information (i.e., Bug Report) for your references, you need to find key bug files {files_limit_token} by looking at all Compressed Bug Files.
1. Read the bug report and view the bug scenario images to describe the bug scenario.
2. Inspect all compressed bug files to find the key files that would need editing.
3. Save the key bug files {files_limit_token} and explain why they are relevant.

* Bug Report
'''
{problem_statement}
'''

* Compressed Bug Files
'''
{compressed_bug_files}
'''
""",
    image_placement="Bug images are appended after the textual user prompt.",
    notes=(
        "{files_limit_token} becomes an at-most-N constraint when the candidate set exceeds "
        "max_candidate_bug_files. Claude adds explicit JSON output types."
    ),
)


CLASS_FUNCTION_LOCALIZATION = PromptSpec(
    stage="3.4 Class/function-level localization",
    purpose=(
        "Identifies the classes and functions most likely to contain the defect within "
        "the selected file skeletons."
    ),
    source="Code/workflow.py::key_class_function_locate_prompt_construction",
    inputs=("problem_statement", "compressed_key_bug_files", "bug_images"),
    system_prompt=r"""The user will provide the Bug Report (may attach the bug images) and Compressed Bug Files. Please describe the bug scenario images, then return key bug classes/functions and explain why these classes/functions are bug related elements.

Compressed Bug Files are skeletons retaining declarations, signatures, fields, and comments. If a bug snippet is not inside a class/function, or the file has no class/function, output only the bug file name.

Return a JSON object with:
{
    "bug_scenario": "Description of the bug scenario.",
    "bug_classes": ["bug_file_name//class_name_1"],
    "bug_functions": ["bug_file_name//function_name_1"],
    "explanation": "Explanation of why these classes/functions are bug-related elements."
}
""",
    user_prompt=r"""I will give you the bug related information (i.e., Bug Report) for your references, you need to find key bug classes/functions by looking at all Compressed Bug Files.
1. Read the bug report and view the bug scenario images to describe the bug scenario.
2. Inspect the compressed bug files to find classes or functions that would need editing.
3. Save the key classes/functions and explain why they are bug-related elements.

* Bug Report
'''
{problem_statement}
'''

* Compressed Bug Files
'''
{compressed_key_bug_files}
'''
""",
    image_placement="Bug images are appended after the textual user prompt.",
    notes="Claude adds explicit JSON output types for all four fields.",
)


LINE_LEVEL_LOCALIZATION = PromptSpec(
    stage="3.5 Line-level fault localization",
    purpose=(
        "Identifies the exact lines that likely require modification within numbered "
        "candidate code snippets."
    ),
    source="Code/workflow.py::line_level_class_function_locate_prompt_construction",
    inputs=("problem_statement", "bug_files_with_context_dict", "bug_images"),
    system_prompt=r"""The user will provide the Bug Report (may attach the bug images) and Bug Files. Please describe the bug scenario images, then return key line-level bug locations and explain why these code lines are bug related elements.
Bug Files are represented as a dictionary: each key is a file path and each value is a code snippet with line numbers. Output line numbers for every file; use an empty list if a file has no bug location.

EXAMPLE OUTPUT:
{
    "bug_locations": {
        "bug_file_path_1": [100, 101, 102, 103],
        "bug_file_path_2": [22, 106, 139],
        "bug_file_path_3": []
    },
    "explanation": "Explanation of why these lines are bug-related code elements."
}
""",
    user_prompt=r"""I will give you the bug related information (i.e., Bug Report) for your references, you need to find line-level bug locations by looking at all Bug Files.
1. Read the bug report and view the bug scenario images to describe the bug scenario.
2. Inspect all bug files to find line-level locations that would need editing.
3. Save all line numbers and explain why those lines are bug-related elements.

* Bug Report
'''
{problem_statement}
'''

* Bug Files
'''
{bug_files_with_context_dict}
'''
""",
    image_placement="Bug images are appended after the textual user prompt.",
)


# ---------------------------------------------------------------------------
# Stage 4: Patch generation, build-error refinement, and selection
# ---------------------------------------------------------------------------

PATCH_GENERATION = PromptSpec(
    stage="4.1 Patch generation",
    purpose=(
        "Combines the issue report, screenshots, and localized code snippets to infer "
        "the root cause and generate directly applicable SEARCH/REPLACE edits."
    ),
    source="Code/workflow.py::patch_generation_prompt_construction",
    inputs=("problem_statement", "bug_files_with_context_dict", "bug_images"),
    system_prompt=r"""The user will provide the Bug Report (may attach the bug images) and Bug Code Snippets. Analyze the images to infer possible root causes, locate the bug, and generate patches for the supplied snippets.

Bug Code Snippets are a dictionary whose keys are file paths and whose values are key snippets; omitted sections are represented by `...`.

First localize the bug, then generate SEARCH/REPLACE edits. Every edit must contain:
1. The exact bug file path from Bug Code Snippets (never the reproduce-code file).
2. `<<<<<<< SEARCH`
3. A contiguous existing-code chunk.
4. `=======`
5. The replacement code.
6. `>>>>>>> REPLACE`

Requirements:
- Preserve exact indentation.
- Include at least 3 lines of SEARCH context.
- Never use `...` or placeholder comments to omit original content.
- Wrap each edit in a ```javascript code block.
""",
    user_prompt=r"""I will give you the bug related information (i.e., Bug Report) for your references, you need to generate patches (SEARCH/REPLACE edits) by looking at all Bug Code Snippets.
1. Read the bug report and view the bug scenario images to describe the scenario and reason about root causes.
2. Inspect Bug Code Snippets to locate code that needs editing.
3. Generate patches for files in Bug Code Snippets. Do not try to fix the reproduce code in the Bug Report.

* Bug Report
'''
{problem_statement}
'''

* Bug Code Snippets
'''
{bug_files_with_context_dict}
'''
""",
    image_placement=(
        "Bug images are prepended before the textual user prompt so visual evidence is seen first."
    ),
)


PATCH_BUILD_REFINEMENT = PromptSpec(
    stage="4.2 Build-error-guided patch refinement",
    purpose=(
        "Refines a candidate patch against the original source and build diagnostics "
        "when the candidate introduces compilation or build errors."
    ),
    source="Code/workflow.py::patch_check_prompt_construction",
    inputs=("bug_files_with_context_dict", "patch", "error_message"),
    system_prompt=r"""Patch is represented as a dictionary. Each edit has a `SEARCH` field containing original code and a `REPLACE` field containing replacement code.

Return a Refined Patch in the same representation. It must preserve the intended bug fix while resolving the compilation errors reported by the build.
""",
    user_prompt=r"""I have generated a patch for a bug file, but this patch introduces compilation errors. Read the original bug file and refine the current patch based on the reported error messages so that it no longer introduces compilation errors.
Finally, output the Refined Patch, which should solve the compilation errors.

* Original Bug Files
'''
{bug_files_with_context_dict}
'''

* Patch
'''
{patch}
'''

* Error Message
'''
{error_message}
'''
""",
)


PATCH_SELECTION = PromptSpec(
    stage="4.3 Candidate-patch selection",
    purpose=(
        "Selects the best candidate patch using the issue report, screenshots, and "
        "correctness, completeness, minimality, and consistency criteria."
    ),
    source="Code/workflow.py::patch_selection_prompt_construction",
    inputs=("problem_statement", "patch_candidates", "bug_images"),
    system_prompt=r"""You are a senior software engineer specializing in debugging and code review.

The user provides a Bug Report, candidate patches, and bug scenario images. Analyze the scenario, carefully review every patch, and select the patch most likely to fix the bug correctly.

Output strictly JSON, with no explanation outside it:
{
    "selected_patch_id": "x/total"
}
""",
    user_prompt=r"""I will provide you with a Bug Report, a set of candidate patches, and bug scenario images.

Select the BEST patch that can correctly fix the bug:
1. Understand the bug from the report and images.
2. Analyze the intent and correctness of each candidate.
3. Compare candidates and select the most likely correct fix.

Evaluation criteria:
- Correctness: Does it truly fix the bug?
- Completeness: Does it handle all necessary cases?
- Minimality: Does it avoid unnecessary changes?
- Consistency: Does it match existing code logic?

IMPORTANT:
- Select only from the provided patch IDs.
- Do not generate a new patch.
- Even if none is perfect, select the best one.

* Bug Report
{problem_statement}

* Candidate Patches (JSON)
{patch_candidates}
""",
    image_placement="Bug images are appended after the textual user prompt.",
)


# ---------------------------------------------------------------------------
# Stage 5: Visual validation and validation-environment preparation
# ---------------------------------------------------------------------------

REPOSITORY_CODE_TEMPLATE_CONSTRUCTION = PromptSpec(
    stage="5.1 Repository-level code-template construction",
    purpose=(
        "Constructs and validates a minimal reusable browser-rendering template the "
        "first time a repository is encountered. The template is built exclusively "
        "from repository structure and official documentation, then cached by "
        "repository identity for later issue-specific reproduction."
    ),
    source="Repository bootstrap stage (reviewer-facing methodology prompt)",
    inputs=("repository_identity", "repository_structure", "official_documentation"),
    system_prompt=r"""You are an expert software engineer responsible for constructing a reusable, minimal, browser-runnable code template for visual validation of a software repository.

This is a repository-level bootstrap task, not an issue-level repair task. You may use ONLY:
1. The repository identity.
2. The repository structure.
3. Official repository documentation supplied by the user.

Strict isolation requirements:
- Do not request, access, or infer any issue report, issue-specific image, candidate patch, pull request, test result, or gold patch.
- Do not encode behavior that is specific to any benchmark instance.
- Do not use implementation knowledge that is absent from the supplied repository structure and official documentation.
- The resulting template must be reusable across multiple issues from the same repository.

Template requirements:
- Create the smallest complete set of files needed to launch a deterministic browser rendering.
- Use the repository-under-test or its local build output. Do not replace it with a CDN or external package version, because later candidate patches must affect the rendered result.
- Preserve only the setup, imports, containers, initialization, and rendering lifecycle required by the documented public API.
- Mark issue-specific insertion points with explicit placeholders such as {{ISSUE_CONTENT}}, {{ISSUE_DATA}}, or {{ISSUE_OPTIONS}}.
- Keep issue-specific content out of the reusable template.
- Provide deterministic install, build, launch, readiness, and capture instructions.
- Prefer a single entry page and the fewest possible dependencies.

Return JSON only, using this schema:
{
  "repository_identity": "owner/repository",
  "template_files": [
    {
      "path": "relative/path/to/file",
      "purpose": "why this file is required",
      "content": "complete file content with explicit issue-specific placeholders"
    }
  ],
  "install_command": "command or empty string",
  "build_command": "command or empty string",
  "run_command": "command that starts the rendering environment",
  "entry_url": "browser URL used for capture",
  "readiness_condition": "deterministic condition indicating that rendering is ready",
  "validation_steps": ["steps used to verify that the base template runs"],
  "assumptions": ["assumptions supported by the supplied official documentation"]
}

Output no markdown and no text outside the JSON object.
""",
    user_prompt=r"""Construct a minimal, reusable browser-rendering template for the repository below.

The template will be validated once and stored under the repository identity. For each future issue, a separate stage will fill its explicit placeholders with content extracted from that issue's description and visual attachment. Candidate patches will then be applied to the repository-under-test and rendered through the instantiated template for visual comparison.

Do not use any issue report, visual attachment, candidate patch, or gold patch in this task.

* Repository Identity
```
{repository_identity}
```

* Repository Structure
```
{repository_structure}
```

* Official Repository Documentation
```markdown
{official_documentation}
```
""",
    notes=(
        "The generated template is validated before caching and is retrieved by "
        "repository identity. Visual validation supports candidate assessment only; "
        "final correctness is determined independently by the official SWE-bench "
        "Multimodal test harness."
    ),
)


VISUAL_REPAIR_FEEDBACK = PromptSpec(
    stage="5.4 Before/after visual repair validation",
    purpose=(
        "Infers the expected rendering from the issue and original screenshots, then "
        "compares the buggy and patched renderings to determine whether the visual "
        "defect was resolved."
    ),
    source="Code/workflow.py::repair_feedback_prompt_construction",
    inputs=("problem_statement", "issue_images", "bug_image", "patch_image"),
    system_prompt=r"""You are a software testing engineer skilled at analyzing bug reports and inferring the correct rendering in order to select a valid candidate patch.

First, analyze the Bug Report and optional scenario images and infer the expected correct behavior. Second, compare the buggy-program image and patched-program image and decide whether the patch solved the scenario (YES or NO).

EXAMPLE OUTPUT:
{
    "bug_analyze": "Analysis of the bug scenario and expected correct rendering.",
    "patch_analyze": "Analysis of whether the patch solved the scenario based on the images.",
    "final_answer": "YES or NO"
}
""",
    user_prompt=r"""[Turn 1: expected-rendering analysis]
I will give you the Issue Report. Analyze the issue scenario and infer what the correct rendering should look like.
1. Read the report and view the available bug-scenario images.
2. Expand the analysis based on the screenshots and infer the expected rendering.

* Bug Report
'''
{problem_statement}
'''

[Turn 2: patch-effect analysis]
I will give you the bug image and patch image. Compare them and analyze whether the current patch resolved the buggy scenario.

* The first picture is the Bug Image.
* The second picture is the Patch Image.
""",
    image_placement=(
        "Turn 1 appends issue images; Turn 2 appends the bug image followed by the patch image."
    ),
)


VALIDATION_HTML_GENERATION = PromptSpec(
    stage="5.3 Issue-specific template instantiation",
    purpose=(
        "Fills a cached repository template with issue-specific content extracted from "
        "the issue context and screenshots, producing a complete page for visual "
        "reproduction and candidate-patch validation."
    ),
    source="Tools/validation/validation_tools.py::generate_html_from_images_with_template_via_llm",
    inputs=("issue_excerpt", "template_html", "bug_images"),
    system_prompt=r"""You generate a complete validation HTML file from bug screenshots and a provided HTML template.
Return JSON only with keys:
{
  "html": "the full final HTML document"
}
Rules:
- Output JSON only, no markdown.
- Use the provided template HTML as the base document.
- Return a complete HTML document, preserving the template structure unless screenshots clearly require changing visible code content or the code-language class.
- Keep existing scripts, styles, runtime initialization, and page bootstrap logic unless screenshots clearly require a different snippet or language.
- Do not omit required script tags or wrapper structure.
- Prefer changing only screenshot-visible code and any directly related language-* class.
- If uncertain, stay close to the template and make the smallest necessary edits.
""",
    user_prompt=r"""Generate the final validation HTML by combining the provided template with the code shown in the bug images.
Issue context:
'''
{issue_excerpt}
'''

Template HTML:
```html
{template_html}
```
""",
    image_placement="Bug images are appended after the textual user prompt.",
)


SCREENSHOT_CODE_EXTRACTION = PromptSpec(
    stage="5.2 Screenshot code extraction",
    purpose=(
        "Extracts raw source text and its language from code-centric screenshots so "
        "the page can be reproduced with an existing syntax-highlighting template."
    ),
    source="Tools/validation/validation_tools.py::extract_code_from_images_via_llm",
    inputs=("issue_excerpt", "bug_images"),
    system_prompt=r"""You extract code snippets from bug screenshots for syntax-highlighting validation.
Return JSON only with keys:
{
  "language": "short language id like sql/javascript/css/plain",
  "code": "the code snippet text shown in the screenshot"
}
Rules:
- Output JSON only, no markdown.
- Return only raw text belonging inside the existing <pre><code>...</code></pre> block.
- Do not return wrapper tags such as <pre>, <code>, <script>, <html>, <head>, or <body>.
- Do not rewrite or invent surrounding template/script content.
- Do not change page initialization, bootstrap logic, or runtime scripts.
- Preserve code line breaks and indentation.
- If uncertain, provide best-effort code and use language `plain`.
""",
    user_prompt=r"""Extract the code snippet visible in these bug images.
Issue context:
'''
{issue_excerpt}
'''
""",
    image_placement="Bug images are appended after the textual user prompt.",
)


# Ordered as the end-to-end pipeline, making the file easy to render or inspect.
PROMPT_CATALOG = (
    REGION_GROUNDING,
    DOCUMENT_LOCALIZATION,
    REPRODUCTION_CODE_GENERATION,
    KEYWORD_GENERATION,
    REPOSITORY_FILE_LOCALIZATION,
    KEY_FILE_LOCALIZATION,
    CLASS_FUNCTION_LOCALIZATION,
    LINE_LEVEL_LOCALIZATION,
    PATCH_GENERATION,
    PATCH_BUILD_REFINEMENT,
    PATCH_SELECTION,
    REPOSITORY_CODE_TEMPLATE_CONSTRUCTION,
    SCREENSHOT_CODE_EXTRACTION,
    VALIDATION_HTML_GENERATION,
    VISUAL_REPAIR_FEEDBACK,
)


def render_catalog() -> str:
    """Return a readable plain-text rendering for reviewers."""

    sections = []
    for index, spec in enumerate(PROMPT_CATALOG, start=1):
        sections.append(
            "\n".join(
                (
                    "=" * 88,
                    f"Prompt {index}: {spec.stage}",
                    f"Purpose: {spec.purpose}",
                    f"Source: {spec.source}",
                    f"Inputs: {', '.join(spec.inputs)}",
                    f"Image placement: {spec.image_placement}",
                    f"Notes: {spec.notes or 'None'}",
                    "-" * 88,
                    "[SYSTEM PROMPT]",
                    spec.system_prompt.strip(),
                    "-" * 88,
                    "[USER PROMPT TEMPLATE]",
                    spec.user_prompt.strip(),
                )
            )
        )
    return "\n\n".join(sections)


if __name__ == "__main__":
    print(render_catalog())
