import os
import io
import json
import base64
import posixpath
import subprocess
from numpy import save
import openai
import re
from itertools import chain
import copy
import time
from difflib import SequenceMatcher
# from file_level_loc_agent import file_level_locating_agent
from collections import Counter
from PIL import Image, ImageChops

try:
    from Code.llm_io_stream import openai_chat, claude_chat, extract_json_from_text
    from Code.RAG import query_bug_file
    from Code.build_cmd import make_build_cmd, make_check_cmd
    from Code.searching_keywords import bug_file_searching_by_keywords
    from Code.multimodal_trans import Image2Code
    from Code.model_utils import is_claude_model, is_openai_compatible_model
except ImportError:
    from llm_io_stream import openai_chat, claude_chat, extract_json_from_text
    from RAG import query_bug_file
    from build_cmd import make_build_cmd, make_check_cmd
    from searching_keywords import bug_file_searching_by_keywords
    from multimodal_trans import Image2Code
    from model_utils import is_claude_model, is_openai_compatible_model

import sys
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
tools_path = os.path.join(project_root, "Tools")
if tools_path not in sys.path:
    sys.path.insert(0, tools_path)

from Tools.preprocess.ground import process_single_image_folder

def has_exactly_one_valid_image(input_dir: str) -> bool:
    if not os.path.isdir(input_dir):
        return False
    files = [
        f for f in os.listdir(input_dir)
        if os.path.isfile(os.path.join(input_dir, f))
    ]
    if len(files) != 1:
        return False
    valid_ext = (".png", ".jpg", ".jpeg")
    return files[0].lower().endswith(valid_ext)


def build_image_payload(path, args):
    image_type = path.split(".")[-1].lower()
    if image_type == "jpg" and 'claude' in args.base_model:
        image_type = "jpeg"

    if any(m in args.base_model for m in ['gpt', 'o4-mini', 'o3']):
        base64_image = encode_image(path)
        return {
            "type":"image_url",
            "image_url":{
                "url":f"data:image/{image_type};base64,{base64_image}"
            }
        }

    elif 'claude' in args.base_model:
        base64_image = encode_image_claude(path)
        return {
            "type":"image",
            "source":{
                "type":"base64",
                "media_type":f"image/{image_type}",
                "data":base64_image
            }
        }
    
def patch_selection_prompt_construction(
    problem_statement,
    patch_candidates,
    image_file_list
):
    import json

    patch_str = json.dumps(patch_candidates, indent=2)

    user_prompt_text = f"""
    I will provide you with a Bug Report, a set of candidate patches, and bug scenario images.

    Your task is to select the BEST patch that can correctly fix the bug.

    You should:
    1. Understand the bug from the Bug Report and Bug Scenario Images;
    2. Analyze the intent and correctness of each candidate patch;
    3. Compare patches and select the one that most likely fixes the bug correctly.

    Evaluation criteria:
    - Correctness: Does the patch truly fix the bug?
    - Completeness: Does it handle all necessary cases?
    - Minimality: Does it avoid unnecessary changes?
    - Consistency: Is it consistent with the existing code logic?

    IMPORTANT:
    - You MUST select from the provided patch IDs
    - Do NOT generate new patches
    - Even if none is perfect, select the best one

    * Bug Report
    {problem_statement}

    * Candidate Patches (JSON)
    {patch_str}
    """

    system_prompt = """
    You are a senior software engineer specializing in debugging and code review.

    The user will provide:
    - A Bug Report
    - A set of candidate patches
    - Bug scenario images

    Your task:
    - Analyze the bug scenario
    - Carefully review each patch
    - Select the BEST patch that most likely fixes the bug correctly

    IMPORTANT:
    - You MUST output strictly in JSON format
    - Do not include any extra explanation outside JSON
    
    Output format:
    {
        "selected_patch_id": "x/total"
    }
    """

    user_prompt = [
        {
            "type": "text",
            "text": user_prompt_text
        }
    ]

    user_prompt = user_prompt + image_file_list

    return system_prompt, user_prompt


from Tools.validation.validation_tools import (
    capture_ui_screenshot_with_retry,
    check_nonempty_file,
    check_png_from_baseline_or_placeholder,
    ensure_chartjs_code_html_for_val,
    ensure_highlightjs_code_html_for_val,
    ensure_markedjs_code_html_for_val,
    ensure_prism_code_html_for_val,
    pick_patch_key_for_index,
)

PREPROCESS_SKIP_REPOS = {
    "highlightjs/highlight.js",
    "prismjs/prism",
    "chartjs/chart.js",
    "markedjs/marked",
}

RUNTIME_WEB_REPOS = {
    "alibaba-fusion/next",
    "carbon-design-system/carbon",
    "eslint/eslint",
    "grommet/grommet",
    "bpmn-io/bpmn-js",
    "prettier/prettier",
    "quarto-dev/quarto-cli",
    "googlechrome/lighthouse",
    "highlightjs/highlight.js",
    "openlayers/openlayers",
    "prismjs/prism",
    "scratchfoundation/scratch-gui",
    "markedjs/marked",
    "chartjs/chart.js",
    "processing/p5.js",
    "Automattic/wp-calypso",
    "diegomura/react-pdf"
}

RUNTIME_WEB_REPOS_WITH_DOCS = {
    "alibaba-fusion/next",
    "carbon-design-system/carbon",
    "eslint/eslint",
    "grommet/grommet",
    "prettier/prettier",
    "quarto-dev/quarto-cli",
    "chartjs/chart.js",
    "processing/p5.js",
}

SPECIAL_PATCH_FORMAT_REPOS = {
    "prismjs/prism",
    "highlightjs/highlight.js",
}

INSTANCE_PREBUILD_REPOS = {
    "prism": {
        "repo_label": "PrismJS/prism",
        "log_prefix": "PrismJS",
    },
    "highlight.js": {
        "repo_label": "highlightjs/highlight.js",
        "log_prefix": "highlightjs",
    },
    "chart.js": {
        "repo_label": "chartjs/Chart.js",
        "log_prefix": "chartjs",
    },
    "marked": {
        "repo_label": "markedjs/marked",
        "log_prefix": "markedjs",
    },
}


def should_skip_preprocess_for_repo(repo: str) -> bool:
    return (repo or "").strip().lower() in PREPROCESS_SKIP_REPOS


def normalize_repo_name(repo: str) -> str:
    return (repo or "").strip().lower()


def get_instance_prebuild_meta(instance_repo_path: str):
    repo_basename = os.path.basename(os.path.normpath(instance_repo_path)).lower()
    return INSTANCE_PREBUILD_REPOS.get(repo_basename)


def run_build_gate(instance_repo_path, build_cmd, logger, log_prefix):
    build_response = "Fail"
    build_result = ""
    try:
        build_result = run_command(f"cd {instance_repo_path} && {build_cmd}")
        if "build" in build_cmd or "pack" in build_cmd:
            logger.info(f"{log_prefix} build: {build_cmd}\n{build_result}")
            if "SyntaxError" in build_result or "TypeError" in build_result or "has Error" in build_result:
                logger.info(f"{log_prefix}: repo build error.")
                if "next" in instance_repo_path:
                    build_response = "Success"
            elif "Local modules not found" in build_result:
                logger.info("Please run npm/pnpm install")
            elif "error Command failed with exit code 1" in build_result:
                logger.info(f"{log_prefix}: build command failed.")
            else:
                build_response = "Success"
        elif "test" in build_cmd:
            logger.info(f"{log_prefix} test: {build_cmd}\n{build_result}")
            if "passing" in build_result:
                build_response = "Success"
            elif "mocha: not found" in build_result:
                logger.info("Please run npm/pnpm install")
            else:
                logger.info("npm run test Error")
        elif "lint" in build_cmd:
            logger.info(f"{log_prefix} lint: {build_cmd}\n{build_result}")
            if "has Error" in build_result:
                logger.info("Lint error")
            else:
                build_response = "Success"
        else:
            build_response = "Success"
            logger.info(f"{log_prefix} no test/build gate configured: {build_cmd}")
    except Exception:
        logger.info(f"{log_prefix}: repo build/test error.")
    return build_response, build_result


def run_instance_prebuild_if_needed(instance_id, instance_repo_path, args, logger):
    prebuild_meta = get_instance_prebuild_meta(instance_repo_path)
    args.instance_prebuild_done = False
    args.instance_prebuild_ok = None
    args.instance_prebuild_cmd = ""
    if not prebuild_meta:
        return

    build_cmd = make_build_cmd(instance_repo_path)
    log_prefix = f"[instance {instance_id}] {prebuild_meta['log_prefix']} prebuild"
    logger.info(f"{log_prefix}: start. cmd={build_cmd}")
    build_response, _ = run_build_gate(instance_repo_path, build_cmd, logger, log_prefix)
    logger.info(f"Wait {args.wait_time_after_build} S after instance prebuild...")
    time.sleep(int(args.wait_time_after_build))
    args.instance_prebuild_done = True
    args.instance_prebuild_ok = build_response == "Success"
    args.instance_prebuild_cmd = build_cmd
    if not args.instance_prebuild_ok:
        raise RuntimeError(f"{log_prefix}: failed")


def read_file(file_path):
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as file:
        return file.read()

def read_file_lines(file_path):
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as file:
        return file.readlines()

def save_file(file_path, data, encoding='utf-8'):
    """Save data to a file with the specified encoding."""
    try:
        with open(file_path, 'w', encoding=encoding) as file:
            file.write(data)
    except UnicodeEncodeError as e:
        print(f"Unicode encode error: {e}")
        return "Unicode encode error"

def read_json_file(file_path):
    with open(file_path, 'r') as json_file:
        return json.load(json_file)

def contains_chinese(obj):
    """check if contrain chinese string"""
    if isinstance(obj, str):
        return bool(re.search(r'[\u4e00-\u9fff]', obj))  
    elif isinstance(obj, dict):
        return any(contains_chinese(value) for value in obj.values())  
    elif isinstance(obj, (list, tuple, set)):
        return any(contains_chinese(item) for item in obj)  
    return False  

def save_json_file(file_path, data):
    """save JSON, if contrain chinese string >>> ensure_ascii=False"""
    contains_cn = contains_chinese(data)  
    with open(file_path, 'w', encoding='utf-8') as json_file:
        json.dump(data, json_file, indent=4, ensure_ascii=not contains_cn)  


def parse_javascript_file(file_path):
    """Use Node.js script to parse a JS file and extract classes and functions."""
    try:
        if 'markedjs' in file_path:
            # because markedjs use some special js coding rules, so we need change parse script
            parse_scripe_file = 'parse_js_new.js'
        elif 'Automattic' in file_path or 'highlightjs' in file_path or 'carbon-design-system' in file_path:
            parse_scripe_file = 'parse_js_old_copy.js'
        else:
            parse_scripe_file = 'parse_js_old.js'
        result = subprocess.run(
            ["node", parse_scripe_file, file_path],
            capture_output=True,
            text=True,
            check=True
        )
        parsed_data = json.loads(result.stdout)
        # Specify the encoding explicitly when reading the file
        with open(file_path, 'r', encoding='utf-8') as file:
            file_lines = file.read().splitlines()
        return parsed_data["classes"], parsed_data["functions"], file_lines
    except subprocess.CalledProcessError as e:
        # print(f"Error parsing JS file {file_path}: {e.stderr}")
        with open(file_path, 'r', encoding='utf-8') as file:
            file_lines = file.read().splitlines()
        return [], [], file_lines
    except UnicodeDecodeError as e:
        print(f"Unicode decode error in file {file_path}: {e}")
        return [], [], []

def create_structure(directory_path, args):
    """Create the structure of the repository directory by parsing JS files."""
    structure = {}

    EXCLUDED_DIRS = {
    "node_modules", "test", "tests", "dist", "build", "out", "auto",
    "coverage", "scripts", "example", "examples", ".git", ".github", "assets", 
    ".yarn", ".husky", "e2e", "docs"
    }

    for root, dirs, files in os.walk(directory_path):
        if args.File_Sort == 'True':
            # sort dirs
            dirs[:] = sorted([d for d in dirs if d not in EXCLUDED_DIRS]) # ignor non-src dirs
            # sort files
            files = sorted(files)
        else:
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS] # ignor non-src dirs

        # Calculate relative path from root
        relative_root = os.path.relpath(root, directory_path)
        path_parts = relative_root.split(os.sep) if relative_root != '.' else []

        # Navigate to the correct level in the structure dictionary
        curr_struct = structure
        for part in path_parts:
            if part not in curr_struct:
                curr_struct[part] = {}
            curr_struct = curr_struct[part]

        # Add files to the current structure level
        for file_name in files:
            if file_name.endswith(".js") or file_name.endswith(".js.snap") or file_name.endswith(".jsx") or file_name.endswith(".ts") or file_name.endswith(".tsx"):
                file_path = os.path.join(root, file_name)
                class_info, function_names, file_lines = parse_javascript_file(file_path)
                curr_struct[file_name] = {
                    "classes": class_info,
                    "functions": function_names,
                    "text": file_lines,
                }
            elif (file_name.endswith(".lua") or file_name.endswith(".R")) and 'quarto-dev' in args.output_dir:
                file_path = os.path.join(root, file_name)
                with open(file_path, 'r', encoding='utf-8') as file:
                    file_lines = file.read().splitlines()
                curr_struct[file_name] = {
                    "classes": '',
                    "functions": '',
                    "text": file_lines,
                }
            elif file_name.endswith(".json") and 'scratchfoundation' in args.output_dir:
                file_path = os.path.join(root, file_name)
                with open(file_path, 'r', encoding='utf-8') as file:
                    file_lines = file.read().splitlines()
                curr_struct[file_name] = {
                    "classes": '',
                    "functions": '',
                    "text": file_lines,
                }
            elif file_name.endswith(".scss") and 'alibaba-fusion' in args.output_dir:
                file_path = os.path.join(root, file_name)
                with open(file_path, 'r', encoding='utf-8') as file:
                    file_lines = file.read().splitlines()
                curr_struct[file_name] = {
                    "classes": '',
                    "functions": '',
                    "text": file_lines,
                }
            elif file_name.endswith(".md") and 'alibaba-fusion' in args.output_dir:
                file_path = os.path.join(root, file_name)
                with open(file_path, 'r', encoding='utf-8') as file:
                    file_lines = file.read().splitlines()
                curr_struct[file_name] = {
                    "classes": '',
                    "functions": '',
                    "text": file_lines,
                }
            else:
                curr_struct[file_name] = {}

    return structure

def show_project_structure(structure, args, spacing=0) -> str:
    """pprint the project structure"""

    pp_string = ""

    # sort items
    sorted_items = sorted(structure.items(), key=lambda item: (
        # 0 if '.' in item[0] else 1,  # before file (0), after dir (1)
        0 if '.' not in item[0] else 1, # before dir, after file
        item[0].lower()             # sort item name
    ))

    # for key, value in structure.items():
    for key, value in sorted_items:
        if "." in key:
            if (".json" in key and 'scratchfoundation' in args.output_dir):
                pass
            if ".json" in key:
                continue
            if ".js" in key or ".ts" in key or ".js.snap" in key or ".jsx" in key or ".tsx" in key or (".lua" in key and 'quarto-dev' in args.output_dir) or (".R" in key and 'quarto-dev' in args.output_dir) or (".scss" in key and 'alibaba-fusion' in args.output_dir) or (".md" in key and 'alibaba-fusion' in args.output_dir):
                pass
            else:
                continue  # skip none js files
        if "." in key:
            pp_string += " " * spacing + str(key) + "\n"
        else:
            pp_string += " " * spacing + str(key) + "/" + "\n"
        if "classes" not in value:
            pp_string += show_project_structure(value, args, spacing + 4)

    return pp_string

def repair_feedback_prompt_construction(bug_image_list, fix_image_list, problem_statement, image_file_list):
    analyze_bug_scenarios_prompt = f"""
I will give you the Issue Report, you need to analyze the issue scenario to infer what the correct rendering of the effect looks like.
    1. Read the bug report and view the bug scenarion images (if images are available) to analyze the bug scenarion; 
    2. Please expand your analysis based on the screenshot of the bug scenario and infer what the correct rendering of the effect looks like.
    
* Bug Report
'''
{problem_statement}
'''
"""
    
    analyze_patch_effects_prompt = f"""
I will give you the bug image and patch image, you need to analyze whether the current patch resolved the buggy scenario by comparing the bug and patch images.

* The first picture is the Bug Image
* The second picture is the Patch Image

"""
    
    system_prompt = """
You are a software testing engineer, you excel at analyzing bug reports and infering what the correct rendering of the effect looks like, in order to help select the valid patch from patch candidates.
First, the user will provide the Bug Report (may attach the issue scenario images), please analyze the issue scenario and infer the correct except behaviors of bug scenario. 
Second, the user will provide the bug image of buggy program and the patch image of patched program, please analyze whether the current patch has solved the bug scenarion and output your answer (YES: solved this bug; NO: unsolved this bug.).

EXAMPLE OUTPUT:
{   
    "bug_analyze": "Analyze the bug scenario and infering what the correct rendering of the effect looks like.",
    "patch_analyze": "Analyze whether the current patch has solved the bug scenario by viewing the bug and patch images.",
    "final_answer": "YES or NO"
}
"""
    user_prompt = {}

    user_prompt['bug_analyze'] = [
        {"type": "text", "text": analyze_bug_scenarios_prompt}
    ] + image_file_list

    user_prompt['fix_analyze'] = [
        {"type": "text", "text": analyze_patch_effects_prompt}
    ] + bug_image_list + fix_image_list

    return system_prompt, user_prompt


