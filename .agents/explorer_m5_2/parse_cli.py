import ast
import os
import sys

cli_path = "/home/ijstt/News/src/geoanalytics/cli.py"

with open(cli_path, "r", encoding="utf-8") as f:
    source = f.read()

tree = ast.parse(source, filename=cli_path)

print(f"Total AST nodes at top level: {len(tree.body)}")

imports = []
functions = []
classes = []
assignments = []

for node in tree.body:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        imports.append(node)
    elif isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
        functions.append(node)
    elif isinstance(node, ast.ClassDef):
        classes.append(node)
    elif isinstance(node, (ast.Assign, ast.AnnAssign)):
        assignments.append(node)

print(f"Imports: {len(imports)}")
print(f"Top-level Functions: {len(functions)}")
print(f"Classes: {len(classes)}")
print(f"Top-level Assignments: {len(assignments)}")

# Details on imports
print("\n--- IMPORTS ---")
for imp in imports:
    lineno = imp.lineno
    if isinstance(imp, ast.Import):
        names = [alias.name + (f" as {alias.asname}" if alias.asname else "") for alias in imp.names]
        print(f"Line {lineno:4d}: import {', '.join(names)}")
    elif isinstance(imp, ast.ImportFrom):
        module = imp.module or ""
        level = imp.level
        dots = "." * level
        names = [alias.name + (f" as {alias.asname}" if alias.asname else "") for alias in imp.names]
        print(f"Line {lineno:4d}: from {dots}{module} import {', '.join(names)}")

# Details on top-level functions & decorators
print("\n--- FUNCTIONS & CLICK COMMANDS ---")
for fn in functions:
    lineno = fn.lineno
    end_lineno = getattr(fn, 'end_lineno', lineno)
    decorators = []
    for dec in fn.decorator_list:
        if isinstance(dec, ast.Name):
            decorators.append(dec.id)
        elif isinstance(dec, ast.Attribute):
            decorators.append(f"{ast.unparse(dec)}")
        elif isinstance(dec, ast.Call):
            decorators.append(f"{ast.unparse(dec.func)}")
        else:
            decorators.append(ast.unparse(dec))
    
    dec_str = f" [@{', @'.join(decorators)}]" if decorators else ""
    print(f"Line {lineno:4d}-{end_lineno:4d}: {fn.name}{dec_str}")
