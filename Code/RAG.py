import os
import openai
import numpy as np
from openai import OpenAI

try:
    import faiss  # type: ignore
except Exception:  # pragma: no cover
    faiss = None


# Use standard OPENAI_* env vars only. Keep OPENAI_API_BASE as a base_url alias.
openai.api_key = os.getenv("OPENAI_API_KEY") or ""
openai_base_url = (
    os.getenv("OPENAI_BASE_URL")
    or os.getenv("OPENAI_API_BASE")
    or ""
)


client_kwargs = {}
if openai.api_key:
    client_kwargs["api_key"] = openai.api_key
if openai_base_url:
    client_kwargs["base_url"] = openai_base_url

client_cache = None


def get_openai_client():
    global client_cache
    if client_cache is not None:
        return client_cache

    runtime_api_key = openai.api_key or os.getenv("OPENAI_API_KEY") or ""
    runtime_base_url = (
        os.getenv("OPENAI_BASE_URL")
        or os.getenv("OPENAI_API_BASE")
        or openai_base_url
        or ""
    )

    runtime_kwargs = {}
    if runtime_api_key:
        runtime_kwargs["api_key"] = runtime_api_key
    if runtime_base_url:
        runtime_kwargs["base_url"] = runtime_base_url

    client_cache = OpenAI(**runtime_kwargs)
    return client_cache


# 1. Embedding
def get_embedding(text):
    client = get_openai_client()
    response = client.embeddings.create(
        input=text,
        model="text-embedding-3-small"
        # model="text-embedding-ada-002"
    )
    return response.data[0].embedding


def load_code_files(repo_path):
    code_files = {}
    for root, _, files in os.walk(repo_path):
        for file in files:
            if file.endswith((".js", ".jsx", ".ts", ".tsx", ".scss", ".lua", ".json")):
                with open(os.path.join(root, file), "r", encoding="utf-8") as f:
                    code_files[os.path.join(root, file)] = f.read()
    return code_files


def normalize_candidate_path(root_path, rel_path):
    rel = str(rel_path or "").strip().replace("\\", os.sep).replace("/", os.sep)
    while rel.startswith(os.sep):
        rel = rel[len(os.sep):]
    return os.path.normpath(os.path.join(root_path, rel))


def build_faiss_index(logger, file_contents):
    file_paths = list(file_contents.keys())
    if not file_paths:
        if logger is not None:
            logger.warning("RAG query skipped: no candidate files found.")
        return None, []

    embeddings = []
    for file_path, content in file_contents.items():
        embeddings.append(get_embedding((content or "")[:2000]))

    dimension = len(embeddings[0])
    index = faiss.IndexFlatL2(dimension)
    index.add(np.array(embeddings).astype("float32"))
    return index, file_paths

def query_doc_file(logger, doc_repo_path, doc_files_path, top_k_files, issue_description):

    if faiss is None:
        raise ImportError("Missing optional dependency 'faiss' (e.g. `pip install faiss-cpu`) required for RAG features.")

    all_doc_files_path = [
        normalize_candidate_path(doc_repo_path, doc_path)
        for doc_path in (doc_files_path or [])
        if str(doc_path or "").strip()
    ]
    
    # logger.info(f'Query Files List:\n{all_doc_files_path}')

    code_files = {}
    for doc_file_name in all_doc_files_path:
        with open(doc_file_name, "r", encoding="utf-8") as f:
            try:
                code_files[doc_file_name] = f.read()
            except:
                code_files[doc_file_name] = ''
        
    index, file_paths = build_faiss_index(logger, code_files)
    if index is None:
        return []


    # issue_description = """blendMode not working when doing point() drawing in webgl"""
    issue_embedding = get_embedding(issue_description)

    search_k = min(max(1, int(top_k_files)), len(file_paths))
    D, I = index.search(np.array([issue_embedding]).astype('float32'), k=search_k)


    bug_files_rag = []
    # print("Potential bug files:")
    for i in I[0]:
        bug_files_rag.append(file_paths[i].replace(doc_repo_path + '/', '').replace('\\','/'))

    return bug_files_rag


def query_bug_file(logger, bug_repo_path, no1_bug_file_path_list, top_k_files, issue_description):

    if faiss is None:
        raise ImportError("Missing optional dependency 'faiss' (e.g. `pip install faiss-cpu`) required for RAG features.")

    code_files_dict = {}
    for No1_bug_file_path in no1_bug_file_path_list:
        repo_path = normalize_candidate_path(bug_repo_path, No1_bug_file_path)
        code_files = load_code_files(repo_path)
        for file_name in code_files.keys():
            code_files_dict[file_name] = code_files[file_name]

    file_paths = list(code_files_dict.keys())

    logger.info(f'Query Files List:\n{file_paths}')

    index, file_paths = build_faiss_index(logger, code_files_dict)
    if index is None:
        return []


    # issue_description = """blendMode not working when doing point() drawing in webgl"""

    if 'Related Documents:' in issue_description:
        issue_description = issue_description.split('Related Documents:')[0]
    issue_embedding = get_embedding(issue_description)

    search_k = min(max(1, int(top_k_files)), len(file_paths))
    D, I = index.search(np.array([issue_embedding]).astype('float32'), k=search_k)


    bug_files_rag = []
    print("Potential bug files:")
    for i in I[0]:
        # print(file_paths[i])
        # bug_files_rag.append(file_paths[i])
        bug_files_rag.append(file_paths[i].replace(bug_repo_path+'/','').replace('\\','/'))

    return bug_files_rag
