import os
import subprocess
from openai import OpenAI
import anthropic
from pydantic import BaseModel
import json
import time
import re

import json5  # You might need to install this: pip install json5
from Code.model_utils import is_reasoning_model


def make_token_usage(base_model: str = ""):
    return {
        "base_model": base_model,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }


def add_token_usage(target, usage):
    if not isinstance(target, dict) or usage is None:
        return target
    target["prompt_tokens"] = int(target.get("prompt_tokens") or 0) + int(getattr(usage, "prompt_tokens", 0) or 0)
    target["completion_tokens"] = int(target.get("completion_tokens") or 0) + int(getattr(usage, "completion_tokens", 0) or 0)
    target["total_tokens"] = int(target.get("total_tokens") or 0) + int(getattr(usage, "total_tokens", 0) or 0)
    return target

def extract_json_from_text_refine(text: str, debug: bool = False):

    
    def pre_clean_text(raw: str) -> str:

        cleaned = re.sub(r'[^\x09\x0A\x0D\x20-\x7E\u4e00-\u9fa5\u3040-\u30FF\uFF00-\uFFEF\u0000-\uFFFF]', '', raw)
        

        cleaned = re.sub(r'\"{3,}', '"', cleaned)


        cleaned = re.sub(r'\\(C|M)-', r'\\\\\1-', cleaned)


        cleaned = re.sub(r'\\u\{([0-9a-fA-F]+)\}', lambda m: '\\u' + m.group(1).zfill(4)[-4:], cleaned)

        return cleaned

    def extract_main_json(cleaned_text: str) -> str:

        match = re.search(r'({[\s\S]*})', cleaned_text)
        if not match:
            raise ValueError("❌ ")
        return match.group(1)

    try:
        precleaned_text = pre_clean_text(text)

        if debug:
            print("🧼 ：")
            print(precleaned_text)

        json_str = extract_main_json(precleaned_text)

        if debug:
            print("🔍 ：")
            print(json_str)

        data = json5.loads(json_str)
        return data

    except Exception as e:
        return f"❌ JSON5 ERROR: {e}"

def extract_json_from_text(text: str, debug: bool = False):


    match = re.search(r'({[\s\S]*})', text)
    if not match:
        return "❌ "

    json_str = match.group(1)

    if debug:
        print("🔍 ")
        print(json_str)


    def clean_multiline_strings(s):

        lines = s.splitlines()
        result = []
        in_string = False
        for line in lines:
            line_strip = line.strip()
            if '"' in line_strip:
                quote_count = line_strip.count('"')
                if quote_count % 2 == 1:
                    in_string = not in_string
            if in_string:
                result.append(line_strip + '\\n')  
            else:
                result.append(line_strip)
        return ''.join(result)

    cleaned_json_str = clean_multiline_strings(json_str)

    if debug:
        print("✅ :")
        print(cleaned_json_str)

    # Try to parse the cleaned JSON payload.
    try:
        result_dict = json5.loads(cleaned_json_str)
        return result_dict
    except Exception as e:
        return f"❌ JSON5 ERROR: {e}"


