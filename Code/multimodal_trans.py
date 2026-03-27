import os
import json, re

try:
    from Code.llm_io_stream import openai_chat, claude_chat
    from Code.RAG import query_doc_file
    from Code.model_utils import is_claude_model, is_openai_compatible_model
except ImportError:
    from llm_io_stream import openai_chat, claude_chat
    from RAG import query_doc_file
    from model_utils import is_claude_model, is_openai_compatible_model


def doc_file_locate_prompt_construction(doc_structure, problem_statement, image_file_list, max_docs_number, args):
    max_docs_number_token = f'(at most {max_docs_number} bug-related documents)'

    obtain_relevant_files_prompt = f"""
I will give you the bug related information (i.e., Bug Report and Images) for your references, you need to find all bug image/scenario related documents {max_docs_number_token} in the Repo Document Dir.
    1. Read the bug report and view the bug scenarion images (if images are available) to describe the bug scenario images and analyze related elements (e.g., Button, TextInput, Background...) that may be involved in the buggy image; 
    2. Look the Document Structure to find bug images related documents that would need to understand and reproduce this issue; 
    3. Save all bug scenario related documents {max_docs_number_token} and explain why these documents are necessary to understand and reproduce this issue (bug scenario).
    
* Bug Report
```markdown
{problem_statement}
```

* Document Structure
```
{doc_structure}
```

"""
    
    system_prompt = """
The user want to understand and reproduce the issue in the Bug Report. You can find and provide necessary bug-related documents in the Repo Documents to help the user understand and reproduce current issue.
The user will provide the Bug Report (may attach the bug images) and Document Structure. Please describe the bug scenario images, then return all bug related documents for user references. 

EXAMPLE INPUT: 

* Bug Report
```markdown
problem_statement
```

* Document Structure
```
repo_documents
```

* Bug Scenario Images
'''
image
'''

EXAMPLE OUTPUT:
{   
    "bug_scenario": "Description of the bug scenario.",
    "documents": ["src/path/document_1.js", "src/path/document_2.js", "src/path/document_3.js"],
    "explanation": "Explanation of why these documents are necessary to understand and reproduce this issue (bug scenario)."
}
"""

    if is_claude_model(args.base_model):
        system_prompt = """
The user want to understand and reproduce the issue in the Bug Report. You can find and provide necessary bug-related documents in the Repo Documents to help the user understand and reproduce current issue.
The user will provide the Bug Report (may attach the bug images) and Document Structure. Please describe the bug scenario images, then return all bug related documents for user references. 

Note that you need analyze this feedback and output in JSON format with keys: "bug_scenario" (str), "documents" (list), and "explanation" (str).

EXAMPLE OUTPUT:
{   
    "bug_scenario": "Description of the bug scenario.",
    "documents": ["src/path/document_1.js", "src/path/document_2.js", "src/path/document_3.js"],
    "explanation": "Explanation of why these documents are necessary to understand and reproduce this issue (bug scenario)."
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


def reproduce_code_generate_prompt_construction(problem_statement, image_file_list, doc_files_content, args):
    obtain_relevant_files_prompt = f"""
I will give you the Bug Report and related Documents for your references, you need to generate Reproduce Code to reproduce the bug scenarion (i.e., bug images).
    1. Read the bug report and view the bug scenarion images (if images are available) to describe the bug scenario images; 
    2. Look Related Documents to understand the bug scenario and learn about how to reproduce it; 
    3. Generate reproduce code to reproduce the bug scenario.
        Note that if the bug images is not a visual symptom (i.e., it is not possible or suitable to generate reproduced code for bug images), please output a description and analysis of the visual images.
    
* Bug Report
```markdown
{problem_statement}
```

* Related Documents
{doc_files_content}


"""
    
    system_prompt = """
The user want to reproduce the issue in the Bug Report. You need to read the bug report and related documents to help the user understand and reproduce current issue.
The user will provide the Bug Report (may attach the bug images) and Related Documents. Please describe the bug scenario images, then generate the reproduce code for user references. 

EXAMPLE INPUT: 

* Bug Report
```markdown
problem_statement
```

* Related Documents
```document_1.md
document content...
```
```document_2.md
document content...
```
```document_3.js
document content...
```

* Bug Scenario Images
'''
image
'''

EXAMPLE OUTPUT:
{   
    "bug_scenario": "Description of the bug scenario.",
    "reproduce_code": "The reproduce code to reproduce the bug scenario (If the reproduced code does not apply to the current instance, only output the description and analysis of the bug images)",
    "explanation": "Explanation of how to generate the reproduce code."
}
"""

    if is_claude_model(args.base_model):
        system_prompt = """
