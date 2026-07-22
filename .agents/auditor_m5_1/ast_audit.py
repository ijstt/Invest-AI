import ast
import subprocess
import os
import sys

def get_head_cli_content():
    result = subprocess.run(
        ["git", "show", "HEAD:src/geoanalytics/cli.py"],
        capture_output=True,
        text=True,
        cwd="/home/ijstt/News"
    )
    if result.returncode != 0:
        raise RuntimeError("Failed to fetch HEAD:src/geoanalytics/cli.py")
    return result.stdout

def get_current_cli_files():
    cli_dir = "/home/ijstt/News/src/geoanalytics/cli"
    files = [os.path.join(cli_dir, f) for f in os.listdir(cli_dir) if f.endswith(".py")]
    files.append("/home/ijstt/News/src/geoanalytics/cli.py")
    return sorted(files)

def analyze():
    print("=== AST & Forensics Analysis starting ===")
    head_code = get_head_cli_content()
    head_tree = ast.parse(head_code)
    
    current_files = get_current_cli_files()
    current_trees = {}
    current_codes = {}
    for fpath in current_files:
        with open(fpath, "r", encoding="utf-8") as f:
            content = f.read()
            current_codes[fpath] = content
            current_trees[fpath] = ast.parse(content, filename=fpath)

    # 1. Extract functions and async functions from HEAD
    head_funcs = {}
    head_classes = {}
    head_subtypers = set()
    head_comments_count = len([line for line in head_code.splitlines() if line.strip().startswith("#")])
    head_docstrings_count = 0

    for node in ast.walk(head_tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            head_funcs[node.name] = node
            doc = ast.get_docstring(node)
            if doc:
                head_docstrings_count += 1
        elif isinstance(node, ast.ClassDef):
            head_classes[node.name] = node

    print(f"HEAD cli.py stats:")
    print(f"  Total lines: {len(head_code.splitlines())}")
    print(f"  Functions/Commands: {len(head_funcs)}")
    print(f"  Classes: {len(head_classes)}")
    print(f"  Comment lines (starting with #): {head_comments_count}")
    print(f"  Functions with docstrings: {head_docstrings_count}")

    # 2. Extract functions from current modular files
    current_funcs = {}
    current_classes = {}
    current_comments_count = 0
    current_docstrings_count = 0
    squash_semicolons = []
    long_lines = []

    for fpath, code in current_codes.items():
        rel_path = os.path.relpath(fpath, "/home/ijstt/News")
        lines = code.splitlines()
        for idx, line in enumerate(lines, 1):
            if ";" in line:
                # exclude strings
                stripped = line.strip()
                if not (stripped.startswith("#") or '"' in stripped or "'" in stripped):
                    squash_semicolons.append((rel_path, idx, line))
            if len(line) > 200:
                long_lines.append((rel_path, idx, len(line)))
            if line.strip().startswith("#"):
                current_comments_count += 1

        tree = current_trees[fpath]
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in current_funcs:
                    print(f"WARNING: Duplicate function name across modules: {node.name} in {rel_path} and {current_funcs[node.name][0]}")
                current_funcs[node.name] = (rel_path, node)
                doc = ast.get_docstring(node)
                if doc:
                    current_docstrings_count += 1
            elif isinstance(node, ast.ClassDef):
                current_classes[node.name] = (rel_path, node)

    print(f"\nModularized CLI stats:")
    print(f"  Total functions found: {len(current_funcs)}")
    print(f"  Total classes found: {len(current_classes)}")
    print(f"  Total comment lines: {current_comments_count}")
    print(f"  Functions with docstrings: {current_docstrings_count}")
    print(f"  Semicolons used for squashing: {len(squash_semicolons)}")
    print(f"  Lines > 200 chars: {len(long_lines)}")

    # 3. Check for missing functions or classes
    missing_funcs = set(head_funcs.keys()) - set(current_funcs.keys())
    extra_funcs = set(current_funcs.keys()) - set(head_funcs.keys())

    print(f"\nFunction Delta:")
    print(f"  Missing functions ({len(missing_funcs)}): {sorted(list(missing_funcs))}")
    print(f"  Extra/new functions ({len(extra_funcs)}): {sorted(list(extra_funcs))}")

    missing_classes = set(head_classes.keys()) - set(current_classes.keys())
    print(f"  Missing classes ({len(missing_classes)}): {sorted(list(missing_classes))}")

    # 4. Detailed comparison per function
    args_mismatch = []
    docstring_mismatch = []
    body_node_diff = []

    for fname, orig_node in head_funcs.items():
        if fname not in current_funcs:
            continue
        mod_path, curr_node = current_funcs[fname]

        # Compare args
        orig_args = [a.arg for a in orig_node.args.args]
        curr_args = [a.arg for a in curr_node.args.args]
        if orig_args != curr_args:
            args_mismatch.append((fname, orig_args, curr_args, mod_path))

        # Compare defaults count
        orig_defaults_cnt = len(orig_node.args.defaults)
        curr_defaults_cnt = len(curr_node.args.defaults)
        if orig_defaults_cnt != curr_defaults_cnt:
            args_mismatch.append((fname, f"defaults orig={orig_defaults_cnt}", f"curr={curr_defaults_cnt}", mod_path))

        # Compare docstring
        orig_doc = ast.get_docstring(orig_node)
        curr_doc = ast.get_docstring(curr_node)
        if orig_doc != curr_doc:
            docstring_mismatch.append((fname, mod_path, orig_doc, curr_doc))

        # Compare AST node counts inside function body
        orig_node_cnt = len(list(ast.walk(orig_node)))
        curr_node_cnt = len(list(ast.walk(curr_node)))
        if orig_node_cnt != curr_node_cnt:
            body_node_diff.append((fname, mod_path, orig_node_cnt, curr_node_cnt))

    print(f"\nFunction Argument Mismatches: {len(args_mismatch)}")
    for item in args_mismatch:
        print(f"  {item}")

    print(f"\nDocstring Mismatches: {len(docstring_mismatch)}")
    for item in docstring_mismatch:
        print(f"  Function: {item[0]} in {item[1]}")
        print(f"    Orig doc: {repr(item[2])}")
        print(f"    Curr doc: {repr(item[3])}")

    print(f"\nBody AST Node Count Differences (checking logic changes): {len(body_node_diff)}")
    for item in body_node_diff:
        print(f"  Function {item[0]} in {item[1]}: HEAD nodes={item[2]}, Curr nodes={item[3]}")

if __name__ == "__main__":
    analyze()