def claude_chat(system_prompt, user_prompt, args, temperature, samples):

    claude_api_key = (
        getattr(args, "claude_api_key", "")
        or os.getenv("CLAUDE_API_KEY")
        or os.getenv("ANTHROPIC_API_KEY")
        or ""
    )
    claude_base_url = (
        getattr(args, "claude_base_url", "")
        or os.getenv("CLAUDE_BASE_URL")
        or os.getenv("ANTHROPIC_BASE_URL")
        or ""
    )
    client_kwargs = {}
    if claude_api_key:
        client_kwargs["api_key"] = claude_api_key
    if claude_base_url:
        client_kwargs["base_url"] = claude_base_url
    client = anthropic.Anthropic(**client_kwargs)

    # generate reproduce_code
    if "reproduce_code" in system_prompt and 'generate the reproduce code' in system_prompt:
        results = {}
        results_str = ''
        input_tokens, output_tokens = 0, 0
        key = 0
        while key < samples:
            completions = client.messages.create(
                max_tokens=8192,
                model=args.base_model,
                temperature=temperature,
                system=system_prompt,
                messages=[
                    {
                        "role": "user",
                        "content": user_prompt
                    }
                ]
            )
            # print(completions)
            key += 1
            for index, completion in enumerate(completions.content):
                result_str = completion.text
                result_dict = extract_json_from_text(completion.text)
                if not isinstance(result_dict, dict):
                    result_dict = extract_json_from_text_refine(completion.text)
                    if not isinstance(result_dict, dict):
                        result_dict = {"reproduce_code": str(completion.text)}
                   
                results_str += result_str
                results[key] = result_dict
            input_tokens += completions.usage.input_tokens
            output_tokens += completions.usage.output_tokens

    # locate file-level or find documents
    if "src/bug_file1.js" in system_prompt or "return all bug related documents" in system_prompt:
        results = {}
        results_str = ''
        input_tokens, output_tokens = 0, 0
        key = 0
        while key < samples:
            completions = client.messages.create(
                max_tokens=8192,
                model=args.base_model,
                temperature=temperature,
                system=system_prompt,
                messages=[
                    {
                        "role": "user",
                        "content": user_prompt
                    }
                ]
            )
            # print(completions)
            key += 1
            for index, completion in enumerate(completions.content):
                result_str = completion.text
                result_dict = extract_json_from_text(completion.text)
                if not isinstance(result_dict, dict):
                    result_dict = extract_json_from_text_refine(completion.text)
                results_str += result_str
                results[key] = result_dict
            input_tokens += completions.usage.input_tokens
            output_tokens += completions.usage.output_tokens


    # locate Class/Function-level
    if "class_name_1" in system_prompt and "function_name_1" in system_prompt:
        key = 0
        results = {}
        results_str = ''
        input_tokens, output_tokens = 0, 0
        while key < samples:
            completions = client.messages.create(
                max_tokens=8192,
                model=args.base_model,
                temperature=temperature,
                # n=samples,  # Request multiple completions
                system=system_prompt,
                messages=[
                    {
                        "role": "user",
                        "content": user_prompt
                    }
                ],
                # response_format={"type": "json"}
            )

            key += 1
            for index, completion in enumerate(completions.content):
                if 'JSON5 ERROR' not in str(extract_json_from_text(completion.text)):
                    result_str = completion.text
                    result_dict = extract_json_from_text(completion.text)
                    results_str += result_str
                    results[key] = result_dict
                else:
                    if 'JSON5 ERROR' in str(extract_json_from_text(completion.text)):
                        result_dict = extract_json_from_text_refine(completion.text)
                        results[key] = result_dict
                    result_str = completion.text
                    results_str += result_str
            input_tokens += completions.usage.input_tokens
            output_tokens += completions.usage.output_tokens


    # Generate Patches
    if "*SEARCH/REPLACE* edits" in system_prompt and "bug_code_snippets" in system_prompt:
        input_tokens, output_tokens = 0, 0
        results = {}
        results_str = ''

        completions = client.messages.create(
            max_tokens=8192,
            model=args.base_model,
            temperature=0.0,
            # n=1,  # Request one completions
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": user_prompt
                }
            ],
            # response_format=BugClassFunction
        )
        
        for key, completion in enumerate(completions.content):
            key += 1
            result_str = completion.text
            result_dict = completion.text
            results_str += result_str
            results[f'{1}/{samples}'] = result_dict
        input_tokens += completions.usage.input_tokens
        output_tokens += completions.usage.output_tokens
        
        if samples > 1:
            key = 1
            while key < samples:
                time.sleep(int(args.wait_time_after_api_request))  # wait -- seconds after api request

                current_temperature = temperature - (key-1) * 0.05
                current_temperature = max(current_temperature, 0.0)  

                completions = client.messages.create(
                    max_tokens=8000,
                    model=args.base_model,
                    temperature=current_temperature,
                    # n=samples-1,  # Request multiple completions
                    system=system_prompt,
                    messages=[
                        {
                            "role": "user",
                            "content": user_prompt
                        }
                    ]
                )
                key += 1
                for index, completion in enumerate(completions.content):
                    result_str = completion.text
                    result_dict = completion.text
                    results_str += result_str
                    results[f'{key}/{samples}'] = completion.text  # Ensure this is a string or serializable object
                input_tokens += completions.usage.input_tokens
                output_tokens += completions.usage.output_tokens

    # select patch
    if "selected_patch_id" in system_prompt:
        results = {}
        results_str = ''
        input_tokens, output_tokens = 0, 0
        key = 0
        temperature_selected = 0.0
        samples=1

        while key < samples:
            completions = client.messages.create(
                max_tokens=8192,
                model=args.base_model,
                temperature=temperature_selected,
                system=system_prompt,
                messages=[
                    {
                        "role": "user",
                        "content": user_prompt
                    }
                ]
            )

            key += 1

            full_text = ""
            for completion in completions.content:
                full_text += completion.text
            results_str += full_text
            match = re.search(r'"selected_patch_id"\s*:\s*"([^"]+)"', full_text)

            if match:
                selected_patch_id = match.group(1)
            else:
                result_dict = extract_json_from_text(full_text)
                if 'JSON5 ERROR' in str(result_dict):
                    result_dict = extract_json_from_text_refine(full_text)
                selected_patch_id = result_dict.get("selected_patch_id", None)

            results[key] = {
                "selected_patch_id": selected_patch_id
            }
            input_tokens += completions.usage.input_tokens
            output_tokens += completions.usage.output_tokens  

    # Extract token usage information
    token_usage = {}
    usage = completions.usage
    token_usage['base_model'] = args.base_model
    token_usage['prompt_tokens'] = input_tokens
    token_usage['completion_tokens'] = output_tokens
    token_usage['total_tokens'] = input_tokens + output_tokens

    time.sleep(int(args.wait_time_after_api_request))  # wait -- seconds after api request

    return results, results_str, token_usage  # Return a dict of results