def keywords_generation_prompt_construction(problem_statement, image_file_list):
    generate_bug_keywords_prompt = f"""
I will give you the bug related information (i.e., Bug Report), you need to analyze what valid information can be obtained from the bug scenario images to help locate the bug files.
    1. Read the bug report and view the bug scenarion images (if images are available) to analyze the bug scenarion; 
    2. Please expand your analysis based on the screenshot of the bug scenario and provide some keywords that may appear in the bug code for bug file search.
    
* Bug Report
'''
{problem_statement}
'''

"""
    
    system_prompt = """
You are a senior software engineer, you excel at analyzing bug reports and finding key information from bug scenario images to help locate bug files.
The user will provide the Bug Report (may attach the bug images). Please analyze the bug scenario images, then return some keywords may appear in the bug code for bug file search and explain why these keywords may in bug files. 

EXAMPLE INPUT: 

* Bug Report
'''
problem_statement
'''

* Bug Scenario Images
'''
image
'''

EXAMPLE OUTPUT:
{   
    "bug_analyze": "Analyze the bug scenario and find key information by looking the bug images.",
    "bug_keywords": ["keyword_1", "keyword_2", "keyword_3", "keyword_4", "keyword_5", "keyword_6", "keyword_7", "keyword_8", "keyword_9", "keyword_10", "keyword_11", "keyword_...", "keyword_n"],
    "explanation": "Explanation of why these keywords may appear in the bug files."
}
"""

    
    user_prompt = [
        {
            "type": "text",
            "text": generate_bug_keywords_prompt,
        }
    ]
    user_prompt = user_prompt + image_file_list
    return system_prompt, user_prompt


def all_file_locate_prompt_construction(repo_structure, problem_statement, image_file_list, args):
    obtain_relevant_files_prompt = f"""
I will give you the bug related information (i.e., Bug Report) for your references, you need to find all suspicious bug related files in the code Repo.
    1. Read the bug report and view the bug scenarion images (if images are available) to describe and analyze the bug scenario images; 
    2. Look the Repository Structure to find bug related files that would need to edit to fix the problem; 
    3. Save all bug related files and explain why these files are bug related .
    
* Bug Report
'''
{problem_statement}
'''

* Repository Structure
'''
{repo_structure}
'''

"""
    
    system_prompt = """
The user will provide the Bug Report (may attach the bug images) and Repository Structure. Please describe the bug scenario images, then return all bug related files and explain why these files are bug related. 

EXAMPLE INPUT: 

* Bug Report
'''
problem_statement
'''

* Repository Structure
'''
repo_structure
'''

* Bug Scenario Images
'''
image
'''

EXAMPLE OUTPUT:
{   
    "bug_scenario": "Description of the bug scenario.",
    "bug_files": ["src/bug_file1.js", "build/bug_file2.js", "components/bug_file3.js"],
    "explanation": "Explanation of why these files are bug related."
}
"""

    if is_claude_model(args.base_model):
        system_prompt = """
The user will provide the Bug Report (may attach the bug images) and Repository Structure. Please describe the bug scenario images, then return all bug related files and explain why these files are bug related. 

Note that you need analyze this feedback and output in JSON format with keys: "bug_scenario" (str), "bug_files" (list), and "explanation" (str).
EXAMPLE INPUT: 

* Bug Report
'''
problem_statement
'''

* Repository Structure
'''
repo_structure
'''

* Bug Scenario Images
'''
image
'''

EXAMPLE OUTPUT:
{   
    "bug_scenario": "Description of the bug scenario.", 
    "bug_files": ["src/bug_file1.js", "build/bug_file2.js", "components/bug_file3.js"], 
    "explanation": "Explanation of why these files are bug related."
}
"""
    
    user_prompt = [
        {
            "type": "text",
            "text": obtain_relevant_files_prompt,
        }
    ]
    user_prompt = user_prompt + image_file_list
    return system_prompt, user_prompt

def key_file_locate_prompt_construction(compressed_bug_files, problem_statement, image_file_list, max_candidate_bug_files, args):
    if len([key for key in compressed_bug_files.keys()]) > max_candidate_bug_files:
        files_limit_token = f' (at most {max_candidate_bug_files} key bug files) '
    else:
        files_limit_token = ' '
    
    obtain_relevant_files_prompt = f"""
I will give you the bug related information (i.e., Bug Report) for your references, you need to find key bug files{files_limit_token}by looking all Compressed Bug Files.
    1. Read the bug report and view the bug scenarion images (if images are available) to describe the bug scenario images; 
    2. Look all compressed bug files to find key bug files that would need to edit to fix the problem; 
    3. Save all key bug files{files_limit_token}and explain why these files are bug files.
    
* Bug Report
'''
{problem_statement}
'''

* Compressed Bug Files
'''
{compressed_bug_files}
'''

"""
    
    system_prompt = """
The user will provide the Bug Report (may attach the bug images) and Compressed Bug Files. Please describe the bug scenario images, then return key bug files and explain why these files are bug files. 
Explain of Compressed Bug Files: Directly providing the complete context of all files can be large. As such, we build a compressed format of each file that contains the list of class, function, or variable declarations. We refer to this format as the skeleton format. In the skeleton format, we provide only the headers of the classes and functions in the file. For classes, we further include any class fields and methods (signatures only). Additionally, we also keep comments in the class and module level to provide further information. Compared to providing the entire file context to the model, the skeleton format is a much more concise representation, especially when the file contains thousands of lines, making it impractical/costly to process all at once with existing LLMs.

EXAMPLE INPUT: 

* Bug Report
'''
problem_statement
'''

* Compressed Bug Files
'''
compressed_bug_files
'''

* Bug Scenario Images
'''
image
'''

EXAMPLE OUTPUT:
{   
    "bug_scenario": "Description of the bug scenario.",
    "bug_files": ["src/bug_file1.js", "build/bug_file2.js", "components/bug_file3.js"],
    "explanation": "Explanation of why these files are bug files."
}
"""

    if is_claude_model(args.base_model):
        system_prompt = """
The user will provide the Bug Report (may attach the bug images) and Compressed Bug Files. Please describe the bug scenario images, then return key bug files and explain why these files are bug files. 
Explain of Compressed Bug Files: Directly providing the complete context of all files can be large. As such, we build a compressed format of each file that contains the list of class, function, or variable declarations. We refer to this format as the skeleton format. In the skeleton format, we provide only the headers of the classes and functions in the file. For classes, we further include any class fields and methods (signatures only). Additionally, we also keep comments in the class and module level to provide further information. Compared to providing the entire file context to the model, the skeleton format is a much more concise representation, especially when the file contains thousands of lines, making it impractical/costly to process all at once with existing LLMs.

Note that you need analyze this feedback and output in JSON format with keys: "bug_scenario" (str), "bug_files" (list), and "explanation" (str).

EXAMPLE OUTPUT:
{   
    "bug_scenario": "Description of the bug scenario.",
    "bug_files": ["src/bug_file1.js", "build/bug_file2.js", "components/bug_file3.js"],
    "explanation": "Explanation of why these files are bug files."
}
"""

    
    user_prompt = [
        {
            "type": "text",
            "text": obtain_relevant_files_prompt,
        }
    ]
    user_prompt = user_prompt + image_file_list
    return system_prompt, user_prompt

def key_class_function_locate_prompt_construction(compressed_key_bug_files, problem_statement, image_file_list, args):
    obtain_relevant_files_prompt = f"""
I will give you the bug related information (i.e., Bug Report) for your references, you need to find key bug classes/functions by looking all Compressed Bug Files.
    1. Read the bug report and view the bug scenarion images (if images are available) to describe the bug scenario images; 
    2. Look all compressed bug files to find key bug classes or functions that would need to edit to fix the problem; 
    3. Save all key bug classes or functions and explain why these classes or functions are bug related elements.
    
* Bug Report
'''
{problem_statement}
'''

* Compressed Bug Files
'''
{compressed_key_bug_files}
'''

"""
    
    system_prompt = """
The user will provide the Bug Report (may attach the bug images) and Compressed Bug Files. Please describe the bug scenario images, then return key bug classes/functions and explain why these classes/functions are bug related elements. 
Explain of Compressed Bug Files: Directly providing the complete context of all files can be large. As such, we build a compressed format of each file that contains the list of class, function, or variable declarations. We refer to this format as the skeleton format. In the skeleton format, we provide only the headers of the classes and functions in the file. For classes, we further include any class fields and methods (signatures only). Additionally, we also keep comments in the class and module level to provide further information. Compared to providing the entire file context to the model, the skeleton format is a much more concise representation, especially when the file contains thousands of lines, making it impractical/costly to process all at once with existing LLMs.
Note that if bug code snippets not in class/function or there are not class/function, only output the bug_file_name is enough.

EXAMPLE INPUT: 

* Bug Report
'''
problem_statement
'''

* Compressed Bug Files
'''
compressed_bug_files
'''

* Bug Scenario Images
'''
image
'''

EXAMPLE OUTPUT:
{   
    "bug_scenario": "Description of the bug scenario.",
    "bug_classes": ["bug_file_name//class_name_1", "bug_file_name//class_name_2", "bug_file_name//class_name_3"],
    "bug_functions": ["bug_file_name//function_name_1", "bug_file_name//function_name_2", "bug_file_name//function_name_3"],
    "explanation": "Explanation of why these classes/functions are bug code elements."
}
"""

    if is_claude_model(args.base_model):
        system_prompt = """
The user will provide the Bug Report (may attach the bug images) and Compressed Bug Files. Please describe the bug scenario images, then return key bug classes/functions and explain why these classes/functions are bug related elements. 
Explain of Compressed Bug Files: Directly providing the complete context of all files can be large. As such, we build a compressed format of each file that contains the list of class, function, or variable declarations. We refer to this format as the skeleton format. In the skeleton format, we provide only the headers of the classes and functions in the file. For classes, we further include any class fields and methods (signatures only). Additionally, we also keep comments in the class and module level to provide further information. Compared to providing the entire file context to the model, the skeleton format is a much more concise representation, especially when the file contains thousands of lines, making it impractical/costly to process all at once with existing LLMs.
If bug code snippets not in class/function or there are not class/function, only output the bug_file_name is enough.

Note that you need analyze this feedback and output in JSON format with keys: "bug_scenario" (str), "bug_classes" (list), "bug_functions" (list), and "explanation" (str).

EXAMPLE OUTPUT:
{   
    "bug_scenario": "Description of the bug scenario.",
    "bug_classes": ["bug_file_name//class_name_1", "bug_file_name//class_name_2", "bug_file_name//class_name_3"],
    "bug_functions": ["bug_file_name//function_name_1", "bug_file_name//function_name_2", "bug_file_name//function_name_3"],
    "explanation": "Explanation of why these classes/functions are bug code elements."
}
"""
    
    user_prompt = [
        {
            "type": "text",
            "text": obtain_relevant_files_prompt,
        }
    ]
    user_prompt = user_prompt + image_file_list
    return system_prompt, user_prompt

def line_level_class_function_locate_prompt_construction(bug_files_with_context_dict, problem_statement, image_file_list):
    obtain_relevant_files_prompt = f"""
I will give you the bug related information (i.e., Bug Report) for your references, you need to find line-level bug locations by looking all Bug Files.
    1. Read the bug report and view the bug scenarion images (if images are available) to describe the bug scenario images; 
    2. Look all bug files to find key line-level bug locations that would need to edit to fix the problem; 
    3. Save all line numbers of bug locations and explain why code lines are bug related elements.
    
* Bug Report
'''
{problem_statement}
'''

* Bug Files
'''
{bug_files_with_context_dict}
'''

"""
    
    system_prompt = """
The user will provide the Bug Report (may attach the bug images) and Bug Files. Please describe the bug scenario images, then return key line-level bug locations and explain why these code lines are bug related elements. 
Note that we will use dict format to record Bug Files, the dict's key is the bug file path, and the dict's value is the bug code snippets with the line number.
Note that you need output the bug line number of bug files. If there is no bug locations for the current bug file, only output the empty list.

EXAMPLE INPUT: 

* Bug Report
'''
problem_statement
'''

* Bug Files
'''
{
    "bug_file_path_1": bug_code_snippets,
    "bug_file_path_2": bug_code_snippets,
    "bug_file_path_3": bug_code_snippets
}
'''

* Bug Scenario Images
'''
image
'''

EXAMPLE OUTPUT:
{   
    "bug_locations": {
        "bug_file_path_1": [100, 101, 102, 103],
        "bug_file_path_2": [22, 106, 139],
        "bug_file_path_3": []
    },
    "explanation": "Explanation of why these bug lines are bug code elements."
}
"""

    
    user_prompt = [
        {
            "type": "text",
            "text": obtain_relevant_files_prompt,
        }
    ]
    user_prompt = user_prompt + image_file_list
    return system_prompt, user_prompt


def patch_generation_prompt_construction(bug_files_with_context_dict, problem_statement, image_file_list):
    generate_patches_prompt = f"""
I will give you the bug related information (i.e., Bug Report) for your references, you need to generate patches (*SEARCH/REPLACE* edits) by looking all Bug Code Snippets.
    1. Read the bug report and view the bug scenarion images (if images are available) to describe the bug scenario images and reasonning the bug root causes; 
    2. Look all bug snippets files in "* Bug Code Snippets" to analyze and locate bug locations that would need to edit to fix the problem; 
    3. Generate patches for bug files in "* Bug Code Snippets" to fix the current bug. (!!! Note that don't try to fix the reproduce code in Bug Report!!!)
    
 
* Bug Report
'''
{problem_statement}
'''

* Bug Code Snippets
'''
{bug_files_with_context_dict}
'''

"""
    
    system_prompt = """
The user will provide the Bug Report (may attach the bug images) and Bug Code Snippets. Please analyze the bug scenario images to infer possible bug root cause, then locate the bug locations and generate patches for "* Bug Code Snippets". 
Explain of Bug Code Snippets: We'll provide the key bug code snippets from the bug file, for the rest of the code sections we use ... to omit.
Note that we will use dict format to record Bug Code Snippets, the dict's key is the bug file path, and the dict's value is the bug code snippets.

EXAMPLE INPUT: 

* Bug Report
'''
problem_statement
'''

* Bug Code Snippets
'''
{
    "bug_file_path 1": "bug_code_snippets",
    "bug_file_path 2": "bug_code_snippets",
    "bug_file_path 3": "bug_code_snippets"
}
'''

* Bug Scenario Images
'''
image
'''


Please first localize the bug based on the issue statement, and then generate *SEARCH/REPLACE* edits (i.e., patches) to fix the issue.

Every *SEARCH/REPLACE* edit must use this format:
1. The bug file path (Please give the specific bug file path in "* Bug Code Snippets", e.g., src/components/Image/ImageSearch.js. Not the reproduce code file.)
2. The start of search block: <<<<<<< SEARCH
3. A contiguous chunk of lines to search for in the existing source code
4. The dividing line: =======
5. The lines to replace into the source code
6. The end of the replace block: >>>>>>> REPLACE

EXAMPLE OUTPUT:
(Here is a *SEARCH/REPLACE* edit example)

```javascript
### bug_file_path 2
<<<<<<< SEARCH
from flask import Flask
from transformer import generate
from transformer import train
=======
import math
from flask import Flask
from transformer import generate
from transformer import train
>>>>>>> REPLACE
```

Please note that the *SEARCH/REPLACE* edit REQUIRES PROPER INDENTATION. If you would like to add the line '        print(x)', you must fully write that out, with all those spaces before the code!
Please note that you must provide sufficient *SEARCH* edit context (No less than 3 lines of code) to ensure that the code location can be successfully searched!
Please note that you can't use "/* ~~~~~~~~~~~~~~~~~~~~ */" or "..." to alter and ignore the original code content, you must keep the original code format and content in the *SEARCH/REPLACE* edit!
Wrap the *SEARCH/REPLACE* edit in blocks ```javascript...```.


"""


# EXAMPLE OUTPUT:
# {
#     "bug_file_path 1": "*SEARCH/REPLACE* edits",
#     "bug_file_path 2": "*SEARCH/REPLACE* edits"
# }
    
    user_prompt = [
        {
            "type": "text",
            "text": generate_patches_prompt,
        }
    ]
    # user_prompt = user_prompt + image_file_list
    user_prompt = image_file_list + user_prompt
    return system_prompt, user_prompt    


def patch_check_prompt_construction(bug_files_with_context_dict, error_message, patch):
    generate_patches_prompt = f"""
I have generated a patch for a bug file, but this patch introduces compilation errors. Could you please read the original bug file and help me refine the current patch to avoid introducing compilation errors based on the error messages reported after patching.
Finally, you need to output the Refined Patch, which should solve the compilation errors.

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

"""
    
    system_prompt = """
Note that we will use dict format to record Patch, the dict's key 'SEARCH' is the bug code, 'REPLACE' is the replaced fix code.

EXAMPLE INPUT: 

* Original Bug Files
'''
bug_files_content
'''

* Patch
'''
"packages/react/src/bug_file.js": [
    {
        "SEARCH": [
            "const mergeRefs = (keyword) => {"
        ],
        "REPLACE": [
            "const mergeRefs = forwardRef((keyword, ref) => {"
        ]
    },
    {
        "SEARCH": [
            "ref={mergeRefs(textInput)}"
        ],
        "REPLACE": [
            "ref={mergeRefs(ref, textInput)}"
        ]
    }
]
'''

* Error Message
'''
error_message
'''


EXAMPLE OUTPUT:

* Refined Patch
'''
"packages/react/src/bug_file.js": [
    {
        "SEARCH": [
            "const mergeRefs = (keyword) => {"
        ],
        "REPLACE": [
            "const mergeRefs = forwardRef((keyword, ref) => {"
        ]
    },
    {
        "SEARCH": [
            "ref={mergeRefs(textInput)}"
        ],
        "REPLACE": [
            "ref={mergeRefs(ref, textInput)}"
        ]
    },
    {
        "SEARCH": [
            "return result",
            "};"
        ],
        "REPLACE": [
            "const context = getthecontext();",
            ")};"
        ]
    }
]
'''

"""

    
    user_prompt = [
        {
            "type": "text",
            "text": generate_patches_prompt,
        }
    ]

    user_prompt = user_prompt
    return system_prompt, user_prompt    

MAX_IMAGE_SIZE = 4 * 1024 * 1024  # 4MB

