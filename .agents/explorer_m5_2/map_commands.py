import ast

cli_path = "/home/ijstt/News/src/geoanalytics/cli.py"

with open(cli_path, "r", encoding="utf-8") as f:
    source = f.read()

tree = ast.parse(source, filename=cli_path)

commands_detail = []

for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        name = node.name
        start = node.lineno
        end = getattr(node, 'end_lineno', start)
        length = end - start + 1
        
        cmd_app = None
        cmd_name = name
        
        for dec in node.decorator_list:
            dec_code = ast.unparse(dec)
            if "command" in dec_code or "callback" in dec_code:
                # parse app name and command name override
                if dec_code.startswith("app.command"):
                    cmd_app = "app"
                elif dec_code.startswith("app.callback"):
                    cmd_app = "app_callback"
                elif dec_code.startswith("portfolio_app.command"):
                    cmd_app = "portfolio_app"
                elif dec_code.startswith("portfolio_app.callback"):
                    cmd_app = "portfolio_app_callback"
                elif dec_code.startswith("fundamentals_app.command"):
                    cmd_app = "fundamentals_app"
                elif dec_code.startswith("segments_app.command"):
                    cmd_app = "segments_app"
                elif dec_code.startswith("futures_intraday_app.command"):
                    cmd_app = "futures_intraday_app"
                elif dec_code.startswith("futures_depth_app.command"):
                    cmd_app = "futures_depth_app"
                elif dec_code.startswith("db_app.command"):
                    cmd_app = "db_app"
                else:
                    cmd_app = dec_code
                
                # Check for explicit command name argument, e.g. @app.command('news-backfill')
                if isinstance(dec, ast.Call):
                    if dec.args:
                        if isinstance(dec.args[0], ast.Constant):
                            cmd_name = dec.args[0].value
                    for k in dec.keywords:
                        if k.arg == "name" and isinstance(k.value, ast.Constant):
                            cmd_name = k.value.value

        doc = ast.get_docstring(node)
        first_line_doc = doc.split("\n")[0] if doc else ""
        
        commands_detail.append({
            "name": name,
            "cmd_name": cmd_name,
            "app": cmd_app,
            "start": start,
            "end": end,
            "length": length,
            "doc": first_line_doc
        })

print(f"{'LineRange':12s} | {'App':22s} | {'Cmd Name':30s} | {'Fn Name':30s} | Doc")
print("-" * 120)

for c in commands_detail:
    lr = f"L{c['start']}-L{c['end']}"
    app_str = str(c['app'])
    print(f"{lr:12s} | {app_str:22s} | {c['cmd_name']:30s} | {c['name']:30s} | {c['doc'][:40]}")