The user want to reproduce the issue in the Bug Report. You need to read the bug report and related documents to help the user understand and reproduce current issue.
The user will provide the Bug Report (may attach the bug images) and Related Documents. Please describe the bug scenario images, then generate the reproduce code for user references. 

Note that you need analyze this feedback and output in JSON format with keys: "bug_scenario" (str), "reproduce_code" (str), and "explanation" (str).
Note that if the bug images is not a visual symptom (i.e., it is not possible or suitable to generate reproduced code for bug images), please output a description and analysis of the visual image.

EXAMPLE OUTPUT:
{   
    "bug_scenario": "Description of the bug scenario.",
    "reproduce_code": "The reproduce code to reproduce the bug scenario (If the reproduced code does not apply to the current instance, only output the description and analysis of the bug images)",
    "explanation": "Explanation of how to generate the reproduce code."
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


def get_all_file_paths(repo_structure_dict, current_path=""):
    """
    get repo_structure_dict 
    
    :param repo_structure_dict: dict
    :param current_path: str
    :return: list
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

def check_doc_files_path(doc_files_dict, all_doc_files_list):
    for index in doc_files_dict:
        doc_files_list = doc_files_dict[index]['documents']
        new_doc_files_list = []
        
        for doc_file in doc_files_list:
            if doc_file in all_doc_files_list or '/'+doc_file in all_doc_files_list:
                new_doc_files_list.append(doc_file)

        doc_files_dict[index]['documents'] = new_doc_files_list

    return doc_files_dict 

# check all bug file path
def check_bug_files_path(bug_files_dict, repo_structure_dict):
    all_files_list = get_all_file_paths(repo_structure_dict)
    all_files_list = list(set(all_files_list))
    all_files_list = [file[1:] for file in all_files_list]
    # print(all_files_list)

    for index in bug_files_dict:
        bug_files_list = bug_files_dict[index]['documents']
        new_bug_files_list = []

        for bug_file in bug_files_list:
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
        
        bug_files_dict[index]['documents'] = new_bug_files_list
    return bug_files_dict, all_files_list

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

# def save_json_file(file_path, data):
#     with open(file_path, 'w') as json_file:
#         json.dump(data, json_file, indent=4) 


def contains_chinese(obj):
    if isinstance(obj, str):
        return bool(re.search(r'[\u4e00-\u9fff]', obj)) 
    elif isinstance(obj, dict):
        return any(contains_chinese(value) for value in obj.values())
    elif isinstance(obj, (list, tuple, set)):
        return any(contains_chinese(item) for item in obj)  
    return False  

def save_json_file(file_path, data):
    """ensure_ascii=False"""
    contains_cn = contains_chinese(data)  
    with open(file_path, 'w', encoding='utf-8') as json_file:
        json.dump(data, json_file, indent=4, ensure_ascii=not contains_cn)


def normalize_doc_files_dict(doc_files_dict):
    if not isinstance(doc_files_dict, dict) or not doc_files_dict:
        return None

    if isinstance(doc_files_dict.get("documents"), list):
        doc_files_dict = {"1": doc_files_dict}

    normalized = {}
    for key, value in doc_files_dict.items():
        if not isinstance(value, dict):
            return None
        documents = value.get("documents")
        if not isinstance(documents, list):
            return None
        normalized[str(key)] = {
            "bug_scenario": value.get("bug_scenario", "") if isinstance(value.get("bug_scenario", ""), str) else "",
            "documents": [doc for doc in documents if isinstance(doc, str)],
            "explanation": value.get("explanation", "") if isinstance(value.get("explanation", ""), str) else "",
        }

    return normalized


def normalize_reproduce_code_dict(reproduce_code_dict):
    if not isinstance(reproduce_code_dict, dict) or not reproduce_code_dict:
        return None

    if "reproduce_code" in reproduce_code_dict:
        reproduce_code_dict = {"1": reproduce_code_dict}

    normalized = {}
    for key, value in reproduce_code_dict.items():
        if not isinstance(value, dict):
            return None
        reproduce_code = value.get("reproduce_code")
        if not isinstance(reproduce_code, str):
            return None
        normalized[str(key)] = {
            "bug_scenario": value.get("bug_scenario", "") if isinstance(value.get("bug_scenario", ""), str) else "",
            "reproduce_code": reproduce_code,
            "explanation": value.get("explanation", "") if isinstance(value.get("explanation", ""), str) else "",
        }

    return normalized


def get_empty_doc_files_dict(reason=""):
    return {
        "1": {
            "bug_scenario": "",
            "documents": [],
            "explanation": reason,
        }
    }



def get_doc_structure(directory_path, args):
    """Create the structure of the repository documents directory by reading .md files."""
    structure = {}

    EXCLUDED_DIRS = {
    "node_modules", "test", "test", "dist", "build", "out", "auto", "__tests__"
    }

    if 'quarto' in directory_path or 'GoogleChrome' in directory_path: EXCLUDED_DIRS = {}

    for root, dirs, files in os.walk(directory_path):
        if args.File_Sort == 'True':
            dirs[:] = sorted([d for d in dirs if d not in EXCLUDED_DIRS]) # ignor non-src dirs
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
            if file_name.endswith(".md") or file_name.endswith(".mdx") or file_name.endswith(".qmd") or file_name.endswith("md"):
                file_path = os.path.join(root, file_name)
                with open(file_path, 'r', encoding='utf-8') as file:
                    file_content = file.read()
                curr_struct[file_name] = {"doc_content": file_content}
            elif file_name.endswith(".jsx") or file_name.endswith(".tsx"):
                file_path = os.path.join(root, file_name)
                with open(file_path, 'r', encoding='utf-8') as file:
                    file_content = file.read()
                curr_struct[file_name] = {"doc_content": file_content}
            elif file_name.endswith(".js") or file_name.endswith(".ts"):
                file_path = os.path.join(root, file_name)
                with open(file_path, 'r', encoding='utf-8') as file:
                    file_content = file.read()
                curr_struct[file_name] = {"doc_content": file_content}
            else:
                curr_struct[file_name] = {"doc_content": ""}

    return structure

def show_doc_structure(structure, args, spacing=0) -> str:
    """pprint the project structure"""

    pp_string = ""

    for key, value in structure.items():
        if "." in key:
            if ".json" in key and 'lighthouse' in args.output_dir:
                continue
            if ".jsx" in key and 'alibaba-fusion' in args.output_dir and 'alibaba-fusion__next-4859' not in args.output_dir:
                pass
            elif ".tsx" in key and 'alibaba-fusion' in args.output_dir and 'alibaba-fusion__next-4859' not in args.output_dir:
                pass
            elif ".js" in key and 'grommet' not in args.output_dir and 'carbon' not in args.output_dir and 'quarto' not in args.output_dir:
                pass
            elif ".qmd" in key and 'quarto' in args.output_dir:
                pass
            elif ".md" in key:
                pass
            elif ".mdx" in key:
                pass
            else:
                continue  # skip none js files
        if "." in key or "doc_content" in value:
            pp_string += " " * spacing + str(key) + "\n"
        else:
            pp_string += " " * spacing + str(key) + "/" + "\n"
        if "doc_content" not in value:
            pp_string += show_doc_structure(value, args, spacing + 4)

    return pp_string

def get_doc_path(instance_id, instance_repo_path):

    doc_path = os.path.join(instance_repo_path)

    if 'alibaba-fusion' in instance_id:
        doc_path = os.path.join(instance_repo_path, 'docs')
        if 'alibaba-fusion__next-4806' in instance_repo_path:
            doc_path = os.path.join(instance_repo_path, 'components')
        if 'alibaba-fusion__next-4859' in instance_repo_path:
            doc_path = os.path.join(instance_repo_path, 'components')

    if 'bpmn-io' in instance_id:
        doc_path = os.path.join(instance_repo_path, 'docs')

    if "carbon-design-system" in instance_id:
        doc_path = os.path.join(instance_repo_path, 'packages/react/src/components')

    if 'eslint' in instance_id:
        doc_path = os.path.join(instance_repo_path, 'docs')

    if 'GoogleChrome' in instance_id:
        # doc_path = os.path.join(instance_repo_path, 'docs')
        doc_path = os.path.join(instance_repo_path, 'lighthouse-core/test')

        if os.path.exists(doc_path):
            pass
        else:
            doc_path = os.path.join(instance_repo_path, 'docs')
        
    if 'prettier' in instance_id:
        doc_path = os.path.join(instance_repo_path, 'docs')

    if 'quarto' in instance_id:
        doc_path = os.path.join(instance_repo_path, 'tests')


    if 'Chart' in instance_id:
        doc_path = os.path.join(instance_repo_path, 'docs')

    if 'p5.js' in instance_id:
        doc_path = os.path.join(instance_repo_path, 'test')


    return doc_path


def Image2Code(logger, args, repo, instance_id, instance_repo_path, token_usage_dict, problem_statement, image_file_list):
    
    max_docs_number = args.max_candidate_doc_files

    reproduce_code = """
"""
    
    # 1. get the documents structure
    doc_structure_dict_file = os.path.join(args.output_dir, '1-1-1_doc_structure.json')
    doc_path = get_doc_path(instance_id, instance_repo_path)
    logger.info(f'Document Path: {doc_path}')
    if os.path.isfile(doc_structure_dict_file):
        logger.info(f'Doc Structure has existed: {doc_structure_dict_file}')
        doc_structure_dict = read_json_file(doc_structure_dict_file)
        doc_structure = show_doc_structure(doc_structure_dict, args)
    else:
        doc_structure_dict = get_doc_structure(doc_path, args)
        doc_structure = show_doc_structure(doc_structure_dict, args)
        save_json_file(doc_structure_dict_file, doc_structure_dict)
        save_file(doc_structure_dict_file.replace('.json','.txt'), doc_structure)

    # return

    # 2. get the bug related documents
    doc_files_dict_file = os.path.join(args.output_dir, '1-1-2_doc_files.json')
    doc_files_dict = None
    if os.path.isfile(doc_files_dict_file):
        logger.info(f'Doc Files has existed: {doc_files_dict_file}')
        doc_files_dict = normalize_doc_files_dict(read_json_file(doc_files_dict_file))
        if doc_files_dict is None:
            logger.warning(f'Invalid cached doc files format: {doc_files_dict_file}. Regenerating this step.')

    if doc_files_dict is None:
        
        system_prompt, user_prompt = doc_file_locate_prompt_construction(doc_structure, problem_statement, image_file_list, max_docs_number, args)
        if is_openai_compatible_model(args.base_model):
            doc_files_dict, token_usage = openai_chat(system_prompt, user_prompt, args, args.bug_docs_temperature, args.bug_docs_samples)
        if is_claude_model(args.base_model):
            doc_files_dict, doc_files_str, token_usage = claude_chat(system_prompt, user_prompt, args, args.bug_docs_temperature, args.bug_docs_samples)
        doc_files_dict = normalize_doc_files_dict(doc_files_dict)
        if doc_files_dict is None:
            logger.warning('Invalid doc files response from LLM. Continuing with an empty document list.')
            doc_files_dict = get_empty_doc_files_dict('Invalid doc file selection response from LLM.')
        save_json_file(doc_files_dict_file, doc_files_dict)
        token_usage_dict[doc_files_dict_file] = token_usage

    # return
    # 2.1 get the bug related documents by RAG embedding
    doc_files_dict_with_RAG_file = os.path.join(args.output_dir, '1-1-2_doc_files_with_RAG.json')
    all_doc_files_list = get_all_file_paths(doc_structure_dict)
    # if repo in ["alibaba-fusion/next", "carbon-design-system/carbon", "eslint/eslint", "grommet/grommet"]:
    all_doc_files_list = [file_path for file_path in all_doc_files_list if ".md" in file_path or ".mdx" in file_path]
    
    first_key = next(iter(doc_files_dict))  
    chat_model_doc_list = doc_files_dict[first_key]["documents"]



    chat_model_dirs = set(os.path.dirname(path) for path in chat_model_doc_list)
    print(chat_model_dirs)
    rag_doc_files_list = []

    for path in all_doc_files_list:
        for chat_path in chat_model_dirs:
            if chat_path in path and path not in rag_doc_files_list:
                rag_doc_files_list.append(path)
                break
    

    if args.doc_RAG_query == 'True':
        if os.path.isfile(doc_files_dict_with_RAG_file) is False:

            logger.info(f'ALL Documents:\n{all_doc_files_list}')
            logger.info(f'RAG Documents:\n{rag_doc_files_list}')
            # return
            doc_files_rag_list = []
       
            doc_files_rag = query_doc_file(logger, doc_path, rag_doc_files_list, max_docs_number, problem_statement)
            doc_files_rag_list.extend(doc_files_rag)
            logger.info(f'RAG Query Documents:\n{doc_files_rag_list}')
            
            doc_files_dict[first_key]["documents"].extend(doc_files_rag_list)
            save_json_file(doc_files_dict_with_RAG_file, doc_files_dict)
        else:
            rag_doc_files_dict = normalize_doc_files_dict(read_json_file(doc_files_dict_with_RAG_file))
            if rag_doc_files_dict is None:
                logger.warning(f'Invalid cached RAG doc files format: {doc_files_dict_with_RAG_file}. Ignoring it.')
            else:
                doc_files_dict = rag_doc_files_dict

    # 3. check the file path and fix it
    
    doc_files_dict, all_files_list = check_bug_files_path(doc_files_dict, doc_structure_dict)
    if 'grommet' in instance_id :
        doc_files_dict = check_doc_files_path(doc_files_dict, all_doc_files_list)
    save_json_file(os.path.join(args.output_dir, '1-1-3_doc_files_checked.json'), doc_files_dict)

    
    # 4. compressing all doc files
    compressed_doc_files_dict_file = os.path.join(args.output_dir, '1-1-4_compressed_doc_files.json')
    if os.path.isfile(compressed_doc_files_dict_file):
        logger.info(f'Compressed Doc Files has existed: {compressed_doc_files_dict_file}')
        compressed_doc_files_dict = read_json_file(compressed_doc_files_dict_file)
    else:
        
        compressed_doc_files_dict = {}
        doc_files_list = list(dict.fromkeys(sum([doc_files_dict[key]['documents'] for key in doc_files_dict.keys()], [])))

        for index, doc_file in enumerate(doc_files_list[:max_docs_number]):
            doc_file_path = os.path.join(doc_path, doc_file)
            logger.info(doc_path)
            logger.info(doc_file_path)
            
            compressed_doc_files_dict[index+1] = {}
            compressed_doc_files_dict[index+1]['document_name'] =  doc_file_path.replace(instance_repo_path,'')
            compressed_doc_files_dict[index+1]['document_content'] = read_file(doc_file_path)
        save_json_file(compressed_doc_files_dict_file, compressed_doc_files_dict)

    
    # doc_files_content = f'{doc_files_dict}' + '\n\n------------------------------------------\n\n'
    doc_files_content = ''
    
    for index, doc_key in enumerate(compressed_doc_files_dict.keys()):
        doc_file_name = compressed_doc_files_dict[doc_key]['document_name']
            
        doc_content = f"""

```{doc_file_name}
{compressed_doc_files_dict[doc_key]['document_content']}
```

"""     
        if len(doc_content.split('\n')) > 1500:
            continue
        doc_files_content += "".join(doc_content)
        # avoid too much docs
        if len(doc_files_content.split('\n')) > 1500:
            break
    
    save_file(compressed_doc_files_dict_file.replace('.json','.txt'), doc_files_content) 


    # 5. generate reproduce code
    reproduce_code_dict_file = os.path.join(args.output_dir, '1-1-5_reproduce_code.json')
    reproduce_code_dict = {}
    if os.path.isfile(reproduce_code_dict_file):
        logger.info(f'Reproduce Code File has existed: {reproduce_code_dict_file}')
        reproduce_code_dict = normalize_reproduce_code_dict(read_json_file(reproduce_code_dict_file))
        if reproduce_code_dict is None:
            logger.warning(f'Invalid cached reproduce code format: {reproduce_code_dict_file}. Regenerating this step.')

    if not reproduce_code_dict:
        system_prompt, user_prompt = reproduce_code_generate_prompt_construction(problem_statement, image_file_list, doc_files_content, args)
        if is_openai_compatible_model(args.base_model):
            reproduce_code_dict, token_usage = openai_chat(system_prompt, user_prompt, args, args.code_reproduce_temperature, args.code_reproduce_samples)
        if is_claude_model(args.base_model):
            reproduce_code_dict, reproduce_code_str, token_usage = claude_chat(system_prompt, user_prompt, args, args.code_reproduce_temperature, args.code_reproduce_samples)
            # save_file(reproduce_code_dict_file.replace('.json', '_full.txt'), reproduce_code_str)
        reproduce_code_dict = normalize_reproduce_code_dict(reproduce_code_dict)
        if reproduce_code_dict is None:
            logger.warning('Invalid reproduce code response from LLM. Continuing with an empty reproduce code block.')
            reproduce_code_dict = {
                "1": {
                    "bug_scenario": "",
                    "reproduce_code": "",
                    "explanation": "Invalid reproduce code response from LLM.",
                }
            }
        save_json_file(reproduce_code_dict_file, reproduce_code_dict)
        token_usage_dict[reproduce_code_dict_file] = token_usage
        
    first_key = next(iter(reproduce_code_dict))
    reproduce_code = reproduce_code_dict[first_key]['reproduce_code']
    save_file(reproduce_code_dict_file.replace('.json', '.txt'), reproduce_code)



    logger.info(f'Documents Path: {doc_path}')



    return reproduce_code_dict, reproduce_code, doc_files_dict, compressed_doc_files_dict, doc_files_content, token_usage_dict