def encode_image_claude(image_path):
    # print('Pre-processing Claude Images...')
    def compress_image(image_path, format):
        img = Image.open(image_path)
        buffer = io.BytesIO()
        for quality in range(95, 10, -5):
            buffer.seek(0)
            buffer.truncate()
            img.save(buffer, format=format, quality=quality, optimize=True)
            if buffer.tell() <= MAX_IMAGE_SIZE:
                return buffer.getvalue()
        raise ValueError(f"Error: {image_path}")

    # get the image size
    file_size = os.path.getsize(image_path)
    ext = image_path.split('.')[-1].lower()
    img_format = 'JPEG' if ext in ['jpg', 'jpeg'] else ext.upper()

    if file_size > MAX_IMAGE_SIZE:
        # print(f"preprocessing image: {image_path}")
        compressed_data = compress_image(image_path, img_format)
        return base64.b64encode(compressed_data).decode("utf-8")
    else:
        # print(f"IMAGE_SIZE = {file_size}")
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")

# Function to encode the image
def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")
    
def get_bug_scenarion_images(image_sets_path, args):
    # print(image_sets_path)
    image_file_list = []
    if os.path.isdir(image_sets_path): # is dir
        image_file_list = os.listdir(image_sets_path)
    else: # not dir, is file path
        image_file_list = [image_sets_path]
    # print(image_file_list)
    
    if image_file_list is not None:
        for index, image_file in enumerate(image_file_list):
            if os.path.isdir(image_sets_path):
                image_path = os.path.join(image_sets_path, image_file) # Path to your image
            else:
                image_path = image_file
            
            image_type = 'png'
            if 'png' in image_path:
                image_type = 'png'
            elif 'jpg' in image_path:
                image_type = 'jpg'
                if is_claude_model(args.base_model): image_type = 'jpeg'
            elif 'jpeg' in image_path:
                image_type = 'jpeg'
            elif 'gif' in image_path:
                image_type = 'gif'
            else:
                image_type = image_path.split('.')[-1].strip()
            
            if is_openai_compatible_model(args.base_model):
                base64_image = encode_image(image_path) # Getting the Base64 string
                image_file_list[index] = {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/{image_type};base64,{base64_image}"},
                }
            elif is_claude_model(args.base_model):
                base64_image = encode_image_claude(image_path)
                image_file_list[index] = {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": f"image/{image_type}",
                        "data": base64_image,
                    },
                }

    return image_file_list


##################################################################################################

def compressing_javascript_file(bug_file_structure_dict, save_variable_declaration):
    """
    Preprocess a JavaScript file to create a compressed skeleton format.
    
    Args:
        file_content (str): The content of the JavaScript file.
        
    Returns:
        str: A string representing the skeleton format of the JavaScript file.
    """
    # print(bug_file_structure_dict)
    classes_list = bug_file_structure_dict['classes']
    functions_list = bug_file_structure_dict['functions']
    text_list = bug_file_structure_dict['text']
    

    
    compressed_text_list = ['' for i in text_list]

    if classes_list != []:
        for class_dict in classes_list:
            class_name = class_dict['name']
            class_start_line = class_dict['start_line']
            class_end_line = class_dict['end_line']

            compressed_text_list[class_start_line-1] = text_list[class_start_line-1]
            compressed_text_list[class_end_line-1] = text_list[class_end_line-1]

            methods_list = class_dict['methods']

            if methods_list != []:
                for method_dict in methods_list:
                    method_name = method_dict['name']
                    method_start_line = method_dict['start_line']
                    method_end_line = method_dict['end_line']

                    compressed_text_list[method_start_line-1] = text_list[method_start_line-1]
                    compressed_text_list[method_end_line-1] = text_list[method_end_line-1]

    if functions_list != []:
        for function_dict in functions_list:
            function_name = function_dict['name']
            function_start_line = function_dict['start_line']
            function_end_line = function_dict['end_line']

            compressed_text_list[function_start_line-1] = text_list[function_start_line-1]
            compressed_text_list[function_end_line-1] = text_list[function_end_line-1]

            line_no = function_start_line-1
            loop_times = 0
            while 1:
                if loop_times > 20: break
                if len(text_list[line_no].strip()) > 0:
                    if text_list[line_no].strip()[-1] == '{' or text_list[line_no].strip()[-1] == '}' or text_list[line_no].strip()[-2:] == '};':
                        break
                    else:
                        loop_times += 1
                        line_no += 1
                        compressed_text_list[line_no] = text_list[line_no]
                else:
                    break
                    
    
    # print(compressed_text_list)

    for index, text in enumerate(text_list):

        text_strip = text.strip()
        # Check if the text is a JavaScript code comment, and save it if it is
        if text_strip.startswith('//') or text_strip.startswith('/*') or text_strip.endswith('*/') or text_strip.startswith('*') or text_strip.startswith('import') or text_strip.startswith('export'):
            compressed_text_list[index] = text

        # Check if the text is a variable declaration, please save it
        
        if save_variable_declaration is True:
            if text_strip.startswith('let ') or text_strip.startswith('const ') or text_strip.startswith('var '):
                compressed_text_list[index] = text

    # print(compressed_text_list)

    # Remove extra blank lines, leaving only one blank line if there are more than two consecutive blank lines.
    compressed_text_list = [line for index, line in enumerate(compressed_text_list) if not (line == '' and (index > 0 and compressed_text_list[index - 1] == '') and (index > 1 and compressed_text_list[index - 2] == ''))]

    for index, compressed_text in enumerate(compressed_text_list):
        compressed_text_list[index] = compressed_text+'\n'
            
    return compressed_text_list


def get_bug_file_structure_dict(bug_file, repo_structure_dict):
    bug_file_structure_dict = repo_structure_dict
    for key in bug_file.split('/'):

        bug_file_structure_dict = bug_file_structure_dict[key]

    return bug_file_structure_dict


def remove_blank_lines(bug_files_with_context_dict, blank_str, with_line_number):
    for file_path, lines in bug_files_with_context_dict.items():
        # Retain only one blank line if there are multiple consecutive blank lines
        new_lines = []
        for index, line in enumerate(lines):
            if line.strip() == blank_str:
                if not new_lines or new_lines[-1].strip() != blank_str:
                    new_lines.append(line)
            else:
                if with_line_number is True:
                    new_lines.append(f'{index+1}: {line}')
                else:
                    new_lines.append(line)
        bug_files_with_context_dict[file_path] = new_lines
    return bug_files_with_context_dict


def split_js_code_safely(code_str):
    lines = []
    current_line = ""

    inside_string = False  
    string_delimiter = ""  
    escape = False  
    inside_regex = False  

    i = 0
    while i < len(code_str):
        char = code_str[i]

        if inside_string:
            if char == "\\":
                escape = not escape  
            elif char == string_delimiter and not escape:
                inside_string = False  
            else:
                escape = False  
            current_line += char

        elif inside_regex:
            if char == "/" and not escape:
                inside_regex = False 
            elif char == "\\":
                escape = not escape
            else:
                escape = False
            current_line += char

        else:
            if char in "\"'":
                inside_string = True
                string_delimiter = char
                escape = False

            elif char == "/" and i > 0 and code_str[i - 1] in " =(,{[":
                inside_regex = True

            elif char == "\n":
                lines.append(current_line)
                current_line = ""
                i += 1
                continue

            current_line += char

        i += 1

    if current_line:
        lines.append(current_line)

    return [line.rstrip() for line in lines]  
               


def extract_patch_edits(patch_content, repo, check_pattern):
    # Initialize a dictionary to store extracted patches
    extracted_edits = {}

    # Regular expression to match the patch edit blocks
    patch_regex = r"```javascript\n### (?P<file_path>.+?)\n(?P<patch_edits>.*?)\n```"
    # Find all matches in the patch content
    matches = re.finditer(patch_regex, patch_content, re.DOTALL)

    

    if 'javascript\n###' not in patch_content:
        # logger.info("extract error. please check patch_content")
        patch_regex = r"```javascript\n(?P<file_path>.+?)\n(?P<patch_edits>.*?)(?=\n```)"
        matches = list(re.finditer(patch_regex, patch_content, re.DOTALL))
    

    for match in matches:
        file_path = match.group("file_path").strip()
        patch_edits = match.group("patch_edits").strip()
        
        # Initialize a list to store multiple edits for the same file
        if file_path not in extracted_edits.keys():
            extracted_edits[file_path] = []
        
        # Regular expression to match each SEARCH/REPLACE block
        edit_regex = r"<<<<<<< SEARCH\n(?P<search>.*?)\n=======\n(?P<replace>.*?)\n>>>>>>> REPLACE"
        edit_matches = re.finditer(edit_regex, patch_edits, re.DOTALL)

        for edit_match in edit_matches:
            edit_search = edit_match.group("search")
            edit_replace = edit_match.group("replace")

            
            # post-processing code format
            if normalize_repo_name(repo) in SPECIAL_PATCH_FORMAT_REPOS or 'Automattic' in repo:
                if check_pattern == '':
                    extracted_edits[file_path].append({"SEARCH": [line.strip() for line in edit_search.split('\n')], "REPLACE": [line.strip() for line in edit_replace.split('\n')]})
                elif check_pattern == '\n':
                    extracted_edits[file_path].append({"SEARCH": [line.strip() for line in split_js_code_safely(edit_search)], "REPLACE": [line.strip() for line in split_js_code_safely(edit_replace)]})
                elif check_pattern == '\\\\':
                    edit_search = edit_search.replace("\\\\", "\\")
                    edit_replace = edit_replace.replace("\\\\", "\\")
                    extracted_edits[file_path].append({"SEARCH": [line.strip() for line in edit_search.split('\n')], "REPLACE": [line.strip() for line in edit_replace.split('\n')]})
                    # extracted_edits[file_path].append({"SEARCH": [line.strip() for line in split_js_code_safely(edit_search)], "REPLACE": [line.strip() for line in split_js_code_safely(edit_replace)]})
                elif check_pattern == '\\t':
                    edit_search = edit_search.replace("\\t", "\t")
                    edit_replace = edit_replace.replace("\\t", "\t")
                    extracted_edits[file_path].append({"SEARCH": [line.strip() for line in edit_search.split('\n')], "REPLACE": [line.strip() for line in edit_replace.split('\n')]})
                    # extracted_edits[file_path].append({"SEARCH": [line.strip() for line in split_js_code_safely(edit_search)], "REPLACE": [line.strip() for line in split_js_code_safely(edit_replace)]})
                elif check_pattern == '\\\\and\\t':
                    edit_search = edit_search.replace("\\\\", "\\").replace("\\t", "\t")
                    edit_replace = edit_replace.replace("\\\\", "\\").replace("\\t", "\t")
                    extracted_edits[file_path].append({"SEARCH": [line.strip() for line in edit_search.split('\n')], "REPLACE": [line.strip() for line in edit_replace.split('\n')]})
                    # extracted_edits[file_path].append({"SEARCH": [line.strip() for line in split_js_code_safely(edit_search)], "REPLACE": [line.strip() for line in split_js_code_safely(edit_replace)]})

            else:
                extracted_edits[file_path].append({"SEARCH": [line.strip() for line in edit_search.split('\n')], "REPLACE": [line for line in edit_replace.split('\n')]})
            
            # else:
                # extracted_edits[file_path].append({"SEARCH": [line.strip() for line in edit_search.split('\n')], "REPLACE": [line.strip() for line in edit_replace.split('\n')]})
                
    return extracted_edits


def has_nonempty_patch_edits(patch_edits):
    if not isinstance(patch_edits, dict):
        return False
    for file_edits in patch_edits.values():
        if isinstance(file_edits, list) and len(file_edits) > 0:
            return True
    return False


def select_nonempty_patch_key(patches_edits_dict, preferred_patch_no=None):
    preferred_candidates = []
    fallback_candidates = []

    for key, patch_edits in patches_edits_dict.items():
        if not has_nonempty_patch_edits(patch_edits):
            continue
        try:
            patch_num = float(str(key).split("/")[0])
        except Exception:
            patch_num = float("inf")
        item = (patch_num, str(key))
        fallback_candidates.append(item)
        if preferred_patch_no is not None and int(patch_num) == int(preferred_patch_no):
            preferred_candidates.append(item)

    if preferred_candidates:
        preferred_candidates.sort(key=lambda item: item[0])
        for patch_num, key in preferred_candidates:
            if abs(patch_num - float(preferred_patch_no)) < 1e-9:
                return key
        return preferred_candidates[0][1]

    if fallback_candidates:
        fallback_candidates.sort(key=lambda item: item[0])
        return fallback_candidates[0][1]

    return None


def load_patch_target_file_lines(bug_file, instance_repo_path, repo_structure_dict):
    bug_file_path = os.path.join(instance_repo_path, bug_file)

    if os.path.exists(bug_file_path):
        return [line.rstrip("\n") for line in read_file_lines(bug_file_path)], bug_file_path, "disk"

    try:
        bug_file_structure_dict = get_bug_file_structure_dict(bug_file, repo_structure_dict)
        return bug_file_structure_dict['text'], bug_file_path, "repo_structure"
    except Exception:
        return None, bug_file_path, "missing"


def load_patch_target_file_lines_raw_default(bug_file, instance_repo_path, repo_structure_dict):
    bug_file_path = os.path.join(instance_repo_path, bug_file)

    try:
        bug_file_structure_dict = get_bug_file_structure_dict(bug_file, repo_structure_dict)
        return bug_file_structure_dict["text"], bug_file_path, "repo_structure"
    except Exception:
        if os.path.exists(bug_file_path):
            return [line.rstrip("\n") for line in read_file_lines(bug_file_path)], bug_file_path, "disk"
        return None, bug_file_path, "missing"


def find_matching_patch_edit_positions(patch_edits_list, bug_file_content):
    # Collect all match locations first so replacements can be applied safely in reverse order.
    positions = []
    match_results = []

    for patch_edit in patch_edits_list:
        search_lines = patch_edit.get("SEARCH", [])
        back_fill_result = 'Fail'

        if not search_lines:
            match_results.append(back_fill_result)
            continue

        for i in range(len(bug_file_content) - len(search_lines) + 1):
            if [line.strip() for line in bug_file_content[i:i + len(search_lines)]] == search_lines:
                positions.append((i, patch_edit))
                back_fill_result = 'Success'
                break

        if back_fill_result == 'Fail':
            for i in range(len(bug_file_content) - len(search_lines) + 1):
                if [line.replace('\\','').strip() for line in bug_file_content[i:i + len(search_lines)]] == [line.replace('\\','').strip() for line in search_lines]:
                    positions.append((i, patch_edit))
                    back_fill_result = 'Success (remove \\)'
                    break

        if back_fill_result == 'Fail':
            for i in range(len(bug_file_content) - len(search_lines) + 1):
                search_str = '\n'.join([line.strip() for line in search_lines])
                replace_str = '\n'.join([line.strip() for line in bug_file_content[i:i + len(search_lines)]])
                similarity = SequenceMatcher(None, search_str, replace_str).ratio()
                if similarity >= 0.9:
                    positions.append((i, patch_edit))
                    back_fill_result = f'Success (similarity {similarity})'
                    break

        if back_fill_result == 'Fail':
            for i in range(len(bug_file_content) - len(search_lines) + 1):
                if [line.strip() for line in bug_file_content[i:i + len(search_lines)] if '\\' not in line] == [line.strip() for line in search_lines if '\\' not in line]:
                    positions.append((i, patch_edit))
                    back_fill_result = 'Success (skip \\)'
                    break

        match_results.append(back_fill_result)

    return positions, match_results


def apply_patch_edits_to_file(patch_edits_list, bug_file_content, bug_file_path, logger):

    pre_file_content = copy.deepcopy(bug_file_content)
    positions, match_results = find_matching_patch_edit_positions(patch_edits_list, bug_file_content)
    for back_fill_result in match_results:
        logger.info(f'A Pre2Bug {back_fill_result}')

    if not positions:
        return False, "\n".join(pre_file_content)

    # Apply from bottom to top so earlier replacements do not shift later match offsets.
    for i, patch_edit in sorted(positions, key=lambda x: x[0], reverse=True):
        search_lines = patch_edit["SEARCH"]
        replace_lines = patch_edit["REPLACE"]

        pre_file_content[i:i + len(search_lines)] = replace_lines
        logger.info(f"Patch applied successfully at line {i}")

    pre_file_content = "\n".join(pre_file_content)
    save_file(bug_file_path, pre_file_content + "\n")

    return True, pre_file_content


def patch_edits_match_repo(patch_edits, instance_repo_path, repo_structure_dict):
    if not has_nonempty_patch_edits(patch_edits):
        return False

    for bug_file, patch_edits_list in patch_edits.items():
        if not isinstance(patch_edits_list, list) or len(patch_edits_list) == 0:
            continue
        bug_file_content, _, source_kind = load_patch_target_file_lines(
            bug_file,
            instance_repo_path,
            repo_structure_dict,
        )
        if source_kind == "missing" or bug_file_content is None:
            continue
        positions, _ = find_matching_patch_edit_positions(patch_edits_list, bug_file_content)
        if positions:
            return True

    return False


def select_matchable_patch_key(
    patches_edits_dict,
    preferred_patch_no=None,
    instance_repo_path=None,
    repo_structure_dict=None,
    applicability_cache=None,
):
    preferred_candidates = []
    fallback_candidates = []

    for key, patch_edits in patches_edits_dict.items():
        if not has_nonempty_patch_edits(patch_edits):
            continue

        if instance_repo_path is not None and repo_structure_dict is not None:
            cache_key = str(key)
            if applicability_cache is not None and cache_key in applicability_cache:
                is_matchable = applicability_cache[cache_key]
            else:
                is_matchable = patch_edits_match_repo(
                    patch_edits,
                    instance_repo_path,
                    repo_structure_dict,
                )
                if applicability_cache is not None:
                    applicability_cache[cache_key] = is_matchable
            if not is_matchable:
                continue

        try:
            patch_num = float(str(key).split("/")[0])
        except Exception:
            patch_num = float("inf")
        item = (patch_num, str(key))
        fallback_candidates.append(item)
        if preferred_patch_no is not None and int(patch_num) == int(preferred_patch_no):
            preferred_candidates.append(item)

    if preferred_candidates:
        preferred_candidates.sort(key=lambda item: item[0])
        for patch_num, key in preferred_candidates:
            if abs(patch_num - float(preferred_patch_no)) < 1e-9:
                return key
        return preferred_candidates[0][1]

    if fallback_candidates:
        fallback_candidates.sort(key=lambda item: item[0])
        return fallback_candidates[0][1]

    return None