def openai_chat(system_prompt, user_prompt, args, temperature, samples):

    class DocFiles(BaseModel):
        bug_scenario: str
        documents: list[str]
        explanation: str

    class ReproduceCode(BaseModel):
        bug_scenario: str
        reproduce_code: str
        explanation: str

    class BugFiles(BaseModel):
        bug_scenario: str
        bug_files: list[str]
        explanation: str
    
    class BugClassFunction(BaseModel):
        bug_scenario: str
        bug_classes: list[str]
        bug_functions: list[str]
        explanation: str

    class BugLine(BaseModel):
        bug_locations: str
        explanation: str

    class BugKeywords(BaseModel):
        bug_analyze: str
        bug_keywords: list[str]
        explanation: str

    class RepairFeedback(BaseModel):
        bug_analyze: str
        patch_analyze: str
        final_answer: str

    class PatchSelection(BaseModel):
        selected_patch_id: str

    class RefinedPatchFunction(BaseModel):
        Refined_Patch: str

    class VariantAppliedEdit(BaseModel):
        edit_index: int
        original_matched_text: str
        new_replacement_text: str
        note: str = ""

    class VariantFileGeneration(BaseModel):
        status: str
        rel_path: str
        content: str
        applied_edits: list[VariantAppliedEdit]
        error: str = ""


    openai_api_key = getattr(args, "openai_api_key", "") or os.getenv("OPENAI_API_KEY") or ""
    openai_base_url = (
        getattr(args, "openai_base_url", "")
        or os.getenv("OPENAI_BASE_URL")
        or os.getenv("OPENAI_API_BASE")
        or ""
    )

    client_kwargs = {}
    if openai_api_key:
        client_kwargs["api_key"] = openai_api_key
    if openai_base_url:
        client_kwargs["base_url"] = openai_base_url
    client = OpenAI(**client_kwargs)

    if is_reasoning_model(args.base_model):
        temperature = 1
    completions = None
    results = {}
    token_usage = make_token_usage(args.base_model)

    # find documents
    if "src/path/document_1.js" in system_prompt and 'Document Structure' in system_prompt:
        completions = client.beta.chat.completions.parse(
            model=args.base_model,
            temperature=temperature,
            n=samples,  # Request multiple completions
            messages=[
                {
                    "role": "system", 
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": user_prompt
                }
            ],
            response_format=DocFiles
        )
        add_token_usage(token_usage, completions.usage)

        results = {}
        for key, completion in enumerate(completions.choices):
            key += 1
            result = completion.message.parsed
            # results.append((result.bug_scenario, result.bug_files, result.explanation))
            results[key] = {}
            results[key]['documents'] = result.documents
            results[key]['explanation'] = result.explanation
            results[key]['bug_scenario'] = result.bug_scenario

    # generate reproduce code
    if "reproduce_code" in system_prompt and 'Related Documents' in system_prompt:
        completions = client.beta.chat.completions.parse(
            model=args.base_model,
            temperature=temperature,
            n=samples,  # Request multiple completions
            messages=[
                {
                    "role": "system", 
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": user_prompt
                }
            ],
            response_format=ReproduceCode
        )
        add_token_usage(token_usage, completions.usage)

        results = {}
        for key, completion in enumerate(completions.choices):
            key += 1
            result = completion.message.parsed
            # results.append((result.bug_scenario, result.bug_files, result.explanation))
            results[key] = {}
            results[key]['reproduce_code'] = result.reproduce_code
            results[key]['explanation'] = result.explanation
            results[key]['bug_scenario'] = result.bug_scenario


    # locate file-level
    if "src/bug_file1.js" in system_prompt:
        completions = client.beta.chat.completions.parse(
            model=args.base_model,
            temperature=temperature,
            n=samples,  # Request multiple completions
            messages=[
                {
                    "role": "system", 
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": user_prompt
                }
            ],
            response_format=BugFiles
        )
        add_token_usage(token_usage, completions.usage)

        results = {}
        for key, completion in enumerate(completions.choices):
            key += 1
            result = completion.message.parsed
            # results.append((result.bug_scenario, result.bug_files, result.explanation))
            results[key] = {}
            results[key]['bug_files'] = result.bug_files
            results[key]['explanation'] = result.explanation
            results[key]['bug_scenario'] = result.bug_scenario

    # generate bug keywords
    if "bug_keywords" in system_prompt and "Explanation of why these keywords may appear in the bug files." in system_prompt:
        completions = client.beta.chat.completions.parse(
            model=args.base_model,
            temperature=temperature,
            n=samples,  # Request multiple completions
            messages=[
                {
                    "role": "system", 
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": user_prompt
                }
            ],
            response_format=BugKeywords
        )
        add_token_usage(token_usage, completions.usage)

        results = {}
        for key, completion in enumerate(completions.choices):
            key += 1
            result = completion.message.parsed
            # results.append((result.bug_scenario, result.bug_files, result.explanation))
            results[key] = {}
            results[key]['bug_analyze'] = result.bug_analyze
            results[key]['bug_keywords'] = result.bug_keywords
            results[key]['explanation'] = result.explanation


    # locate Class/Function-level
    if "class_name_1" in system_prompt and "function_name_1" in system_prompt:
        completions = client.beta.chat.completions.parse(
            model=args.base_model,
            temperature=temperature,
            n=samples,  # Request multiple completions
            messages=[
                {
                    "role": "system", 
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": user_prompt
                }
            ],
            response_format=BugClassFunction
        )
        add_token_usage(token_usage, completions.usage)

        results = {}
        for key, completion in enumerate(completions.choices):
            key += 1
            result = completion.message.parsed
            # results.append((result.bug_scenario, result.bug_files, result.explanation))
            results[key] = {}
            results[key]['bug_classes'] = result.bug_classes
            results[key]['bug_functions'] = result.bug_functions
            results[key]['explanation'] = result.explanation
            results[key]['bug_scenario'] = result.bug_scenario

    # locate line-level
    if "why these bug lines are" in system_prompt and "output the bug line number" in system_prompt:
        completions = client.beta.chat.completions.parse(
            model=args.base_model,
            temperature=temperature,
            n=samples,  # Request multiple completions
            messages=[
                {
                    "role": "system", 
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": user_prompt
                }
            ],
            response_format=BugLine
        )
        add_token_usage(token_usage, completions.usage)

        results = {}
        for key, completion in enumerate(completions.choices):
            key += 1
            result = completion.message.parsed
            results[key] = {}
            try:
                results[key]['bug_locations'] = json.loads(result.bug_locations)
                results[key]['explanation'] = result.explanation
            except:
                results[key]['bug_locations'] = {}
                results[key]['explanation'] = ''

    # Generate Patches
    if "*SEARCH/REPLACE* edits" in system_prompt and "bug_code_snippets" in system_prompt:
        results = {}

        if is_reasoning_model(args.base_model): 
            temperature_patch=1
            temperature = 1
        else:
            temperature_patch=0.0
 
        completions = client.beta.chat.completions.parse(
            model=args.base_model,
            temperature=temperature_patch,
            n=1,  # Request one completions
            messages=[
                {
                    "role": "system", 
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": user_prompt
                }
            ],
            # response_format=BugClassFunction
        )
        add_token_usage(token_usage, completions.usage)
        for key, completion in enumerate(completions.choices):
            result = completion.message
            results[f'{1}/{samples}'] = result.content
        
        if samples > 1:
            completions = client.beta.chat.completions.parse(
                model=args.base_model,
                temperature=temperature,
                n=samples-1,  # Request multiple completions
                messages=[
                    {
                        "role": "system", 
                        "content": system_prompt
                    },
                    {
                        "role": "user",
                        "content": user_prompt
                    }
                ],
                # response_format=BugClassFunction
            )
            add_token_usage(token_usage, completions.usage)
            for key, completion in enumerate(completions.choices):
                
                key += 2
                result = completion.message
                results[f'{key}/{samples}'] = result.content  # Ensure this is a string or serializable object

    # select patch
    if "selected_patch_id" in system_prompt:
        results = {}
        
        if is_reasoning_model(args.base_model): 
            temperature_selected = 1
        else:
            temperature_selected = 0.0
        completions = client.beta.chat.completions.parse(
            model=args.base_model,
            temperature=temperature_selected,
            n=1,
            messages=[
                {
                    "role": "system", 
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": user_prompt
                }
            ],
            response_format=PatchSelection
        )
        add_token_usage(token_usage, completions.usage)
        
        for key, completion in enumerate(completions.choices):
            key += 1
            result = completion.message.parsed

            results[key] = {}
            results[key]['selected_patch_id'] = result.selected_patch_id



    # check patch
    if "Refined Patch" in system_prompt:
        results = {}

        completions = client.beta.chat.completions.parse(
            model=args.base_model,
            temperature=0.0,
            n=1,  # Request one completions
            messages=[
                {
                    "role": "system", 
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": user_prompt
                }
            ],
            response_format=RefinedPatchFunction
        )
        add_token_usage(token_usage, completions.usage)

        
        for key, completion in enumerate(completions.choices):
            key += 1
            result = completion.message.parsed
            results[key] = {}
            results[key] = result.Refined_Patch
 
    # analyze repair feedback
    if "infering what the correct rendering" in system_prompt:
        completions = client.beta.chat.completions.parse(
            model=args.base_model,
            temperature=temperature,
            n=samples, 
            messages=[
                {
                    "role": "system", 
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": user_prompt['bug_analyze']
                },
                {
                    "role": "user",
                    "content": user_prompt['fix_analyze']
                }
            ],
            response_format=RepairFeedback
        )
        add_token_usage(token_usage, completions.usage)

        results = {}
        for key, completion in enumerate(completions.choices):
            key += 1
            result = completion.message.parsed

            results[key] = {}
            results[key]['bug_analyze'] = result.bug_analyze
            results[key]['patch_analyze'] = result.patch_analyze
            results[key]['final_answer'] = result.final_answer

    # generate patch-variant file content (syntax-highlighting validation)
    if "VARIANT_FILE_GENERATION" in system_prompt:
        results = {}
        completions = client.beta.chat.completions.parse(
            model=args.base_model,
            temperature=0.0,
            n=1,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format=VariantFileGeneration,
        )
        add_token_usage(token_usage, completions.usage)
        for key, completion in enumerate(completions.choices):
            key += 1
            result = completion.message.parsed
            results[key] = {
                "status": result.status,
                "rel_path": result.rel_path,
                "content": result.content,
                "applied_edits": [
                    {
                        "edit_index": e.edit_index,
                        "original_matched_text": e.original_matched_text,
                        "new_replacement_text": e.new_replacement_text,
                        "note": getattr(e, "note", "") or "",
                    }
                    for e in (result.applied_edits or [])
                ],
                "error": getattr(result, "error", "") or "",
            }

    if completions is None:
        completions = client.chat.completions.create(
            model=args.base_model,
            temperature=temperature,
            n=samples,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        add_token_usage(token_usage, completions.usage)
        results = {}
        for idx, choice in enumerate(completions.choices):
            results[idx + 1] = choice.message.content

    time.sleep(int(args.wait_time_after_api_request))  # wait -- seconds after api request

    return results, token_usage  # Return a dict of results
