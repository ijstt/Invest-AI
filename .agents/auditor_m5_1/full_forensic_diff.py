import ast
import subprocess
import os
import difflib

def get_head_cli_content():
    return subprocess.run(
        ["git", "show", "HEAD:src/geoanalytics/cli.py"],
        capture_output=True, text=True, cwd="/home/ijstt/News"
    ).stdout

def get_current_cli_files():
    cli_dir = "/home/ijstt/News/src/geoanalytics/cli"
    files = [os.path.join(cli_dir, f) for f in os.listdir(cli_dir) if f.endswith(".py")]
    files.append("/home/ijstt/News/src/geoanalytics/cli.py")
    return sorted(files)

def normalize_ast(node):
    """Recursively strip docstrings and position info for strict AST equality comparison."""
    for n in ast.walk(node):
        for attr in ("lineno", "col_offset", "end_lineno", "end_col_offset"):
            if hasattr(n, attr):
                delattr(n, attr)
    return node

def forensic_check():
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

    # Map function names to AST nodes in HEAD
    head_funcs = {}
    for node in ast.walk(head_tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # If function name repeated (like _fmt), store list
            if node.name not in head_funcs:
                head_funcs[node.name] = []
            head_funcs[node.name].append(node)

    # Map function names in Modularized code
    curr_funcs = {}
    for fpath, tree in current_trees.items():
        rel = os.path.relpath(fpath, "/home/ijstt/News")
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name not in curr_funcs:
                    curr_funcs[node.name] = []
                curr_funcs[node.name].append((rel, node))

    print(f"HEAD functions: {sum(len(v) for v in head_funcs.values())}")
    print(f"Curr functions: {sum(len(v) for v in curr_funcs.values())}")

    # Check each function
    differences_found = 0
    for name, head_nodes in head_funcs.items():
        if name not in curr_funcs:
            print(f"[FAIL] Missing function in current CLI: {name}")
            differences_found += 1
            continue

        curr_list = curr_funcs[name]
        if len(head_nodes) != len(curr_list):
            print(f"[FAIL] Function count mismatch for {name}: HEAD has {len(head_nodes)}, Curr has {len(curr_list)}")
            differences_found += 1
            continue

        for i in range(len(head_nodes)):
            h_node = head_nodes[i]
            c_rel, c_node = curr_list[i]

            # Compare function parameters
            h_args = [(a.arg, ast.dump(a.annotation) if a.annotation else None) for a in h_node.args.args]
            c_args = [(a.arg, ast.dump(a.annotation) if a.annotation else None) for a in c_node.args.args]
            if h_args != c_args:
                print(f"[FAIL] Parameter mismatch in {name} ({c_rel}):")
                print(f"  HEAD: {h_args}")
                print(f"  Curr: {c_args}")
                differences_found += 1

            # Compare defaults
            h_defs = [ast.dump(d) for d in h_node.args.defaults]
            c_defs = [ast.dump(d) for d in c_node.args.defaults]
            if h_defs != c_defs:
                print(f"[FAIL] Default values mismatch in {name} ({c_rel}):")
                print(f"  HEAD: {h_defs}")
                print(f"  Curr: {c_defs}")
                differences_found += 1

            # Compare docstrings
            if ast.get_docstring(h_node) != ast.get_docstring(c_node):
                print(f"[FAIL] Docstring mismatch in {name} ({c_rel})")
                differences_found += 1

    print(f"\nTotal differences found in signatures, parameters, defaults, and docstrings: {differences_found}")

    # Check for hardcoded test results or dummy implementations across all modular files
    dummy_patterns = 0
    for fpath, code in current_codes.items():
        rel = os.path.relpath(fpath, "/home/ijstt/News")
        tree = current_trees[fpath]
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Check body for pass only or return constant without computation
                body_non_doc = [n for n in node.body if not (isinstance(n, ast.Expr) and isinstance(n.value, (ast.Str, ast.Constant)))]
                if len(body_non_doc) == 1 and isinstance(body_non_doc[0], ast.Pass):
                    print(f"[WARNING/PROHIBITED] Empty body function with 'pass': {node.name} in {rel}")
                    dummy_patterns += 1
                elif len(body_non_doc) == 1 and isinstance(body_non_doc[0], ast.Return):
                    if isinstance(body_non_doc[0].value, ast.Constant):
                        print(f"[WARNING/PROHIBITED] Constant return facade function: {node.name} in {rel}")
                        dummy_patterns += 1

    print(f"Dummy / Facade pattern count: {dummy_patterns}")

if __name__ == "__main__":
    forensic_check()