def promote_default_patch_edits_for_val(
    patches_edits_dict,
    start_no,
    max_no,
    logger=None,
    instance_repo_path=None,
    repo_structure_dict=None,
    applicability_cache=None,
):
    canonical_key = f"{int(start_no)}/{int(max_no)}"
    canonical_patch_edits = patches_edits_dict.get(canonical_key, {})
    if has_nonempty_patch_edits(canonical_patch_edits):
        if (
            instance_repo_path is None
            or repo_structure_dict is None
            or patch_edits_match_repo(canonical_patch_edits, instance_repo_path, repo_structure_dict)
        ):
            return False

    source_key = select_matchable_patch_key(
        patches_edits_dict,
        preferred_patch_no=start_no,
        instance_repo_path=instance_repo_path,
        repo_structure_dict=repo_structure_dict,
        applicability_cache=applicability_cache,
    )
    if not source_key or source_key == canonical_key:
        return False

    patches_edits_dict[canonical_key] = copy.deepcopy(patches_edits_dict[source_key])
    if logger is not None:
        logger.info(f"Promote default val patch edits: {source_key} -> {canonical_key}")
    return True


def back_fill_patch(patch_edits, instance_repo_path, repo_structure_dict, logger):
    for bug_file in patch_edits.keys():
        logger.info(f'Backfill: {bug_file}')
        patch_edits_list = patch_edits[bug_file]

        bug_file_content, bug_file_path, source_kind = load_patch_target_file_lines_raw_default(
            bug_file,
            instance_repo_path,
            repo_structure_dict,
        )
        if source_kind == "repo_structure":
            logger.info('default patch apply: get Backfill src file from repo_structure')
        elif source_kind == "disk":
            logger.info('default patch apply: get Backfill src file from disk')
        else:
            logger.info(f'default patch apply: Backfill Bug File path not exist: {bug_file}')

        if source_kind != "missing":
            applied, _ = apply_patch_edits_to_file(
                patch_edits_list,
                bug_file_content,
                bug_file_path,
                logger,
            )
            if applied:
                logger.info('default patch apply succeeded')
                continue
            logger.info('default patch apply failed, fallback to current method')

        bug_file_content, bug_file_path, source_kind = load_patch_target_file_lines(
            bug_file,
            instance_repo_path,
            repo_structure_dict,
        )
        if source_kind == "disk":
            logger.info('fallback patch apply: get Backfill src file from disk')
        elif source_kind == "repo_structure":
            logger.info('fallback patch apply: get Backfill src file from repo_structure')
        else:
            logger.info(f'fallback patch apply: Backfill Bug File path not exist: {bug_file}')
            continue

        applied, _ = apply_patch_edits_to_file(
            patch_edits_list,
            bug_file_content,
            bug_file_path,
            logger,
        )
        if not applied:
            logger.info('fallback patch apply failed')

def pre2bug(patch_edits_list, bug_file_content, bug_file_path, logger):
    _, pre_file_content = apply_patch_edits_to_file(
        patch_edits_list,
        bug_file_content,
        bug_file_path,
        logger,
    )
    return pre_file_content

def get_all_file_paths(repo_structure_dict, current_path=""):
    """
    get repo_structure_dict file path, retrun all file paths
    
    :param repo_structure_dict: dict, repo dir
    :param current_path: str, current path
    :return: list, all file paths
    """
    file_paths = []
    
    for key, value in repo_structure_dict.items():
        if isinstance(value, dict):
            file_paths.extend(get_all_file_paths(value, current_path + "/" + key))
        else:
            file_paths.append(current_path)
    
    return file_paths

def remove_first_dir(path, remove_dir):
    parts = path.split(os.sep)  
    if len(parts) > remove_dir:
        return os.path.join(*parts[remove_dir:])
    return path

# check all bug file path
def check_bug_files_path(bug_files_dict, repo_structure_dict):
    all_files_list = get_all_file_paths(repo_structure_dict)
    all_files_list = list(set(all_files_list))
    all_files_list = [file[1:] for file in all_files_list]
    # print(all_files_list)

    for index in bug_files_dict:
        bug_files_list = bug_files_dict[index]['bug_files']
        new_bug_files_list = []

        for bug_file in bug_files_list:
            if '.' not in bug_file: continue

            find_result = 'no'
            if bug_file in all_files_list:
                new_bug_files_list.append(bug_file)
                find_result = 'yes'
                continue
            else:
                for iter_file in all_files_list:
                    if bug_file in iter_file:
                        new_bug_files_list.append(iter_file)
                        find_result = 'yes'
                        break
            # remove incorrent first level dir if is doesn't work
            if find_result == 'no':
                bug_file_slice = remove_first_dir(bug_file, remove_dir=1)
                # print(bug_file_slice)
                for iter_file in all_files_list:
                    if bug_file_slice in iter_file:
                        new_bug_files_list.append(iter_file)
                        find_result = 'yes'
                        break    
            # remove incorrent second level dir if is doesn't work
            if find_result == 'no':
                bug_file_slice = remove_first_dir(bug_file, remove_dir=2)
                # print(bug_file_slice)
                for iter_file in all_files_list:
                    if bug_file_slice in iter_file:
                        new_bug_files_list.append(iter_file)
                        find_result = 'yes'
                        break  
            # remove incorrent third level dir if is doesn't work
            if find_result == 'no':
                bug_file_slice = remove_first_dir(bug_file, remove_dir=3)
                # print(bug_file_slice)
                for iter_file in all_files_list:
                    if bug_file_slice in iter_file:
                        new_bug_files_list.append(iter_file)
                        find_result = 'yes'
                        break  
            # remove incorrent fourth level dir if is doesn't work
            if find_result == 'no':
                bug_file_slice = remove_first_dir(bug_file, remove_dir=4)
                # print(bug_file_slice)
                for iter_file in all_files_list:
                    if bug_file_slice in iter_file:
                        new_bug_files_list.append(iter_file)
                        find_result = 'yes'
                        break  
        # if new_bug_files_list == []:
        
        bug_files_dict[index]['bug_files'] = new_bug_files_list
    return bug_files_dict, all_files_list



