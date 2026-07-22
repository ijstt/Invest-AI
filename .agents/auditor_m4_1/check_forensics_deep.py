import ast
import os
import subprocess
import tokenize
import io

def get_git_file_content(commit, path):
    res = subprocess.run(["git", "show", f"{commit}:{path}"], capture_output=True, text=True)
    if res.returncode == 0:
        return res.stdout
    return None

def count_comments_and_docstrings(code_str):
    comment_count = 0
    tokens = list(tokenize.generate_tokens(io.StringIO(code_str).readline))
    for toktype, tokval, _, _, _ in tokens:
        if toktype == tokenize.COMMENT:
            comment_count += 1
    
    # Docstrings from AST
    docstring_count = 0
    tree = ast.parse(code_str)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            if ast.get_docstring(node):
                docstring_count += 1
    return comment_count, docstring_count

def extract_detailed_func_signatures(tree):
    sigs = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = []
            for arg in node.args.args:
                args.append(arg.arg)
            kwonly = [arg.arg for arg in node.args.kwonlyargs]
            has_vararg = node.args.vararg is not None
            has_kwarg = node.args.kwarg is not None
            doc = ast.get_docstring(node)
            sigs[node.name] = {
                'args': args,
                'kwonlyargs': kwonly,
                'vararg': has_vararg,
                'kwarg': has_kwarg,
                'doc': doc
            }
    return sigs

def check_code_squashing_strict(filepath, code_str):
    tokens = list(tokenize.generate_tokens(io.StringIO(code_str).readline))
    squashed = []
    for toktype, tokval, start, end, line in tokens:
        if toktype == tokenize.OP and tokval == ';':
            squashed.append((start[0], line.strip()))
    return squashed

def main():
    print("=== DEEP FORENSIC INTEGRITY AUDIT ===")

    # 0. Check git status of tests/ directory
    git_test_diff = subprocess.run(["git", "diff", "HEAD", "--", "tests/"], capture_output=True, text=True).stdout
    git_test_untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "tests/"], capture_output=True, text=True).stdout
    
    if git_test_diff.strip() or git_test_untracked.strip():
        print(f"FAIL: Test files were modified or untracked test files exist!\nDiff: {git_test_diff}\nUntracked: {git_test_untracked}")
    else:
        print("PASS: `tests/` directory is completely untouched (0 changes to test suite).")

    # 1. Original web.py
    orig_web_code = get_git_file_content("HEAD", "src/geoanalytics/api/web.py")
    orig_comments, orig_docstrings = count_comments_and_docstrings(orig_web_code)
    orig_tree = ast.parse(orig_web_code)
    orig_sigs = extract_detailed_func_signatures(orig_tree)

    print(f"Original web.py: {orig_comments} comment tokens, {orig_docstrings} docstrings, {len(orig_sigs)} functions.")

    # 2. Modularized files
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

    total_comments = 0
    total_docstrings = 0
    curr_sigs = {}
    squash_violations = []

    for rf in router_files:
        with open(rf, "r", encoding="utf-8") as f:
            code = f.read()
        
        sq = check_code_squashing_strict(rf, code)
        if sq:
            squash_violations.append((rf, sq))

        c_cnt, d_cnt = count_comments_and_docstrings(code)
        total_comments += c_cnt
        total_docstrings += d_cnt

        tree = ast.parse(code)
        sigs = extract_detailed_func_signatures(tree)
        for fname, sig in sigs.items():
            curr_sigs[fname] = sig

    print(f"Modularized files combined: {total_comments} comment tokens, {total_docstrings} docstrings, {len(curr_sigs)} functions.")

    # 3. Check squashing
    if squash_violations:
        print(f"FAIL: Code squashing (semicolons in code tokens) found: {squash_violations}")
    else:
        print("PASS: Zero code squashing / semicolons in python code tokens.")

    # 4. Compare comments & docstrings
    print(f"Comment token comparison: Original = {orig_comments}, Modularized = {total_comments}")
    print(f"Docstring comparison: Original = {orig_docstrings}, Modularized = {total_docstrings}")
    if total_comments < orig_comments:
        print(f"WARNING: Comment token count decreased from {orig_comments} to {total_comments}")
    else:
        print("PASS: Comments preserved/enhanced.")

    if total_docstrings < orig_docstrings:
        print(f"FAIL: Docstrings stripped! Original {orig_docstrings} vs {total_docstrings}")
    else:
        print("PASS: All docstrings preserved intact.")

    # 5. Check signature mismatches
    sig_mismatches = []
    for fname, orig_sig in orig_sigs.items():
        if fname not in curr_sigs:
            sig_mismatches.append(f"Function {fname} missing in refactored code!")
        else:
            curr_sig = curr_sigs[fname]
            if orig_sig['args'] != curr_sig['args']:
                sig_mismatches.append(f"Function {fname} args mismatch: {orig_sig['args']} vs {curr_sig['args']}")
            if orig_sig['kwonlyargs'] != curr_sig['kwonlyargs']:
                sig_mismatches.append(f"Function {fname} kwonlyargs mismatch")
            if orig_sig['doc'] != curr_sig['doc']:
                sig_mismatches.append(f"Function {fname} docstring mismatch!")

    if sig_mismatches:
        print(f"FAIL: Function signature or docstring mismatches found ({len(sig_mismatches)}):")
        for m in sig_mismatches:
            print(f"  {m}")
    else:
        print("PASS: 100% of function signatures, arguments, and docstrings match original code perfectly!")

    # 6. Check file line limits (<600 lines)
    print("\n--- Line Count Limits Check ---")
    line_limit_violations = []
    for rf in router_files:
        with open(rf, "r", encoding="utf-8") as f:
            line_count = len(f.readlines())
        if line_count >= 600:
            line_limit_violations.append((rf, line_count))
        print(f"  {rf}: {line_count} lines")

    if line_limit_violations:
        print(f"FAIL: Files exceeding 600 line limit: {line_limit_violations}")
    else:
        print("PASS: All files are strictly under the 600-line limit.")

if __name__ == "__main__":
    main()
