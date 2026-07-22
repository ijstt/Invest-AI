import ast
import os
import subprocess

def get_git_file_content(commit, path):
    res = subprocess.run(["git", "show", f"{commit}:{path}"], capture_output=True, text=True)
    if res.returncode == 0:
        return res.stdout
    return None

def check_squashing(filepath, code):
    lines = code.splitlines()
    squashed_lines = []
    for idx, line in enumerate(lines, 1):
        # ignore comments and string literals simple check
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # Check for multiple statements separated by semicolon
        if ";" in line:
            squashed_lines.append((idx, line))
    return squashed_lines

def extract_endpoints_from_ast(tree):
    endpoints = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for decorator in node.decorator_list:
                # check if @router.get / @router.post or @app.get etc.
                if isinstance(decorator, ast.Call):
                    func = decorator.func
                    if isinstance(func, ast.Attribute):
                        if func.attr in ('get', 'post', 'put', 'delete', 'patch'):
                            # get path arg
                            path = None
                            if decorator.args:
                                if isinstance(decorator.args[0], ast.Constant):
                                    path = decorator.args[0].value
                            endpoints.append({
                                'func_name': node.name,
                                'method': func.attr,
                                'path': path,
                                'args': [a.arg for a in node.args.args]
                            })
    return endpoints

def extract_functions_and_classes(tree):
    items = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            items.add(node.name)
    return items

def main():
    print("=== FORENSIC INTEGRITY CHECK ===")

    # 1. Get original web.py content from git HEAD
    orig_web_code = get_git_file_content("HEAD", "src/geoanalytics/api/web.py")
    if not orig_web_code:
        print("ERROR: Could not fetch original web.py from git HEAD")
        return

    orig_ast = ast.parse(orig_web_code)
    orig_endpoints = extract_endpoints_from_ast(orig_ast)
    orig_funcs = extract_functions_and_classes(orig_ast)

    print(f"Original web.py: {len(orig_endpoints)} endpoints, {len(orig_funcs)} functions/classes.")

    # 2. Inspect current files
    router_files = [
        "src/geoanalytics/api/web.py",
        "src/geoanalytics/api/routers/alerts.py",
        "src/geoanalytics/api/routers/asset.py",
        "src/geoanalytics/api/routers/backtest.py",
        "src/geoanalytics/api/routers/dashboard.py",
        "src/geoanalytics/api/routers/factors.py",
        "src/geoanalytics/api/routers/graph.py",
        "src/geoanalytics/api/routers/portfolio.py",
        "src/geoanalytics/api/routers/track2.py",
    ]

    current_endpoints = []
    current_funcs = set()
    all_squashed = {}

    for rf in router_files:
        with open(rf, "r", encoding="utf-8") as f:
            code = f.read()
        
        sq = check_squashing(rf, code)
        if sq:
            all_squashed[rf] = sq
        
        tree = ast.parse(code)
        current_endpoints.extend(extract_endpoints_from_ast(tree))
        current_funcs.update(extract_functions_and_classes(tree))

    print(f"Current web.py + routers: {len(current_endpoints)} endpoints, {len(current_funcs)} functions/classes.")

    # 3. Squashing check results
    if all_squashed:
        print("WARNING/FAIL: Found potential squashed lines with ';':")
        for rf, sq in all_squashed.items():
            print(f"  {rf}: {sq}")
    else:
        print("PASS: No artificial code squashing (semicolons) found in router files.")

    # 4. Compare endpoints
    orig_ep_set = {(ep['method'], ep['path'], ep['func_name']) for ep in orig_endpoints}
    curr_ep_set = {(ep['method'], ep['path'], ep['func_name']) for ep in current_endpoints}

    missing_eps = orig_ep_set - curr_ep_set
    extra_eps = curr_ep_set - orig_ep_set

    if missing_eps:
        print(f"FAIL: Missing endpoints in refactored code ({len(missing_eps)}):")
        for ep in missing_eps:
            print(f"  Missing: {ep}")
    else:
        print(f"PASS: All {len(orig_ep_set)} original endpoints are intact and present in sub-routers.")

    if extra_eps:
        print(f"INFO: Extra endpoints introduced: {extra_eps}")

    # 5. Check function preservation
    missing_funcs = orig_funcs - current_funcs
    if missing_funcs:
        print(f"WARNING/FAIL: Missing functions/classes ({len(missing_funcs)}): {missing_funcs}")
    else:
        print(f"PASS: All original functions/classes are preserved in refactored code.")

    # 6. Check hardcoded test results or fake responses
    print("\n--- Hardcoded Response / Facade Check ---")
    suspicious_patterns = ["fake", "dummy", "hardcode", "mock_response"]
    found_suspicious = False
    for rf in router_files:
        with open(rf, "r", encoding="utf-8") as f:
            code = f.read()
        for idx, line in enumerate(code.splitlines(), 1):
            for pat in suspicious_patterns:
                if pat in line.lower() and not line.strip().startswith("#"):
                    print(f"Suspicious pattern '{pat}' in {rf}:{idx}: {line.strip()}")
                    found_suspicious = True

    if not found_suspicious:
        print("PASS: No suspicious dummy/hardcoded patterns found.")

if __name__ == "__main__":
    main()