def file_level_locating_val(instance_id, instance_repo_path, instance_info, args, logger):
    repo = instance_info['repo']
    base_commit = instance_info['base_commit']
    problem_statement = instance_info['problem_statement']


    token_usage_dict_file = os.path.join(args.output_dir, 'token_usage.json')
    token_usage_dict = {}
    if os.path.isfile(token_usage_dict_file):
        try:
            existing_token_usage = read_json_file(token_usage_dict_file)
            if isinstance(existing_token_usage, dict):
                token_usage_dict = existing_token_usage
        except Exception as e:
            logger.warning(f"Failed to read existing token_usage.json: {e}")
    run_instance_prebuild_if_needed(instance_id, instance_repo_path, args, logger)

    input_dir = os.path.join(
        args.repo_path,
        args.dataset_split,
        instance_id.split('__')[0],
        instance_id,
        "IMAGE"
    )

    logger.info(f"Input image dir: {input_dir}")

    preprocess_output_dir = os.path.join(args.output_dir, "preprocess_image")

    logger.info(f"Check preprocess_output_dir: {preprocess_output_dir}")

    skip_preprocess = should_skip_preprocess_for_repo(repo)
    logger.info(
        f"Preprocess gate: repo={repo}, instance_id={instance_id}, "
        f"skip_preprocess={skip_preprocess}"
    )

    if args.task == "run":
        os.makedirs(preprocess_output_dir, exist_ok=True)

        if not skip_preprocess and os.path.isdir(input_dir) and os.listdir(input_dir):
            from Tools.preprocess.preprocess import preprocess
            preprocess(
                input_dir=input_dir,
                output_dir=preprocess_output_dir
            )

    # get bug scenarion images
    try:
        if skip_preprocess:
            image_file_list = get_bug_scenarion_images(input_dir, args)
        else:
            image_file_list = get_bug_scenarion_images(preprocess_output_dir, args)
        if args.task == "val" and not image_file_list:
            missing_input_dir = input_dir if skip_preprocess else preprocess_output_dir
            raise RuntimeError(f"no valid image inputs in: {missing_input_dir}")
        # image_file_list = get_bug_scenarion_images(os.path.join(args.repo_path, args.dataset_split, instance_id.split('__')[0], instance_id, 'IMAGE'), args)
    except Exception as e:
        if args.task == "val":
            raise
        logger.info(f"{e}")
        image_file_list = []

    
    # Image2Code: generating reproduce code about the bug scenarion/image
    
    normalized_repo = normalize_repo_name(repo)
    
    if args.Code_Reply == 'True' and normalized_repo in RUNTIME_WEB_REPOS:
        user_reproduce_code_file_path = os.path.join(args.repo_path, args.dataset_split, instance_id.split('__')[0], instance_id, 'BUG', 'reproduce_code.txt')
        # if os.path.isfile(user_reproduce_code_file_path): # if issue report provides the reproduce code url or text
        #     logger.info(f'Issue Reprot has provided Reproduce Code or LLM has genereated Reproduce Code.')
        #     reproduce_code = read_file(user_reproduce_code_file_path)
        #     doc_files_dict, compressed_doc_files_dict, doc_files_content = {}, {}, ""
        
        # else:  # if no reproduce code
        reproduce_code_dict, reproduce_code, doc_files_dict, compressed_doc_files_dict, doc_files_content, token_usage_dict = Image2Code(logger, args, repo, instance_id, instance_repo_path, token_usage_dict, problem_statement, image_file_list)
            # save_file(user_reproduce_code_file_path, reproduce_code)

        if os.path.isfile(user_reproduce_code_file_path): # if issue report provides the reproduce code url or text
            logger.info(f'Issue Reprot has provided Reproduce Code.')
            reproduce_code = read_file(user_reproduce_code_file_path)

        if "****" in problem_statement: mark_code_left, mark_code_right = "****", "****"
        elif "***" in problem_statement: mark_code_left, mark_code_right = "***", "***"
        elif "**" in problem_statement: mark_code_left, mark_code_right = "**", "**"
        elif "*\n" in problem_statement: mark_code_left, mark_code_right = "*", "*"
        elif "####" in problem_statement: mark_code_left, mark_code_right = "#### ", ""
        elif "###" in problem_statement: mark_code_left, mark_code_right = "### ", ""
        elif "##" in problem_statement: mark_code_left, mark_code_right = "## ", ""
        elif "#\n" in problem_statement: mark_code_left, mark_code_right = "# ", ""
        else: mark_code_left, mark_code_right = "", ""


        reproduce_code_file_type = 'JSX'

        # if this is Web UI, add the UI .md docs.
        if normalized_repo in RUNTIME_WEB_REPOS_WITH_DOCS:
            problem_statement = f"""
{problem_statement.strip()}
\n
{mark_code_left}Reproduce Code:{mark_code_right}
```{reproduce_code_file_type}
{reproduce_code}
```
\n
{mark_code_left}Related Documents:{mark_code_right}
Here's some documents maybe are useful to understand Web Components. 
```markdown
{doc_files_content}
```
\n
"""
        elif repo in ["GoogleChrome/lighthouse"]:
            problem_statement = f"""
{problem_statement.strip()}
\n
{mark_code_left}Reproduce Code:{mark_code_right}
```json
{reproduce_code_dict}
```
"""            

        else:
            problem_statement = f"""
{problem_statement.strip()}
\n
{mark_code_left}Reproduce Code:{mark_code_right}
```{reproduce_code_file_type}
{reproduce_code}
```
\n
"""
    
    # 1.1 - get repo structure
    repo_structure_dict_file = os.path.join(args.output_dir, '1-1_repo_structure.json')
    if os.path.isfile(repo_structure_dict_file):
        logger.info(f'Repo Structure has existed (1): {repo_structure_dict_file}')
        repo_structure_dict = read_json_file(repo_structure_dict_file)
    elif os.path.isfile(repo_structure_dict_file.replace('_Image2Code','')) and os.path.isfile(repo_structure_dict_file) is False:
        logger.info(f"Repo Structure has existed (2): {repo_structure_dict_file.replace('_Image2Code','')}")
        repo_structure_dict = read_json_file(repo_structure_dict_file.replace('_Image2Code',''))
    elif os.path.isfile(repo_structure_dict_file.replace(args.base_model + '/','')) and os.path.isfile(repo_structure_dict_file) is False:
        logger.info(f"Repo Structure has existed (3): {repo_structure_dict_file.replace(args.base_model + '/','')}")
        repo_structure_dict = read_json_file(repo_structure_dict_file.replace(args.base_model + '/',''))
    elif os.path.isfile(repo_structure_dict_file.replace(args.base_model + '/','').replace('_Image2Code','')) and os.path.isfile(repo_structure_dict_file) is False:
        logger.info(f"Repo Structure has existed (4): {repo_structure_dict_file.replace(args.base_model + '/','').replace('_Image2Code','')}")
        repo_structure_dict = read_json_file(repo_structure_dict_file.replace(args.base_model + '/','').replace('_Image2Code',''))
    else:
        try:
            clean_result = clean_repo(instance_repo_path, logger) # clean the repo changes
            repo_structure_dict = create_structure(instance_repo_path, args)
            save_json_file(repo_structure_dict_file, repo_structure_dict)
        except:
            repo_structure_dict = {}
            logger.info('Repo Structure Parser Error!')
    repo_structure_filtering = show_project_structure(repo_structure_dict, args) # Filtering non-js files
    logger.info(f'Repo Path: {instance_repo_path}')
    save_file(repo_structure_dict_file.replace('.json','.txt'), repo_structure_filtering)
    # logger.info(f'Repo Structure:\n{repo_structure_filtering}')
    # return
    
    # 1.2 - find all bug files by looking repo structure
    # Action: input problem_statement and repo_structure to ask LLM infer related bug files
    bug_files_dict_file = os.path.join(args.output_dir, '1-2_bug_files.json')
    if os.path.isfile(bug_files_dict_file):
        logger.info(f'Bug Files has existed: {bug_files_dict_file}')
        bug_files_dict = read_json_file(bug_files_dict_file)
    else:
        system_prompt, user_prompt = all_file_locate_prompt_construction(repo_structure_filtering, problem_statement, image_file_list, args)
        if is_openai_compatible_model(args.base_model):
            bug_files_dict, token_usage = openai_chat(system_prompt, user_prompt, args, args.all_bug_file_temperature, args.all_bug_file_samples)
        elif is_claude_model(args.base_model):
            bug_files_dict, bug_files_str, token_usage = claude_chat(system_prompt, user_prompt, args, args.all_bug_file_temperature, args.all_bug_file_samples)
            save_file(bug_files_dict_file.replace('.json','.txt'), bug_files_str)
        save_json_file(bug_files_dict_file, bug_files_dict)
        token_usage_dict[bug_files_dict_file] = token_usage
    logger.info(f'Bug Files Created')
    # return
    
    # bug_files_agent = file_level_locating_agent(instance_id, instance_repo_path, instance_info, args)

    # check the file path and fix it
    bug_files_dict, all_files_list = check_bug_files_path(bug_files_dict, repo_structure_dict)
    # logger.info(all_files_list)
    save_json_file(os.path.join(args.output_dir, '1-3_bug_files_checked.json'), bug_files_dict)
    logger.info(f'Bug Files Checked')
    # return

    # Generate search keywords from the bug report and images, then use them
    # to retrieve additional candidate files from the repository.
    bug_files_dict_with_keyword_file = os.path.join(args.output_dir, '1-4_bug_files_with_keyword.json')
    if args.keywords_searching == 'True':
        if os.path.isfile(bug_files_dict_with_keyword_file) is False:
            # Build keyword hints from the bug report and reference images.
            system_prompt, user_prompt = keywords_generation_prompt_construction(problem_statement, image_file_list)
            bug_keywords_dict, token_usage = openai_chat(system_prompt, user_prompt, args, args.bug_keywords_temperature, args.bug_keywords_samples)
            print(bug_keywords_dict)
            logger.info(f"Generated bug keywords:\n {bug_keywords_dict[1]['bug_keywords']} ...")

            # Search the repository with the generated keywords and cache the results.
            logger.info(f"Searching {instance_repo_path} with generated bug keywords ...")
            bug_keywords = bug_keywords_dict[1]['bug_keywords']
            # bug_keywords = [
            #     "formatter", "format(", "highlight(", "shiki", "async", "await",
            #     "applyFixes", "bracketSpacing", "semi", "rules", "lintRules", "fixable"
            # ]
            bug_files_searching_keywords = bug_file_searching_by_keywords(instance_repo_path, all_files_list, bug_keywords)
            bug_keywords_dict[1]['bug_files'] = bug_files_searching_keywords
            save_json_file(bug_files_dict_with_keyword_file, bug_keywords_dict)
            token_usage_dict[bug_files_dict_with_keyword_file] = token_usage
            logger.info(f"\nKeyword search completed. Saved top-{len(bug_files_searching_keywords)} files.")
        else:
            bug_files_dict_with_keyword = read_json_file(bug_files_dict_with_keyword_file)
            bug_files_searching_keywords = bug_files_dict_with_keyword["1"]['bug_files']

        
        bug_files_dict["1"]["bug_files"].extend([bug_file[0] for bug_file in bug_files_searching_keywords])
        save_json_file(bug_files_dict_with_keyword_file.replace('1-4_','1-4-1_'), bug_files_dict)
    
    
    # need add RAG embedding
    bug_files_dict_with_RAG_file = os.path.join(args.output_dir, '1-4_bug_files_with_RAG.json')
    if args.RAG_query == 'True':
        if os.path.isfile(bug_files_dict_with_RAG_file) is False:
            first_key = next(iter(bug_files_dict))  # get the first key
            first_bug_file_list = bug_files_dict[first_key]["bug_files"]  # get the bug_files
            No1_bug_file_path_list = []
            for first_bug_file in first_bug_file_list:
                No1_bug_file_path = os.path.dirname(first_bug_file)  # Get the parent path
                if No1_bug_file_path not in No1_bug_file_path_list:
                    No1_bug_file_path_list.append(No1_bug_file_path)
            # No1_bug_file_path = os.path.dirname(bug_files_dict["1"]["bug_files"][0])  # Get the parent path
            # logger.info(f'No1_bug_file_path: {No1_bug_file_path}')
            # No1_bug_file_path_list = No1_bug_file_path_list[-1:]
            logger.info(f'RAG Dir Path: {No1_bug_file_path_list}')
            # return
            bug_files_rag_list = []
            

            bug_files_rag = query_bug_file(logger, instance_repo_path, No1_bug_file_path_list, 8, problem_statement)
            bug_files_rag_list.extend(bug_files_rag)
            logger.info(f'{bug_files_rag_list}')
            
            bug_files_dict[first_key]["bug_files"].extend(bug_files_rag_list)
            save_json_file(bug_files_dict_with_RAG_file, bug_files_dict)
        else:
            bug_files_dict = read_json_file(bug_files_dict_with_RAG_file)

    # return

    # 2.1 - compressing all bug files
    compressed_bug_files_dict_file = os.path.join(args.output_dir, '2-1_compressed_bug_files.json')
    if os.path.isfile(compressed_bug_files_dict_file):
        logger.info(f'Compressed Bug Files has existed: {compressed_bug_files_dict_file}')
        compressed_bug_files_dict = read_json_file(compressed_bug_files_dict_file)
    else:
        compressed_bug_files_dict = {}
        # bug_files_list = list(set(sum([bug_files_dict[key]['bug_files'] for key in bug_files_dict.keys()], [])))
        bug_files_list = list(dict.fromkeys(sum([bug_files_dict[key]['bug_files'] for key in bug_files_dict.keys()], [])))

        for index, bug_file in enumerate(bug_files_list):
            bug_file_path = os.path.join(instance_repo_path, bug_file)
            # logger.info(f'{bug_file}')
            bug_file_structure_dict = get_bug_file_structure_dict(bug_file, repo_structure_dict)
            try: 
                compressed_bug_file_content_list = compressing_javascript_file(bug_file_structure_dict, save_variable_declaration=False)
            except:
                continue
            
            compressed_bug_files_dict[index+1] = {}
            compressed_bug_files_dict[index+1]['bug_file'] = bug_file
            if len(bug_file_structure_dict['text']) > args.max_lines_per_key_file:
                compressed_bug_files_dict[index+1]['compressed'] = 'YES'
                compressed_bug_files_dict[index+1]['line_numbers'] = len(bug_file_structure_dict['text'])
                compressed_bug_files_dict[index+1]['compressed_line_numbers'] = len(compressed_bug_file_content_list)
                compressed_bug_files_dict[index+1]['compressed_bug_file_content'] = """""".join(compressed_bug_file_content_list)
                if compressed_bug_files_dict[index+1]['compressed_bug_file_content'].strip() == '':
                    compressed_bug_files_dict[index+1]['compressed'] = 'YES2NO'
                    compressed_bug_files_dict[index+1]['compressed_bug_file_content'] = "\n".join(bug_file_structure_dict['text'])

            else:
                compressed_bug_files_dict[index+1]['compressed'] = 'NO'
                compressed_bug_files_dict[index+1]['line_numbers'] = len(bug_file_structure_dict['text'])
                compressed_bug_files_dict[index+1]['compressed_line_numbers'] = len(bug_file_structure_dict['text'])
                compressed_bug_files_dict[index+1]['compressed_bug_file_content'] = "\n".join(bug_file_structure_dict['text'])

        save_json_file(compressed_bug_files_dict_file, compressed_bug_files_dict)
    logger.info(f'Compressed Bug Files: {compressed_bug_files_dict_file}')
    # return

    # 2.2 - find key bug files by looking compressed bug files
    # Action: input problem_statement and compressed bug files from step-2.1, then ask LLM to query key bug files
    key_bug_files_dict_file = os.path.join(args.output_dir, '2-2_key_bug_files.json')
    if os.path.isfile(key_bug_files_dict_file):
        logger.info(f'Key Bug Files has existed: {bug_files_dict_file}')
        key_bug_files_dict = read_json_file(key_bug_files_dict_file)
    else:
        if len(compressed_bug_files_dict.keys()) > args.max_candidate_bug_files:
            system_prompt, user_prompt = key_file_locate_prompt_construction(compressed_bug_files_dict, problem_statement, image_file_list, args.max_candidate_bug_files, args)
            if is_openai_compatible_model(args.base_model):
                key_bug_files_dict, token_usage = openai_chat(system_prompt, user_prompt, args, args.key_bug_file_temperature, args.key_bug_file_samples)
            if is_claude_model(args.base_model):
                key_bug_files_dict, key_bug_files_str, token_usage = claude_chat(system_prompt, user_prompt, args, args.key_bug_file_temperature, args.key_bug_file_samples)
                save_file(key_bug_files_dict_file.replace('.json','.txt'), key_bug_files_str)
            token_usage_dict[key_bug_files_dict_file] = token_usage
        else:
            key_bug_files_dict = {}
            key_bug_files_dict["1"] = {
                "bug_files": [compressed_bug_files_dict[key]["bug_file"] for key in compressed_bug_files_dict.keys()]
            }
        save_json_file(key_bug_files_dict_file, key_bug_files_dict)
    logger.info(f'Key Bug Files: {key_bug_files_dict_file}')


    # check the key file path and fix it
    key_bug_files_dict, all_files_list = check_bug_files_path(key_bug_files_dict, repo_structure_dict)
    save_json_file(os.path.join(args.output_dir, '2-2_key_bug_files_checked.json'), key_bug_files_dict)
    logger.info(f'Key Bug Files Checked')


    # 3.1 - compressing key bug files
    compressed_key_bug_files_dict_file = os.path.join(args.output_dir, '3-1_compressed_key_bug_files.json')
    if os.path.isfile(compressed_key_bug_files_dict_file):
        logger.info(f'Compressed Key Bug Files has existed: {compressed_key_bug_files_dict_file}')
        compressed_key_bug_files_dict = read_json_file(compressed_key_bug_files_dict_file)
    else:
        compressed_key_bug_files_dict = {}
        key_bug_files_list = sum([key_bug_files_dict[key]['bug_files'] for key in key_bug_files_dict.keys()], [])

        for index, bug_file in enumerate(key_bug_files_list):
            bug_file_path = os.path.join(instance_repo_path, bug_file)
            bug_file_structure_dict = get_bug_file_structure_dict(bug_file, repo_structure_dict)
            compressed_key_bug_file_content_list = compressing_javascript_file(bug_file_structure_dict, save_variable_declaration=False)
            
            compressed_key_bug_files_dict[index+1] = {}
            compressed_key_bug_files_dict[index+1]['bug_file'] = bug_file
            if len(bug_file_structure_dict['text']) > args.max_lines_per_key_file:
                compressed_key_bug_files_dict[index+1]['compressed'] = 'YES'
                compressed_key_bug_files_dict[index+1]['line_numbers'] = len(bug_file_structure_dict['text'])
                compressed_key_bug_files_dict[index+1]['compressed_line_numbers'] = len(compressed_key_bug_file_content_list)
                compressed_key_bug_files_dict[index+1]['compressed_bug_file_content'] = """""".join(compressed_key_bug_file_content_list)
                if compressed_key_bug_files_dict[index+1]['compressed_bug_file_content'].strip() == '':
                    compressed_key_bug_files_dict[index+1]['compressed'] = 'YES2NO'
                    compressed_key_bug_files_dict[index+1]['compressed_bug_file_content'] = "\n".join(bug_file_structure_dict['text'])
            else:
                compressed_key_bug_files_dict[index+1]['compressed'] = 'NO'
                compressed_key_bug_files_dict[index+1]['line_numbers'] = len(bug_file_structure_dict['text'])
                compressed_key_bug_files_dict[index+1]['compressed_line_numbers'] = len(bug_file_structure_dict['text'])
                compressed_key_bug_files_dict[index+1]['compressed_bug_file_content'] = "\n".join(bug_file_structure_dict['text'])


        save_json_file(compressed_key_bug_files_dict_file, compressed_key_bug_files_dict)
    logger.info(f'Compressed Key Bug Files: {compressed_key_bug_files_dict_file}')


    # 3.2 - locate all bug classes/functions
    # Action: input problem_statement and compressed key bug files from step-3.1, then ask LLM to find possible bug classes/functions
    key_bug_classes_functions_dict_file = os.path.join(args.output_dir, '3-2_key_bug_classes_functions.json')
    if os.path.isfile(key_bug_classes_functions_dict_file):
        logger.info(f'Key Bug Classes/Functions File has existed: {key_bug_classes_functions_dict_file}')
        key_bug_classes_functions_dict = read_json_file(key_bug_classes_functions_dict_file)
    else:
        if len(problem_statement.split('\n')) > 1500 and "Related Documents:" in problem_statement: 
            logger.info('Too Much Docs Content, Please Remove before locating all bug classes/functions...')
            problem_statement = problem_statement.split("Related Documents:")[0]
        system_prompt, user_prompt = key_class_function_locate_prompt_construction(compressed_key_bug_files_dict, problem_statement, image_file_list, args)
        if 'p5.js' in repo:
            system_prompt = system_prompt + """\n*Note that if function format is 'p5.RendererGL.prototype.newBuffers = function(gId, obj) {...}', the function name is 'p5.RendererGL.prototype.newBuffers'. Don't output incomplete function name."""
        
        if is_openai_compatible_model(args.base_model):
            key_bug_classes_functions_dict, token_usage = openai_chat(system_prompt, user_prompt, args, args.key_bug_class_function_temperature, args.key_bug_class_function_samples)
        if is_claude_model(args.base_model):
            key_bug_classes_functions_dict, key_bug_classes_functions_str, token_usage = claude_chat(system_prompt, user_prompt, args, args.key_bug_class_function_temperature, args.key_bug_class_function_samples)
            save_file(key_bug_classes_functions_dict_file.replace('.json','.txt'), key_bug_classes_functions_str)

        save_json_file(key_bug_classes_functions_dict_file, key_bug_classes_functions_dict)
        token_usage_dict[key_bug_classes_functions_dict_file] = token_usage
    logger.info(f'Key Bug Classes/Functions: {key_bug_classes_functions_dict_file}')
    # return

    # 4.1 - merge all bug classes/functions
    # Action: merge all classes and functions or elements
    merge_bug_classes_functions_dict_file = os.path.join(args.output_dir, '4-1_merged_key_bug_classes_functions.json')

    merge_bug_classes_functions_dict = {}
    merge_bug_classes_functions_dict['bug_classes'] = list(dict.fromkeys(sum([key_bug_classes_functions_dict[key]['bug_classes'] for key in key_bug_classes_functions_dict.keys()], [])))
    merge_bug_classes_functions_dict['bug_functions'] = list(dict.fromkeys(sum([key_bug_classes_functions_dict[key]['bug_functions'] for key in key_bug_classes_functions_dict.keys()], [])))
    # note that p5.js Repo have some class definition in Function Comments, so LLM may extract func name as class name
    # here we add class names to func names to avoid incorre extraction
    if 'p5.js' in repo: merge_bug_classes_functions_dict['bug_functions'].extend(merge_bug_classes_functions_dict['bug_classes'])
    save_json_file(merge_bug_classes_functions_dict_file, merge_bug_classes_functions_dict)
    logger.info(f'Merged Bug Classes/Functions: {merge_bug_classes_functions_dict_file}')
    # return

    # 4.2 - check all classes/functions to get the correct elements
    checked_bug_classed_functions_dict_file = os.path.join(args.output_dir, '4-2_checked_key_bug_classes_functions.json')
    checked_bug_classed_functions_dict = {}
    checked_bug_classed_functions_dict['bug_classes']= {}
    checked_bug_classed_functions_dict['bug_functions'] = {}
    key = 1

    for file_class in merge_bug_classes_functions_dict['bug_classes']:

        try:
            if '//' in file_class:
                pass
            else:
                file_class = file_class + '// '
            bug_file_path = file_class.split('//')[0]
            bug_class_name = file_class.split('//')[1]
            if '.' not in bug_file_path: continue
            
            bug_file_structure_dict = get_bug_file_structure_dict(bug_file_path, repo_structure_dict)
            checked_bug_classed_functions_dict['bug_classes'][key] = {}
            checked_bug_classed_functions_dict['bug_classes'][key]["class_name"] = bug_class_name
            checked_bug_classed_functions_dict['bug_classes'][key]["file_path"] = bug_file_path
            checked_bug_classed_functions_dict['bug_classes'][key]["class_details"] = [bug_class_dict for bug_class_dict in bug_file_structure_dict['classes'] if bug_class_dict['name'] == bug_class_name]
            
            class_name_list = [bug_class_dict['name'] for bug_class_dict in bug_file_structure_dict['classes']] 
            if bug_class_name not in class_name_list and args.Checked_Exception == 'True':
                if bug_class_name in "".join(bug_file_structure_dict["text"]):
                    logger.info(f'{bug_class_name} not in class_name_list')
                    checked_bug_classed_functions_dict['bug_classes'][key]["class_name"] = bug_class_name + " (not found) "
                    if len(bug_file_structure_dict["text"]) <= args.max_lines_per_snippet:
                        checked_bug_classed_functions_dict['bug_classes'][key]["class_details"].extend([{"name":bug_class_name, "start_line":1, "end_line": len(bug_file_structure_dict["text"])}])
                    else:
                        class_strat_line = 1
                        class_end_line = len(bug_file_structure_dict["text"])
                        for line_no,line_content in enumerate(bug_file_structure_dict["text"]):
                            if bug_class_name in line_content:
                                class_strat_line = line_no + 1
                                break
                        if class_strat_line+args.max_lines_per_snippet <= class_end_line: class_end_line = class_strat_line+args.max_lines_per_snippet
                        else: class_end_line = class_end_line
                        checked_bug_classed_functions_dict['bug_classes'][key]["class_name"] = bug_class_name + " (not found) " + str(class_strat_line) + '-' + str(class_end_line)
                        checked_bug_classed_functions_dict['bug_classes'][key]["class_details"].extend([{"name":bug_class_name, "start_line": class_strat_line, "end_line": class_end_line}])

            if checked_bug_classed_functions_dict['bug_classes'][key]["class_details"] == []:
                del checked_bug_classed_functions_dict['bug_classes'][key]
            else:
                strat_line = checked_bug_classed_functions_dict['bug_classes'][key]["class_details"][0]["start_line"]
                end_line = checked_bug_classed_functions_dict['bug_classes'][key]["class_details"][0]["end_line"]
                checked_bug_classed_functions_dict['bug_classes'][key]["class_code"] = bug_file_structure_dict['text'][strat_line-1:end_line]
                key += 1
        except Exception as e:
            del checked_bug_classed_functions_dict['bug_classes'][key]
            logger.info(f'{e}')

    for file_functions in merge_bug_classes_functions_dict['bug_functions']:
        
        try:
            if '//' in file_functions:
                pass
            else:
                file_functions = file_functions + '// '
            bug_file_path = file_functions.split('//')[0]
            bug_functions_name = file_functions.split('//')[1]
            if len(file_functions.split('//')) > 2: bug_functions_name = file_functions.split('//')[-1]

            if '.' not in bug_file_path: continue

            bug_file_structure_dict = get_bug_file_structure_dict(bug_file_path, repo_structure_dict)
            checked_bug_classed_functions_dict['bug_functions'][key] = {}
            checked_bug_classed_functions_dict['bug_functions'][key]["function_name"] = bug_functions_name
            checked_bug_classed_functions_dict['bug_functions'][key]["file_path"] = bug_file_path
            checked_bug_classed_functions_dict['bug_functions'][key]["function_details"] = [bug_functions_dict for bug_functions_dict in bug_file_structure_dict['functions'] if bug_functions_dict['name'] == bug_functions_name]
            
            function_name_list = [bug_functions_dict['name'] for bug_functions_dict in bug_file_structure_dict['functions']] 
            if bug_functions_name not in function_name_list and args.Checked_Exception == 'True':
                if bug_functions_name in "".join(bug_file_structure_dict["text"]):
                    logger.info(f'{bug_functions_name} not in function_name_list')
                    checked_bug_classed_functions_dict['bug_functions'][key]["function_name"] = bug_functions_name + " (not found) "
                    if len(bug_file_structure_dict["text"]) <= args.max_lines_per_snippet:
                        checked_bug_classed_functions_dict['bug_functions'][key]["function_details"].extend([{"name":bug_functions_name, "start_line":1, "end_line": len(bug_file_structure_dict["text"])}])
                    else:
                        function_strat_line = 1
                        function_end_line = len(bug_file_structure_dict["text"])
                        for line_no,line_content in enumerate(bug_file_structure_dict["text"]):
                            if bug_functions_name in line_content:
                                function_strat_line = line_no + 1
                                break
                        if function_strat_line+args.max_lines_per_snippet <= function_end_line: function_end_line = function_strat_line+args.max_lines_per_snippet
                        else: function_end_line = function_end_line
                        checked_bug_classed_functions_dict['bug_functions'][key]["function_name"] = bug_functions_name + " (not found) " + str(function_strat_line) + '-' + str(function_end_line)
                        checked_bug_classed_functions_dict['bug_functions'][key]["function_details"].extend([{"name":bug_functions_name, "start_line":function_strat_line, "end_line": function_end_line}])

            if checked_bug_classed_functions_dict['bug_functions'][key]["function_details"] == []:
                del checked_bug_classed_functions_dict['bug_functions'][key]
            else:
                strat_line = checked_bug_classed_functions_dict['bug_functions'][key]["function_details"][0]["start_line"]
                end_line = checked_bug_classed_functions_dict['bug_functions'][key]["function_details"][0]["end_line"]
                checked_bug_classed_functions_dict['bug_functions'][key]["function_code"] = bug_file_structure_dict['text'][strat_line-1:end_line]
                key += 1
        except:
            pass

    save_json_file(checked_bug_classed_functions_dict_file, checked_bug_classed_functions_dict)
    # return


    # 4.3 - preprocess checked classes/functions to get the bug files with context_window
    bug_files_with_context_dict_file = os.path.join(args.output_dir, '4-3_checked_bug_files_with_context.json')
    bug_files_with_context_dict = {}
    
    for key in checked_bug_classed_functions_dict['bug_classes']:
        file_path = checked_bug_classed_functions_dict['bug_classes'][key]["file_path"]
        bug_file_structure_dict = get_bug_file_structure_dict(file_path, repo_structure_dict)
        bug_files_with_context_dict[file_path] = ['...' for i in bug_file_structure_dict['text']]
    for key in checked_bug_classed_functions_dict['bug_functions']:
        file_path = checked_bug_classed_functions_dict['bug_functions'][key]["file_path"]
        bug_file_structure_dict = get_bug_file_structure_dict(file_path, repo_structure_dict)
        bug_files_with_context_dict[file_path] = ['...' for i in bug_file_structure_dict['text']]

    for key in checked_bug_classed_functions_dict['bug_classes'].keys():
        class_name = checked_bug_classed_functions_dict['bug_classes'][key]["class_name"]
        file_path = checked_bug_classed_functions_dict['bug_classes'][key]["file_path"]
        start_line = checked_bug_classed_functions_dict['bug_classes'][key]["class_details"][0]["start_line"]
        end_line = checked_bug_classed_functions_dict['bug_classes'][key]["class_details"][0]["end_line"]
        bug_file_structure_dict = get_bug_file_structure_dict(file_path, repo_structure_dict)
        bug_files_with_context_dict[file_path][start_line-args.context_window-1:end_line+args.context_window] = bug_file_structure_dict['text'][start_line-args.context_window-1:end_line+args.context_window]
    for key in checked_bug_classed_functions_dict['bug_functions'].keys():
        class_name = checked_bug_classed_functions_dict['bug_functions'][key]["function_name"]
        file_path = checked_bug_classed_functions_dict['bug_functions'][key]["file_path"]
        start_line = checked_bug_classed_functions_dict['bug_functions'][key]["function_details"][0]["start_line"]
        end_line = checked_bug_classed_functions_dict['bug_functions'][key]["function_details"][0]["end_line"]
        bug_file_structure_dict = get_bug_file_structure_dict(file_path, repo_structure_dict)
        bug_files_with_context_dict[file_path][start_line-args.context_window-1:end_line+args.context_window] = bug_file_structure_dict['text'][start_line-args.context_window-1:end_line+args.context_window]


    bug_files_with_context_dict = remove_blank_lines(bug_files_with_context_dict, blank_str='...', with_line_number=False)
    save_json_file(bug_files_with_context_dict_file, bug_files_with_context_dict)
    # return

    # save_json_file(bug_files_with_context_dict_file, bug_files_with_context_dict)


    # 4.4 - locate Top-N sets of line-level bug locations
    # not like agentless, we skip this step to locate the line/hunk level fault localization, we just use the class/function level fl like the SRepair

    # bug_files_with_context_line_level_fl_dict_file = os.path.join(args.output_dir, 'checked_bug_files_with_context_line_level_fl.json')
    # bug_files_with_context_line_level_fl_dict = {}
    # if os.path.isfile(bug_files_with_context_line_level_fl_dict_file):
    #     logger.info(f'bug_files_with_context_line_level_fl_dict_file has existed:\n{bug_files_with_context_line_level_fl_dict_file}')
    #     bug_files_with_context_line_level_fl_dict = read_json_file(bug_files_with_context_line_level_fl_dict_file)
    # else:
    #     system_prompt, user_prompt = line_level_class_function_locate_prompt_construction(bug_files_with_context_dict, problem_statement, image_file_list)
    #     bug_files_with_context_line_level_fl_dict = openai_chat(system_prompt, user_prompt, args, args.line_level_fl_temperature, args.line_level_fl_samples)
    #     save_json_file(bug_files_with_context_line_level_fl_dict_file, bug_files_with_context_line_level_fl_dict)
    # logger.info(f'Line-level FL Bug Classes/Functions:\n{bug_files_with_context_line_level_fl_dict_file}')

    input_dir = os.path.join(
        args.repo_path,
        args.dataset_split,
        instance_id.split('__')[0],
        instance_id,
        "IMAGE"
    )
    if not skip_preprocess and os.path.isdir(input_dir) and os.listdir(input_dir) and os.path.isdir(preprocess_output_dir) and os.listdir(preprocess_output_dir):
        input_dir = preprocess_output_dir

    should_ground = has_exactly_one_valid_image(input_dir)
    if should_ground:
        ground_dir = os.path.join(args.output_dir, "ground_image")
        os.makedirs(ground_dir, exist_ok=True)
        token_usage = process_single_image_folder(input_dir, problem_statement, ground_dir)
        token_usage_dict[ground_dir] = token_usage


    # 5.1 - Patch Generation
    # Action: input problem_statement and bug_files_with_context from step-4.3, then ask LLM to generate patches
    if args.task == 'run': patches_dict_file = os.path.join(args.output_dir, '5-1_patches.json')
    if args.task == 'val': patches_dict_file = os.path.join(args.output_dir, '5-1_patches_val.json')

    if os.path.isfile(patches_dict_file):
        logger.info(f'Patches File has existed: {patches_dict_file}')
        patches_dict = read_json_file(patches_dict_file)
    else:
        for key in bug_files_with_context_dict.keys():
            bug_files_with_context_dict[key] = "\n".join(bug_files_with_context_dict[key])
        
        # if len(problem_statement.split('\n')) > 1500 and "Related Documents:" in problem_statement
        if "Related Documents:" in problem_statement : 
            problem_statement = problem_statement.split("Related Documents:")[0]
            logger.info('Remove docs before patch generation.')
            # logger.info(f'{problem_statement}')



        if not should_ground:
            system_prompt, user_prompt = patch_generation_prompt_construction(bug_files_with_context_dict, problem_statement, image_file_list)
            # if 'gpt' in args.base_model or 'o4-mini' in args.base_model:
            if is_openai_compatible_model(args.base_model):
                patches_dict, token_usage = openai_chat(system_prompt, user_prompt, args, args.patch_generation_temperature, args.patch_generation_samples)
            if is_claude_model(args.base_model):
                patches_dict, patches_str, token_usage = claude_chat(system_prompt, user_prompt, args, args.patch_generation_temperature, args.patch_generation_samples)
                save_file(patches_dict_file.replace('.json','.txt'), patches_str)
            save_json_file(patches_dict_file, patches_dict)
            token_usage_dict[patches_dict_file] = token_usage

        else:
            def reindex_patches(patches, start, total):
                new_patches = {}
                for i, (_, v) in enumerate(patches.items(), start=start):
                    new_patches[f'{i}/{total}'] = v
                return new_patches

            files = os.listdir(ground_dir)
            images = [
                f for f in files
                if f.lower().endswith((".png", ".jpg", ".jpeg"))
                and "_visualized" not in f
            ]
            images = sorted(images)  

            total = len(images) * args.ground_patch_generation_samples

            patches_dict = {}
            patches_str_total = ''
            token_usage_total = {'base_model': args.base_model, 'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
            idx = 1

            for img in images:
                img_list = [build_image_payload(os.path.join(ground_dir, img), args)]

                system_prompt, user_prompt = patch_generation_prompt_construction(bug_files_with_context_dict, problem_statement, img_list)

                if is_openai_compatible_model(args.base_model):
                    patches, token_usage = openai_chat(system_prompt, user_prompt, args, args.patch_generation_temperature, args.ground_patch_generation_samples)
                if is_claude_model(args.base_model):
                    patches, patches_str, token_usage = claude_chat(system_prompt, user_prompt, args, args.patch_generation_temperature, args.ground_patch_generation_samples)
                    patches_str_total += patches_str

                patches_dict.update(reindex_patches(patches, idx, total))

                token_usage_total['prompt_tokens'] += token_usage.get('prompt_tokens', 0)
                token_usage_total['completion_tokens'] += token_usage.get('completion_tokens', 0)
                token_usage_total['total_tokens'] += token_usage.get('total_tokens', 0)

                idx += args.ground_patch_generation_samples

            if 'claude' in args.base_model:
                save_file(patches_dict_file.replace('.json', '.txt'), patches_str_total)

            save_json_file(patches_dict_file, patches_dict)
            token_usage_dict[patches_dict_file] = token_usage_total


    # 5.2 - Patch Post-process
    patches_edits_dict = {}
    if args.task == 'run': patches_edits_dict_file = os.path.join(args.output_dir, '5-2_patches_edits.json')
    if args.task == 'val': patches_edits_dict_file = os.path.join(args.output_dir, '5-2_patches_edits_val.json')

    if os.path.isfile(patches_edits_dict_file):
        logger.info(f'Patches Edits File has existed: {patches_edits_dict_file}')
        patches_edits_dict = read_json_file(patches_edits_dict_file)
    else:
        for key in patches_dict.keys():
            patches_edits_dict[key] = extract_patch_edits(patches_dict[key], repo, check_pattern='')
            if normalize_repo_name(repo) in SPECIAL_PATCH_FORMAT_REPOS:
                first_num, second_num = float(key.split('/')[0]), int(key.split('/')[1])
                patches_edits_dict[f'{first_num+0.1}/{second_num}'] = extract_patch_edits(patches_dict[key], repo, check_pattern='\n')
                patches_edits_dict[f'{first_num+0.2}/{second_num}'] = extract_patch_edits(patches_dict[key], repo, check_pattern='\\\\')
                patches_edits_dict[f'{first_num+0.3}/{second_num}'] = extract_patch_edits(patches_dict[key], repo, check_pattern='\\t')
                patches_edits_dict[f'{first_num+0.4}/{second_num}'] = extract_patch_edits(patches_dict[key], repo, check_pattern='\\\\and\\t')
              
        save_json_file(patches_edits_dict_file, patches_edits_dict)

    if (
        args.task == 'val'
        and getattr(args, "Patch_Select", "False") == "True"
        and int(float(getattr(args, "val_patch_no", "1"))) > 0
    ):
        start_no = int(float(getattr(args, "val_patch_no", "1")))
        max_no = int(getattr(args, "patch_generation_samples", 1))
        patch_applicability_cache = {}
        if promote_default_patch_edits_for_val(
            patches_edits_dict,
            start_no,
            max_no,
            logger=logger,
            instance_repo_path=instance_repo_path,
            repo_structure_dict=repo_structure_dict,
            applicability_cache=patch_applicability_cache,
        ):
            save_json_file(patches_edits_dict_file, patches_edits_dict)


    # 6.0 - Patch Check
    # Action: check the patch compilation error and refine it
    if args.task == 'run':
        patches_edits_dict_checked_file = os.path.join(args.output_dir, '5-2_patches_edits_checked.json')
    if args.task == 'val':
        patches_edits_dict_checked_file = os.path.join(args.output_dir, '5-2_patches_edits_checked_val.json')

    if args.Patch_Check == 'True':
        if os.path.isfile(patches_edits_dict_checked_file):
            logger.info(f'Patches Edits have checked: {patches_edits_dict_checked_file}')
            patches_edits_dict = read_json_file(patches_edits_dict_checked_file)
        else:
            logger.info('Check the compilation/lint error in patches...')
            patches_edits_dict = patch_check(
                patches_edits_dict,
                instance_repo_path,
                repo_structure_dict,
                logger,
                args,
                bug_files_with_context_dict,
                problem_statement,
                image_file_list,
                token_usage_dict,
                patches_edits_dict_checked_file,
            )
            save_json_file(patches_edits_dict_checked_file, patches_edits_dict)

    if args.task == 'run': 
        selected_patch_file = os.path.join(args.output_dir, '5-3_selected_patch.json')
        if os.path.isfile(selected_patch_file):
            logger.info(f'Patch Filter File has existed: {selected_patch_file}')
            selected_patch_dict_final = read_json_file(selected_patch_file)
        else:
            system_prompt, user_prompt = patch_selection_prompt_construction(problem_statement, patches_edits_dict, image_file_list)
            if is_openai_compatible_model(args.base_model):
                selected_patch_dict, selected_token_usage = openai_chat(system_prompt, user_prompt, args, 0, 1)
            if is_claude_model(args.base_model):
                selected_patch_dict, selected_patch_str, selected_token_usage = claude_chat(system_prompt, user_prompt, args, 0, 1)
                save_file(selected_patch_file.replace('.json','.txt'), selected_patch_str)


            selected_patch_id = list(selected_patch_dict.values())[0]['selected_patch_id']
            logger.info(f"selected_patch_id: {selected_patch_id}")
            patch_edits = patches_edits_dict[selected_patch_id]

            selected_patch_dict_final = {
                selected_patch_id: patch_edits
            }
            save_json_file(selected_patch_file, selected_patch_dict_final)
            logger.info(f"Saved selected patch to: {selected_patch_file}")

            token_usage_dict[selected_patch_file] = selected_token_usage

        

    # 6.1 - Patch Validation
    plaus_patch_diff = patch_validation(selected_patch_dict_final, instance_repo_path, repo_structure_dict, logger, args)

    if args.task == 'run':
        if plaus_patch_diff != "":
            save_file(os.path.join(args.output_dir, 'changes.diff'), plaus_patch_diff)
        else:
            save_file(os.path.join(args.output_dir, 'changes.diff'), plaus_patch_diff)
            logger.info("No Content in changes.diff")
    if args.task == 'val':
        save_file(os.path.join(args.output_dir, 'changes_val.diff'), plaus_patch_diff)

        if args.Patch_Select == 'True' and int(args.val_patch_no) > 0:
            feedback_multi = getattr(args, "feedback_multi", None)
            logger.info(f"feedback_multi: {feedback_multi}")
            feedback_selected = getattr(args, "feedback_selected", None)
            token_usage_multi = getattr(args, "token_usage_multi", None)
            token_usage_selected = getattr(args, "token_usage_selected", None)
            token_usage_code_html = getattr(args, "token_usage_code_html", None)

            if feedback_multi is not None and feedback_selected is not None:
                save_json_file(os.path.join(args.output_dir, '5-3_feedback_multi.json'), feedback_multi)
                save_json_file(os.path.join(args.output_dir, '5-3_feedback.json'), feedback_selected)
                if token_usage_multi is not None:
                    token_usage_dict[os.path.join(args.output_dir, '5-3_feedback_multi.json')] = token_usage_multi
                if token_usage_selected is not None:
                    token_usage_dict[os.path.join(args.output_dir, '5-3_feedback.json')] = token_usage_selected
                if token_usage_code_html is not None:
                    token_usage_dict[os.path.join(args.output_dir, 'code_html_selection.json')] = token_usage_code_html
            else:
                repair_feedback_dict, token_usage = repair_feedback(problem_statement, image_file_list, logger, args)
                feedback_multi_fallback = feedback_multi if feedback_multi is not None else {
                    "instance_id": instance_id,
                    "selected_patch_no": getattr(args, "selected_patch_no", None),
                    "candidates": [],
                    "fallback": True,
                }
                save_json_file(os.path.join(args.output_dir, '5-3_feedback_multi.json'), feedback_multi_fallback)
                save_json_file(os.path.join(args.output_dir, '5-3_feedback.json'), repair_feedback_dict)
                token_usage_dict[os.path.join(args.output_dir, '5-3_feedback_multi.json')] = {
                    "base_model": getattr(args, "base_model", ""),
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "skipped": True,
                }
                token_usage_dict[os.path.join(args.output_dir, '5-3_feedback.json')] = token_usage
                if token_usage_code_html is not None:
                    token_usage_dict[os.path.join(args.output_dir, 'code_html_selection.json')] = token_usage_code_html


    # save token usage
    save_json_file(token_usage_dict_file, token_usage_dict)
        
def repair_feedback(problem_statement, image_file_list, logger, args):
    def resolve_existing_dir(path_str: str) -> str:
        if not path_str:
            return path_str
        if os.path.isdir(path_str):
            return path_str
        if os.path.isabs(path_str):
            return path_str
        candidates = [
            os.path.normpath(os.path.join(os.getcwd(), path_str)),
            os.path.normpath(os.path.join(os.path.dirname(__file__), path_str)),
            os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(__file__)), path_str)),
        ]
        for candidate in candidates:
            if os.path.isdir(candidate):
                return candidate
        return path_str

    repair_feedback_dict = {}
    token_usage = {
        "base_model": getattr(args, "base_model", ""),
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "skipped": False,
    }

    resolved_output_dir = resolve_existing_dir(getattr(args, "output_dir", ""))
    bug_image_path = os.path.join(resolved_output_dir, 'IMAGE', '0.png')
    selected_patch_no = getattr(args, "selected_patch_no", None)
    patch_no_for_image = selected_patch_no if selected_patch_no is not None else args.val_patch_no
    fix_image_path = os.path.join(resolved_output_dir, 'IMAGE', str(patch_no_for_image) + '.png')

    if not os.path.isfile(bug_image_path) or not os.path.isfile(fix_image_path):
        token_usage["skipped"] = True
        logger.warning(
            "Repair feedback skipped due to missing screenshot(s): "
            f"bug_image={bug_image_path} exists={os.path.isfile(bug_image_path)}; "
            f"fix_image={fix_image_path} exists={os.path.isfile(fix_image_path)} "
            f"(patch_no_for_image={patch_no_for_image})"
        )
        repair_feedback_dict = {
            1: {
                "bug_analyze": "Skipped repair feedback: missing screenshot(s) for pixel/LLM comparison.",
                "patch_analyze": "",
                "final_answer": "UNKNOWN",
            }
        }
        return repair_feedback_dict, token_usage

    # open bug and patch images
    try:
        bug_image = Image.open(bug_image_path).convert('RGB')
        fix_image = Image.open(fix_image_path).convert('RGB')
    except Exception as e:
        token_usage["skipped"] = True
        logger.warning(f"Repair feedback skipped: failed to open screenshot(s): {e}")
        repair_feedback_dict = {
            1: {
                "bug_analyze": "Skipped repair feedback: failed to open screenshot(s).",
                "patch_analyze": "",
                "final_answer": "UNKNOWN",
            }
        }
        return repair_feedback_dict, token_usage

    # pixel-level matching
    if bug_image.size == fix_image.size:
        # comparing bug vs. patch images
        diff = ImageChops.difference(bug_image, fix_image)
        if diff.getbbox() is None:
            logger.info(f'Pixel-level matching successful, Patch_{patch_no_for_image} Fix fail')
            token_usage["skipped"] = True
            repair_feedback_dict = {
                1: {
                    "bug_analyze": "Bug and patch screenshots are pixel-identical.",
                    "patch_analyze": "Pixel-level diff is empty; patch likely did not change the rendering.",
                    "final_answer": "NO",
                }
            }
            return repair_feedback_dict, token_usage
        else:
            repair_feedback_dict = {}   # difference

    bug_image_list = get_bug_scenarion_images(bug_image_path, args)
    fix_image_list = get_bug_scenarion_images(fix_image_path, args)

    system_prompt, user_prompt = repair_feedback_prompt_construction(bug_image_list, fix_image_list, problem_statement, image_file_list)
    # logger.info(f'{user_prompt['bug_analyze']}')
    # logger.info(f'{user_prompt['fix_analyze']}')
    if is_openai_compatible_model(args.base_model):
        repair_feedback_dict, token_usage = openai_chat(system_prompt, user_prompt, args, args.code_reproduce_temperature, args.code_reproduce_samples)
    if is_claude_model(args.base_model):
        repair_feedback_dict, repair_feedback_str, token_usage = claude_chat(system_prompt, user_prompt, args, args.code_reproduce_temperature, args.code_reproduce_samples)
    logger.info(f'{repair_feedback_dict}')
    if 'yes' in repair_feedback_dict[1]['final_answer'].lower():
        logger.info(f"Patch_{patch_no_for_image} Repair_Feedback = {repair_feedback_dict[1]['final_answer']}, Resolved Issue")
    else:
        logger.info(f"Patch_{patch_no_for_image} Repair_Feedback = {repair_feedback_dict[1]['final_answer']}, Fix Fail")

    return repair_feedback_dict, token_usage


def merge_token_usage(target, source):
    if not isinstance(target, dict) or not isinstance(source, dict):
        return target
    target["prompt_tokens"] = int(target.get("prompt_tokens") or 0) + int(source.get("prompt_tokens") or 0)
    target["completion_tokens"] = int(target.get("completion_tokens") or 0) + int(source.get("completion_tokens") or 0)
    target["total_tokens"] = int(target.get("total_tokens") or 0) + int(source.get("total_tokens") or 0)
    if not target.get("base_model") and source.get("base_model"):
        target["base_model"] = source.get("base_model")
    target["skipped"] = bool(int(target.get("total_tokens") or 0) == 0)
    return target


def make_zero_token_usage(base_model: str = ""):
    return {
        "base_model": base_model,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "skipped": True,
    }

def clean_image_dir(image_dir: str, logger):
    try:
        if not os.path.isdir(image_dir):
            os.makedirs(image_dir, exist_ok=True)
            return
        for name in os.listdir(image_dir):
            full_path = os.path.join(image_dir, name)
            if os.path.isdir(full_path):
                try:
                    for root, dirs, files in os.walk(full_path, topdown=False):
                        for f in files:
                            try:
                                os.remove(os.path.join(root, f))
                            except Exception:
                                pass
                        for d in dirs:
                            try:
                                os.rmdir(os.path.join(root, d))
                            except Exception:
                                pass
                    os.rmdir(full_path)
                except Exception:
                    pass
                continue
            if os.path.isfile(full_path):
                try:
                    os.remove(full_path)
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"IMAGE cleanup failed: {e}")


def patch_validation(
    patches_edits_dict,
    instance_id,
    instance_repo_path,
    repo_structure_dict,
    logger,
    args,
    problem_statement=None,
    image_file_list=None,
):
    args.selected_patch_no = None

    repo_basename = os.path.basename(os.path.normpath(instance_repo_path))
    runtime_repo_meta = {
        "prism": {
            "family": "prismjs",
            "repo_label": "PrismJS/prism",
            "log_prefix": "PrismJS",
        },
        "highlight.js": {
            "family": "highlightjs",
            "repo_label": "highlightjs/highlight.js",
            "log_prefix": "highlightjs",
        },
        "Chart.js": {
            "family": "chartjs",
            "repo_label": "chartjs/Chart.js",
            "log_prefix": "chartjs",
        },
        "marked": {
            "family": "markedjs",
            "repo_label": "markedjs/marked",
            "log_prefix": "markedjs",
        },
    }

    if (
        repo_basename in runtime_repo_meta
        and args.task == "val"
        and getattr(args, "Patch_Select", "False") == "True"
        and int(float(getattr(args, "val_patch_no", "1"))) > 0
    ):
        runtime_meta = runtime_repo_meta[repo_basename]
        highlighter_family = runtime_meta["family"]
        repo_label = runtime_meta["repo_label"]
        log_prefix = runtime_meta["log_prefix"]

        start_no = int(float(getattr(args, "val_patch_no", "1")))
        max_no = int(getattr(args, "patch_generation_samples", 1))

        image_dir = os.path.join(args.output_dir, "IMAGE")
        clean_image_dir(image_dir, logger)
        baseline_png = os.path.join(image_dir, "0.png")
        code_html_path = os.path.join(instance_repo_path, "code.html")

        if problem_statement is None:
            problem_statement = ""
        if image_file_list is None:
            image_file_list = []

        def reset_repo_hard():
            reset_result = run_command(f'cd {instance_repo_path} && git reset --hard')
            logger.info(f"{log_prefix} repo reset --hard: {reset_result}")
            return reset_result

        reset_repo_hard()
        code_html_token_usage = {
            "base_model": getattr(args, "base_model", ""),
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "skipped": True,
        }
        if highlighter_family == "highlightjs":
            code_html_path, code_html_token_usage = ensure_highlightjs_code_html_for_val(
                instance_id,
                instance_repo_path,
                problem_statement,
                image_file_list,
                args,
                logger,
            )
        elif highlighter_family == "chartjs":
            code_html_path, code_html_token_usage = ensure_chartjs_code_html_for_val(
                instance_id,
                instance_repo_path,
                problem_statement,
                image_file_list,
                args,
                logger,
            )
        elif highlighter_family == "markedjs":
            code_html_path, code_html_token_usage = ensure_markedjs_code_html_for_val(
                instance_id,
                instance_repo_path,
                problem_statement,
                image_file_list,
                args,
                logger,
            )
        else:
            code_html_path, code_html_token_usage = ensure_prism_code_html_for_val(
                instance_id,
                instance_repo_path,
                problem_statement,
                image_file_list,
                args,
                logger,
            )
        if not os.path.isfile(code_html_path):
            logger.warning(f"code.html not found: {code_html_path}")
            return ""

        feedback_multi = {
            "instance_id": instance_id,
            "repo": repo_label,
            "baseline_png": os.path.join("IMAGE", "0.png"),
            "max_patch_no": max_no,
            "start_patch_no": start_no,
            "candidates": [],
            "selected_patch_no": None,
        }
        token_usage_sum = {
            "base_model": getattr(args, "base_model", ""),
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "skipped": True,
        }
        fallback_first_patch_no = None
        fallback_first_patch_diff = ""
        fallback_first_nonempty_patch_no = None
        fallback_first_nonempty_patch_diff = ""
        fallback_first_build_ok_patch_no = None
        fallback_first_build_ok_patch_diff = ""

        if check_nonempty_file(baseline_png):
            logger.info(f"Baseline screenshot exists, keep: {baseline_png}")
        else:
            baseline_ok = capture_ui_screenshot_with_retry(
                instance_id,
                instance_repo_path,
                baseline_png,
                logger,
                html_file="code.html",
            )
            if not baseline_ok:
                logger.warning(f"Failed to capture baseline screenshot after retries: {baseline_png}")

        code_html_selection = {
            "instance_id": instance_id,
            "records": [
                {
                    "html_file": "code.html",
                    "generation_mode": "template_llm_or_local_fallback",
                }
            ]
        }
        patch_applicability_cache = {}

        for patch_no in range(start_no, max_no + 1):
            patch_png = os.path.join(image_dir, f"{patch_no}.png")
            try:
                if os.path.isfile(patch_png):
                    os.remove(patch_png)
            except Exception:
                pass

            patch_key = pick_patch_key_for_index(patches_edits_dict, patch_no)
            patch_edits = patches_edits_dict.get(patch_key, {}) if patch_key else {}
            patch_has_edits = has_nonempty_patch_edits(patch_edits)
            patch_is_matchable = False
            if patch_key and patch_has_edits:
                cache_key = str(patch_key)
                if cache_key in patch_applicability_cache:
                    patch_is_matchable = patch_applicability_cache[cache_key]
                else:
                    patch_is_matchable = patch_edits_match_repo(
                        patch_edits,
                        instance_repo_path,
                        repo_structure_dict,
                    )
                    patch_applicability_cache[cache_key] = patch_is_matchable

            if patch_key and (not patch_has_edits or not patch_is_matchable):
                fallback_patch_key = select_matchable_patch_key(
                    patches_edits_dict,
                    preferred_patch_no=patch_no,
                    instance_repo_path=instance_repo_path,
                    repo_structure_dict=repo_structure_dict,
                    applicability_cache=patch_applicability_cache,
                )
                if fallback_patch_key and fallback_patch_key != patch_key:
                    fallback_reason = "matchable patch key" if patch_has_edits else "non-empty patch key"
                    logger.info(
                        f"{log_prefix} patch {patch_no}: use {fallback_reason} "
                        f"{fallback_patch_key} instead of {patch_key}."
                    )
                    patch_key = fallback_patch_key
                    patch_edits = patches_edits_dict.get(patch_key, {})
                    patch_has_edits = has_nonempty_patch_edits(patch_edits)
                    patch_is_matchable = True

            if not patch_key or not patch_has_edits or not patch_is_matchable:
                missing_reason = "missing patch key"
                if patch_key and patch_has_edits and not patch_is_matchable:
                    missing_reason = "search not found in repo"
                    logger.info(f"{log_prefix} patch {patch_no}: patch edits exist but SEARCH did not match repo, skip.")
                else:
                    logger.info(f"{log_prefix} patch {patch_no}: missing patch edits (no key found).")
                check_png_from_baseline_or_placeholder(
                    patch_png,
                    baseline_png,
                    logger,
                    reason=missing_reason,
                )
                feedback_multi["candidates"].append(
                    {
                        "patch_no": patch_no,
                        "patch_key": patch_key,
                        "build_ok": False,
                        "png": os.path.join("IMAGE", f"{patch_no}.png"),
                        "llm_final_answer": "UNKNOWN",
                    }
                )
                continue

            logger.info(f"{log_prefix} candidate patch {patch_no} (key={patch_key})")
            try:
                reset_repo_hard()
                patch_edits = patches_edits_dict[patch_key]
                try:
                    back_fill_patch(patch_edits, instance_repo_path, repo_structure_dict, logger)
                    logger.info(f"{log_prefix} patch {patch_no}: applied to repo worktree.")
                except Exception as e:
                    logger.info(f"{log_prefix} patch {patch_no}: apply failed: {e}")
                    check_png_from_baseline_or_placeholder(
                        patch_png,
                        baseline_png,
                        logger,
                        reason=f"patch apply failed for patch {patch_no}",
                    )
                    feedback_multi["candidates"].append(
                        {
                            "patch_no": patch_no,
                            "patch_key": patch_key,
                            "build_ok": False,
                            "png": os.path.join("IMAGE", f"{patch_no}.png"),
                            "llm_final_answer": "NO",
                        }
                    )
                    continue

                get_patch_diff_result = run_command(
                    f'cd {instance_repo_path} && git diff -- "*.js" "*.ts" "*.jsx" "*.tsx" "*.js.snap" "*.scss" "*.md" "*.lua" > changes.diff'
                )
                patch_diff = read_file(os.path.join(instance_repo_path, "changes.diff"))
                run_command(f'cd {instance_repo_path} && rm changes.diff')
                patch_diff_lines = len((patch_diff or "").splitlines())
                if get_patch_diff_result:
                    logger.info(
                        f"{log_prefix} patch {patch_no}: git diff command output: {get_patch_diff_result}"
                    )
                logger.info(
                    f"{log_prefix} patch {patch_no}: patch diff collected ({patch_diff_lines} lines)."
                )
                if fallback_first_patch_no is None:
                    fallback_first_patch_no = patch_no
                    fallback_first_patch_diff = patch_diff or ""
                if (
                    fallback_first_nonempty_patch_no is None
                    and len((patch_diff or "").splitlines()) > 1
                ):
                    fallback_first_nonempty_patch_no = patch_no
                    fallback_first_nonempty_patch_diff = patch_diff or ""

                if patch_diff_lines <= 1:
                    logger.info(
                        f"{log_prefix} patch {patch_no}: no repo diff after backfill, skip."
                    )
                    check_png_from_baseline_or_placeholder(
                        patch_png,
                        baseline_png,
                        logger,
                        reason=f"no repo diff after backfill for patch {patch_no}",
                    )
                    feedback_multi["candidates"].append(
                        {
                            "patch_no": patch_no,
                            "patch_key": patch_key,
                            "build_ok": False,
                            "png": os.path.join("IMAGE", f"{patch_no}.png"),
                            "llm_final_answer": "NO",
                        }
                    )
                    continue

                if getattr(args, "instance_prebuild_done", False):
                    build_cmd = getattr(args, "instance_prebuild_cmd", "") or make_build_cmd(instance_repo_path)
                    build_response = "Success" if getattr(args, "instance_prebuild_ok", False) else "Fail"
                    build_result = ""
                    logger.info(
                        f"{log_prefix} patch {patch_no}: reuse instance prebuild result "
                        f"({build_response}). cmd={build_cmd}"
                    )
                else:
                    build_cmd = make_build_cmd(instance_repo_path)
                    build_response, build_result = run_build_gate(
                        instance_repo_path,
                        build_cmd,
                        logger,
                        f"{log_prefix} patch {patch_no}",
                    )
                    logger.info(f"Wait {args.wait_time_after_build} S after REPO build/test...")
                    time.sleep(int(args.wait_time_after_build))

                if build_response == "Fail":
                    logger.info(f"{log_prefix} patch {patch_no}: build gate failed.")
                    check_png_from_baseline_or_placeholder(
                        patch_png,
                        baseline_png,
                        logger,
                        reason=f"build gate failed for patch {patch_no}",
                    )
                    feedback_multi["candidates"].append(
                        {
                            "patch_no": patch_no,
                            "patch_key": patch_key,
                            "build_ok": False,
                            "png": os.path.join("IMAGE", f"{patch_no}.png"),
                            "llm_final_answer": "NO",
                        }
                    )
                    continue
                if fallback_first_build_ok_patch_no is None:
                    fallback_first_build_ok_patch_no = patch_no
                    fallback_first_build_ok_patch_diff = patch_diff or ""

                capture_ok = capture_ui_screenshot_with_retry(
                    instance_id,
                    instance_repo_path,
                    patch_png,
                    logger,
                    html_file="code.html",
                )
                if not capture_ok:
                    check_png_from_baseline_or_placeholder(
                        patch_png,
                        baseline_png,
                        logger,
                        reason=f"capture failed for patch {patch_no}",
                    )

                prev_selected = getattr(args, "selected_patch_no", None)
                args.selected_patch_no = str(patch_no)
                repair_feedback_dict, token_usage = repair_feedback(problem_statement, image_file_list, logger, args)
                args.selected_patch_no = prev_selected

                final_answer = ""
                try:
                    final_answer = str(repair_feedback_dict[1].get("final_answer", "")).strip()
                except Exception:
                    final_answer = ""

                merge_token_usage(token_usage_sum, token_usage)

                feedback_multi["candidates"].append(
                    {
                        "patch_no": patch_no,
                        "patch_key": patch_key,
                        "build_ok": True,
                        "png": os.path.join("IMAGE", f"{patch_no}.png"),
                        "llm_feedback": repair_feedback_dict,
                        "llm_final_answer": final_answer,
                    }
                )

                if "yes" in (final_answer or "").lower():
                    logger.info(f"{log_prefix} patch {patch_no}: LLM YES, selected for submission.")
                    args.selected_patch_no = str(patch_no)
                    feedback_multi["selected_patch_no"] = patch_no

                    args.feedback_multi = feedback_multi
                    args.feedback_selected = repair_feedback_dict
                    args.token_usage_multi = token_usage_sum
                    args.token_usage_selected = make_zero_token_usage(getattr(args, "base_model", ""))
                    args.token_usage_code_html = code_html_token_usage
                    try:
                        save_json_file(os.path.join(args.output_dir, "code_html_selection.json"), code_html_selection)
                    except Exception as e:
                        logger.warning(f"Failed to save code_html_selection.json: {e}")
                    return patch_diff

                logger.info(f"{log_prefix} patch {patch_no}: LLM NO, try next patch.")
            finally:
                reset_repo_hard()

        # No patch satisfied (build_ok && LLM YES). Fallback to first patch diff for val submission.
        fallback_selected_patch_no = None
        fallback_selected_patch_diff = ""
        fallback_reason = "none"
        if (
            fallback_first_build_ok_patch_no is not None
            and len((fallback_first_build_ok_patch_diff or "").splitlines()) > 1
        ):
            fallback_selected_patch_no = fallback_first_build_ok_patch_no
            fallback_selected_patch_diff = fallback_first_build_ok_patch_diff
            fallback_reason = "first_build_ok_patch_diff"
        elif (
            fallback_first_nonempty_patch_no is not None
            and len((fallback_first_nonempty_patch_diff or "").splitlines()) > 1
        ):
            fallback_selected_patch_no = fallback_first_nonempty_patch_no
            fallback_selected_patch_diff = fallback_first_nonempty_patch_diff
            fallback_reason = "first_nonempty_patch_diff"
        elif fallback_first_patch_no is not None and len((fallback_first_patch_diff or "").splitlines()) > 1:
            fallback_selected_patch_no = fallback_first_patch_no
            fallback_selected_patch_diff = fallback_first_patch_diff
            fallback_reason = "first_patch_diff"

        if fallback_selected_patch_no is not None:
            logger.info(
                f"{log_prefix}: no patch passed both build gate and LLM check; "
                f"fallback to {fallback_reason} (patch_no={fallback_selected_patch_no})."
            )
            feedback_multi["selected_patch_no"] = fallback_selected_patch_no
            args.selected_patch_no = str(fallback_selected_patch_no)
            args.feedback_multi = feedback_multi
            args.feedback_selected = {
                1: {
                    "bug_analyze": "No candidate patch satisfied (build_ok && LLM YES).",
                    "patch_analyze": f"Fallback to {fallback_reason} (patch_no={fallback_selected_patch_no}) for submission.",
                    "final_answer": "NO",
                }
            }
            args.token_usage_multi = token_usage_sum
            args.token_usage_selected = make_zero_token_usage(getattr(args, "base_model", ""))
            args.token_usage_code_html = code_html_token_usage
            try:
                save_json_file(os.path.join(args.output_dir, "code_html_selection.json"), code_html_selection)
            except Exception as e:
                logger.warning(f"Failed to save code_html_selection.json: {e}")
            return fallback_selected_patch_diff

        default_patch_key = select_matchable_patch_key(
            patches_edits_dict,
            preferred_patch_no=start_no,
            instance_repo_path=instance_repo_path,
            repo_structure_dict=repo_structure_dict,
            applicability_cache=patch_applicability_cache,
        )
        if default_patch_key:
            logger.info(
                f"{log_prefix}: no patch passed both build gate and LLM check; "
                f"fallback to default patch key {default_patch_key} from 5-2_patches_edits_val.json."
            )
            try:
                reset_repo_hard()
                back_fill_patch(patches_edits_dict[default_patch_key], instance_repo_path, repo_structure_dict, logger)
                run_command(
                    f'cd {instance_repo_path} && git diff -- "*.js" "*.ts" "*.jsx" "*.tsx" "*.js.snap" "*.scss" "*.md" "*.lua" > changes.diff'
                )
                default_patch_diff = read_file(os.path.join(instance_repo_path, "changes.diff"))
                run_command(f'cd {instance_repo_path} && rm changes.diff')
                if len((default_patch_diff or "").splitlines()) > 1:
                    try:
                        selected_patch_no = int(float(str(default_patch_key).split("/")[0]))
                    except Exception:
                        selected_patch_no = start_no
                    feedback_multi["selected_patch_no"] = selected_patch_no
                    args.selected_patch_no = str(selected_patch_no)
                    args.feedback_multi = feedback_multi
                    args.feedback_selected = {
                        1: {
                            "bug_analyze": "No candidate patch satisfied (build_ok && LLM YES).",
                            "patch_analyze": f"Fallback to default patch key {default_patch_key} from 5-2_patches_edits_val.json.",
                            "final_answer": "NO",
                        }
                    }
                    args.token_usage_multi = token_usage_sum
                    args.token_usage_selected = make_zero_token_usage(getattr(args, "base_model", ""))
                    args.token_usage_code_html = code_html_token_usage
                    try:
                        save_json_file(os.path.join(args.output_dir, "code_html_selection.json"), code_html_selection)
                    except Exception as e:
                        logger.warning(f"Failed to save code_html_selection.json: {e}")
                    return default_patch_diff
            except Exception as e:
                logger.warning(f"{log_prefix}: default patch key fallback failed: {e}")
            finally:
                reset_repo_hard()

        logger.info(f"{log_prefix}: no patch passed both build gate and LLM check; writing empty changes_val.diff.")
        args.feedback_multi = feedback_multi
        args.feedback_selected = {
            1: {
                "bug_analyze": "No patch satisfied (build_ok && LLM YES).",
                "patch_analyze": "No candidate patch was selected.",
                "final_answer": "NO",
            }
        }
        args.token_usage_multi = token_usage_sum
        args.token_usage_selected = make_zero_token_usage(getattr(args, "base_model", ""))
        args.token_usage_code_html = code_html_token_usage
        try:
            save_json_file(os.path.join(args.output_dir, "code_html_selection.json"), code_html_selection)
        except Exception as e:
            logger.warning(f"Failed to save code_html_selection.json: {e}")
        args.selected_patch_no = None
        return ""

    patch_diff = ""
    fallback_first_patch_sample_no = None
    fallback_first_patch_diff = ""
    fallback_first_nonempty_patch_sample_no = None
    fallback_first_nonempty_patch_diff = ""
    for sample_no in patches_edits_dict.keys():
        if float(sample_no.split('/')[0]) >= float(args.val_patch_no):
            pass
        else:
            continue
        logger.info(f'Patch No. {sample_no}')
        
        
        # clean changed files
        clean_result = clean_repo(instance_repo_path, logger)

        # (optional) generate baseline screenshot on clean repo (bug scenario)
        if args.task == "val" and getattr(args, "Patch_Select", "False") == "True":
            baseline_png = os.path.join(args.output_dir, "IMAGE", "0.png")
            if not check_nonempty_file(baseline_png):
                capture_ui_screenshot_with_retry(instance_id, instance_repo_path, baseline_png, logger)
        # if 'chartjs__Chart.js-10806' in instance_repo_path or 'chartjs__Chart.js-11116' in instance_repo_path \
        #     or 'carbon-design-system' in instance_repo_path or 'PrismJS' in instance_repo_path:
        #     create_tmp_file = run_command(f'cd {instance_repo_path} && cd ../.. && mkdir -p tmp')
        #     clean_result = run_command(f'cd {instance_repo_path} && cp package.json ../../tmp/package.json && git reset --hard && cp ../../tmp/package.json ./package.json')
        #     logger.info(f'Do not Clean Repo Package.json')
        # elif 'openlayers' in instance_repo_path:
        #     create_tmp_file = run_command(f'cd {instance_repo_path} && cd ../.. && mkdir -p tmp')
        #     clean_result = run_command(f'cd {instance_repo_path} && cp config/tsconfig-build.json ../../tmp/tsconfig-build.json && git reset --hard && cp ../../tmp/tsconfig-build.json config/tsconfig-build.json')
        # else:
        #     clean_result = run_command(f'cd {instance_repo_path} && git reset --hard')
        #     logger.info(f'Repo Clean Result: {clean_result}')

        # backfill patch to repo
        patch_edits = patches_edits_dict[sample_no]
        # logger.info(f'{patch_edits}')

        # back_fill_patch(patch_edits, instance_repo_path, repo_structure_dict, logger)
        
        try:
            if float(args.val_patch_no) > 0:
                back_fill_patch(patch_edits, instance_repo_path, repo_structure_dict, logger)
                logger.info(f'Pre Backfill Finish.')
            else:
                logger.info(f'Show bug scenarion, do not backfill.')
        except Exception as e:
            logger.info(f'Pre Backfill Fail: {e}')

        logger.info(instance_repo_path)

        # get patch diff
        get_patch_diff_result = run_command(f'cd {instance_repo_path} && git diff -- "*.js" "*.ts" "*.jsx" "*.tsx" "*.js.snap" "*.scss" "*.md" "*.lua"> changes.diff')
        
        patch_diff = read_file(os.path.join(instance_repo_path, 'changes.diff'))
        if fallback_first_patch_sample_no is None:
            fallback_first_patch_sample_no = sample_no
            fallback_first_patch_diff = patch_diff or ""
        if (
            fallback_first_nonempty_patch_sample_no is None
            and len((patch_diff or "").splitlines()) > 1
        ):
            fallback_first_nonempty_patch_sample_no = sample_no
            fallback_first_nonempty_patch_diff = patch_diff or ""
        clean_patch_diff_result = run_command(f'cd {instance_repo_path} && rm changes.diff')
        logger.info(f'Patch Diff Have Saved ... {get_patch_diff_result}')

        # check if generate available patch. Note that if we only reply the bug scenario (i.e., patch_no = 0), we will skip this check step
        if float(args.val_patch_no) > 0:
            if len(patch_diff.split('\n')) > 1:
                pass
            else:
                continue
        else:
            logger.info(f'Bug Reply...')
            pass
        
        # repo build
        if args.patch_generation_samples >= 1 and args.task == 'val':
            if getattr(args, "instance_prebuild_done", False):
                build_cmd = getattr(args, "instance_prebuild_cmd", "") or make_build_cmd(instance_repo_path)
                build_response = 'Success' if getattr(args, "instance_prebuild_ok", False) else 'Fail'
                build_result = ''
                logger.info(f"Reuse instance prebuild result ({build_response}). CMD: {build_cmd}")
            else:
                build_cmd = make_build_cmd(instance_repo_path)
                build_response, build_result = run_build_gate(
                    instance_repo_path,
                    build_cmd,
                    logger,
                    'Repo',
                )
        else:
            build_response = 'NONE'
            logger.info(f'Repo Build No Need. Only 1 Patch.')

        
        if not getattr(args, "instance_prebuild_done", False):
            logger.info(f'Wait {args.wait_time_after_build} S after REPO build/test...')
            time.sleep(int(args.wait_time_after_build))  # wait 20 seconds after fix

        if build_response == 'Fail':
            logger.info(f'Current Patch No. {sample_no} Error, Run Next Patch...\n')
            # if line error, please try to refine this error patch
            continue
        else:
            selected_patch_no = str(sample_no.split('/')[0])
            args.selected_patch_no = selected_patch_no

            # (optional) generate screenshot for selected patch
            if args.task == "val" and getattr(args, "Patch_Select", "False") == "True":
                patch_png = os.path.join(args.output_dir, "IMAGE", f"{selected_patch_no}.png")
                if not check_nonempty_file(patch_png):
                    patch_ok = capture_ui_screenshot_with_retry(instance_id, instance_repo_path, patch_png, logger)
                    if not patch_ok:
                        check_png_from_baseline_or_placeholder(
                            patch_png,
                            os.path.join(args.output_dir, "IMAGE", "0.png"),
                            logger,
                            reason=f"selected patch screenshot failed: {selected_patch_no}",
                        )

            if build_response == 'NONE':
                logger.info(f'Repo Build No Need. Only 1 Patch. Skip This Step.')
            else:
                logger.info(f'Current Patch No. {sample_no} Build/Test Success, finish build process.\n')
            break

    # we should clean repo after validation
    # time.sleep(5)
    # run_command(f'cd {instance_repo_path} && git reset --hard')

    if (
        args.task == 'val'
        and float(args.val_patch_no) > 0
        and not getattr(args, "selected_patch_no", None)
    ):
        if len((fallback_first_patch_diff or "").splitlines()) > 1:
            args.selected_patch_no = str(fallback_first_patch_sample_no).split('/')[0]
            logger.info(
                "No patch passed build/test in val mode; "
                f"fallback to first patch diff ({fallback_first_patch_sample_no}) for submission."
            )
            return fallback_first_patch_diff
        if len((fallback_first_nonempty_patch_diff or "").splitlines()) > 1:
            args.selected_patch_no = str(fallback_first_nonempty_patch_sample_no).split('/')[0]
            logger.info(
                "No patch passed build/test in val mode; "
                f"fallback to first non-empty patch diff ({fallback_first_nonempty_patch_sample_no}) for submission."
            )
            return fallback_first_nonempty_patch_diff

    return patch_diff

def run_command(command, cwd=None):
    """Execute a shell command and return the result."""
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            shell=True,
            capture_output=True,
            text=True,
            encoding='utf-8',  # Specify the encoding explicitly
            errors='replace'
        )
        if result.returncode != 0:
            # print(f"Run Command {command} has Error: {result.stderr} {result.stdout.strip()}")
            return f"Run Command {command} has Error: {result.stderr} {result.stdout.strip()}"
        return result.stdout.strip()
    except UnicodeDecodeError as e:
        print(f"Unicode decode error: {e}")
        return "Unicode decode error"


def clean_repo(instance_repo_path, logger):
    # clean changed files
    lock_path = os.path.join(instance_repo_path, ".git", "index.lock")
    if os.path.isfile(lock_path):
        try:
            os.remove(lock_path)
            logger.info("Removed stale git index.lock before reset.")
        except Exception as e:
            logger.warning(f"Failed to remove git index.lock: {e}")
    if 'chartjs__Chart.js-10806' in instance_repo_path or 'chartjs__Chart.js-11116' in instance_repo_path \
        or 'carbon-design-system' in instance_repo_path or 'PrismJS' in instance_repo_path:
        create_tmp_file = run_command(f'cd {instance_repo_path} && cd ../.. && mkdir -p tmp')
        clean_result = run_command(f'cd {instance_repo_path} && cp package.json ../../tmp/package.json && git reset --hard && cp ../../tmp/package.json ./package.json')
        logger.info(f'Do not Clean Repo Package.json')
    elif 'openlayers' in instance_repo_path:
        create_tmp_file = run_command(f'cd {instance_repo_path} && cd ../.. && mkdir -p tmp')
        clean_result = run_command(f'cd {instance_repo_path} && cp config/tsconfig-build.json ../../tmp/tsconfig-build.json && git reset --hard && cp ../../tmp/tsconfig-build.json config/tsconfig-build.json')
        logger.info(f'Do not Clean Repo config')
    else:
        clean_result = run_command(f'cd {instance_repo_path} && git reset --hard')
        logger.info(f'Repo Clean All Files')
    logger.info(f'Repo Clean Result: {clean_result}')
    return clean_result



def patch_check(
    patches_edits_dict,
    instance_repo_path,
    repo_structure_dict,
    logger,
    args,
    bug_files_with_context_dict,
    problem_statement,
    image_file_list,
    token_usage_dict=None,
    patch_check_usage_file="",
):

    for sample_no in patches_edits_dict.keys():
        logger.info(f'Patch No. {sample_no}')
        
        # clean changed files


        if 'chartjs__Chart.js-10806' in instance_repo_path or 'chartjs__Chart.js-11116' in instance_repo_path \
            or 'carbon-design-system' in instance_repo_path or 'PrismJS' in instance_repo_path:
            create_tmp_file = run_command(f'cd {instance_repo_path} && cd ../.. && mkdir -p tmp')
            clean_result = run_command(f'cd {instance_repo_path} && cp package.json ../../tmp/package.json && git reset --hard && cp ../../tmp/package.json ./package.json')
            logger.info(f'Do not Clean Repo Package.json')
        elif 'openlayers' in instance_repo_path:
            create_tmp_file = run_command(f'cd {instance_repo_path} && cd ../.. && mkdir -p tmp')
            clean_result = run_command(f'cd {instance_repo_path} && cp config/tsconfig-build.json ../../tmp/tsconfig-build.json && git reset --hard && cp ../../tmp/tsconfig-build.json config/tsconfig-build.json')
        else:
            clean_result = run_command(f'cd {instance_repo_path} && git reset --hard')
            logger.info(f'Repo Clean Result: {clean_result}')

        # backfill patch to repo
        patch_edits = patches_edits_dict[sample_no]

        # back_fill_patch(patch_edits, instance_repo_path, repo_structure_dict, logger)
        
        try:
            if float(args.val_patch_no) > 0:
                back_fill_patch(patch_edits, instance_repo_path, repo_structure_dict, logger)
                logger.info(f'Pre Backfill Finish.')
            else:
                logger.info(f'Show bug scenarion, do not backfill.')
        except Exception as e:
            logger.info(f'Pre Backfill Fail: {e}')

        logger.info(instance_repo_path)

        # get patch diff
        get_patch_diff_result = run_command(f'cd {instance_repo_path} && git diff -- "*.js" "*.ts" "*.jsx" "*.tsx" "*.js.snap" "*.scss" "*.md" "*.lua"> changes.diff')
        if 'scratchfoundation' in args.output_dir:
            get_patch_diff_result = run_command(f'cd {instance_repo_path} && git diff -- "*.js" "*.ts" "*.jsx" "*.tsx" "*.js.snap" "*.scss" "*.md" "*.lua" "*.json" > changes.diff')
        patch_diff = read_file(os.path.join(instance_repo_path, 'changes.diff'))
        clean_patch_diff_result = run_command(f'cd {instance_repo_path} && rm changes.diff')
        logger.info(f'Patch Diff Have Saved ... {get_patch_diff_result}')

        
        # repo check
        build_cmd = make_check_cmd(instance_repo_path)
        if build_cmd.strip().lower() in {'ls', 'dir'}:
            logger.info('Repo check skipped (no check command configured).')
            continue

        build_result = run_command(f'cd {instance_repo_path} && {build_cmd}')
        build_failed = 'has Error:' in build_result

        logger.info(f'Wait {args.wait_time_after_build} S after REPO check...')
        time.sleep(int(args.wait_time_after_build))

        if build_failed:
            logger.info(f'Current Patch No. {sample_no} check failed, refine this patch...')
            system_prompt, user_prompt = patch_check_prompt_construction(bug_files_with_context_dict, build_result, patch_edits)

            if is_openai_compatible_model(args.base_model):
                refined_patch_dict, token_usage = openai_chat(system_prompt, user_prompt, args, 0.0, 1)
            elif is_claude_model(args.base_model):
                refined_patch_dict, refined_patch_str, token_usage = claude_chat(system_prompt, user_prompt, args, 0.0, 1)
            else:
                logger.info(f'Unknown base_model for patch check: {args.base_model}')
                continue
            if isinstance(token_usage_dict, dict) and patch_check_usage_file:
                token_usage_dict[patch_check_usage_file] = token_usage

            logger.info(f'{refined_patch_dict[1]}')
            refined_patch = json.loads(refined_patch_dict[1])
            patches_edits_dict[sample_no] = refined_patch
            break

        logger.info(f'Current Patch No. {sample_no} check success, continue.')
        continue

    # we should clean repo after validation
    # time.sleep(5)
    # run_command(f'cd {instance_repo_path} && git reset --hard')

    return patches_edits_dict

