"""
Diagnostic Tool: inspect_code
Static analysis of Python code using Python AST to identify functions,
data loadings, column references, transformations, joins, type conversions, and aggregations.
"""
import ast
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
from backend.app.tools.base import BaseDiagnosticTool, DiagnosticToolDefinition, ToolParameter, ToolResult


class ASTCodeVisitor(ast.NodeVisitor):
    def __init__(self, target_line: Optional[int] = None):
        self.target_line = target_line
        self.functions: List[Dict[str, Any]] = []
        self.data_loadings: List[Dict[str, Any]] = []
        self.column_references: List[Dict[str, Any]] = []
        self.transformations: List[Dict[str, Any]] = []
        self.joins_and_merges: List[Dict[str, Any]] = []
        self.type_conversions: List[Dict[str, Any]] = []
        self.aggregations: List[Dict[str, Any]] = []
        self.imports: List[str] = []

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            self.imports.append(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        mod = node.module or ""
        for alias in node.names:
            self.imports.append(f"{mod}.{alias.name}")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        args = [arg.arg for arg in node.args.args]
        self.functions.append({
            "name": node.name,
            "line_start": node.lineno,
            "line_end": getattr(node, "end_lineno", node.lineno),
            "arguments": args
        })
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        lineno = getattr(node, "lineno", 0)

        # Detect data loading
        if func_name in ("read_csv", "read_parquet", "read_sql", "read_json", "load"):
            arg_val = None
            if node.args and isinstance(node.args[0], ast.Constant):
                arg_val = str(node.args[0].value)
            self.data_loadings.append({
                "function": func_name,
                "line": lineno,
                "target_arg": arg_val
            })

        # Detect joins & merges
        if func_name in ("merge", "join", "concat"):
            self.joins_and_merges.append({
                "function": func_name,
                "line": lineno
            })

        # Detect type conversions
        if func_name in ("astype", "to_numeric", "to_datetime", "int", "float", "str"):
            target_type = None
            if node.args and isinstance(node.args[0], ast.Constant):
                target_type = str(node.args[0].value)
            elif node.args and isinstance(node.args[0], ast.Name):
                target_type = node.args[0].id
            self.type_conversions.append({
                "function": func_name,
                "line": lineno,
                "target_type": target_type
            })

        # Detect aggregations
        if func_name in ("groupby", "agg", "aggregate", "sum", "mean", "median", "count", "min", "max"):
            cols = []
            if node.args:
                for arg in node.args:
                    if isinstance(arg, ast.Constant):
                        cols.append(str(arg.value))
                    elif isinstance(arg, ast.List):
                        for el in arg.elts:
                            if isinstance(el, ast.Constant):
                                cols.append(str(el.value))
            self.aggregations.append({
                "function": func_name,
                "line": lineno,
                "columns": cols
            })

        # Detect transformations
        if func_name in ("apply", "map", "transform", "assign", "drop", "fillna", "dropna"):
            self.transformations.append({
                "function": func_name,
                "line": lineno
            })

        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript):
        # Look for df['column_name'] or df[['col1', 'col2']]
        lineno = getattr(node, "lineno", 0)
        slice_node = node.slice
        if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
            self.column_references.append({
                "column": slice_node.value,
                "line": lineno
            })
        elif isinstance(slice_node, ast.List):
            for elt in slice_node.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    self.column_references.append({
                        "column": elt.value,
                        "line": lineno
                    })
        self.generic_visit(node)


class InspectCodeTool(BaseDiagnosticTool):
    name = "inspect_code"
    description = (
        "Performs static Python AST analysis on a code file to identify function definitions, data loading "
        "calls (e.g. read_csv, read_parquet), column references, data transformations, joins, type conversions, "
        "and aggregations."
    )
    permission_level = "read_only"
    timeout_seconds = 20

    def get_definition(self) -> DiagnosticToolDefinition:
        return DiagnosticToolDefinition(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParameter(
                    name="file_path",
                    type="string",
                    description="Relative path to Python file within project to inspect.",
                    required=True
                ),
                ToolParameter(
                    name="focus_line",
                    type="integer",
                    description="Optional line number to focus analysis around (e.g. from a stack trace).",
                    required=False,
                    default=None
                )
            ],
            permission_level=self.permission_level,
            timeout_seconds=self.timeout_seconds
        )

    async def execute(self, project_path: str, arguments: Dict[str, Any]) -> ToolResult:
        start_time = time.time()
        base_dir = Path(project_path)
        file_rel = arguments.get("file_path", "")
        focus_line = arguments.get("focus_line")

        target_file = (base_dir / file_rel).resolve()
        if not str(target_file).startswith(str(base_dir)):
            return ToolResult(
                success=False,
                summary="Security error: Path traversal detected.",
                error="Path traversal attempted.",
                execution_time_ms=int((time.time() - start_time) * 1000)
            )

        if not target_file.exists():
            return ToolResult(
                success=False,
                summary=f"Code file '{file_rel}' not found.",
                error="File not found.",
                execution_time_ms=int((time.time() - start_time) * 1000)
            )

        try:
            content = target_file.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(content, filename=file_rel)
        except SyntaxError as se:
            return ToolResult(
                success=False,
                summary=f"SyntaxError in '{file_rel}': {str(se)} at line {se.lineno}",
                error=f"SyntaxError: {str(se)}",
                execution_time_ms=int((time.time() - start_time) * 1000)
            )
        except Exception as e:
            return ToolResult(
                success=False,
                summary=f"Failed to parse '{file_rel}': {str(e)}",
                error=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000)
            )

        visitor = ASTCodeVisitor(target_line=focus_line)
        visitor.visit(tree)

        # Snippet around focus line if provided
        code_lines = content.splitlines()
        focus_snippet = ""
        if focus_line and 1 <= focus_line <= len(code_lines):
            start_l = max(1, focus_line - 5)
            end_l = min(len(code_lines), focus_line + 5)
            numbered = [
                f"{'>' if i == focus_line else ' '} {i:4d} | {code_lines[i-1]}"
                for i in range(start_l, end_l + 1)
            ]
            focus_snippet = "\n".join(numbered)

        # Deduplicate column references
        col_summary = {}
        for ref in visitor.column_references:
            c = ref["column"]
            if c not in col_summary:
                col_summary[c] = []
            col_summary[c].append(ref["line"])

        data = {
            "file": file_rel,
            "total_lines": len(code_lines),
            "functions": visitor.functions,
            "imports": visitor.imports,
            "data_loadings": visitor.data_loadings,
            "column_references": [{"column": c, "lines": l} for c, l in col_summary.items()],
            "transformations": visitor.transformations,
            "joins_and_merges": visitor.joins_and_merges,
            "type_conversions": visitor.type_conversions,
            "aggregations": visitor.aggregations,
            "focus_snippet": focus_snippet
        }

        cols_str = ", ".join(list(col_summary.keys())[:8]) or "None"
        summary = (
            f"AST analysis of '{file_rel}': {len(visitor.functions)} functions, {len(visitor.data_loadings)} data loaders, "
            f"referenced columns: [{cols_str}], {len(visitor.transformations)} transformations, "
            f"{len(visitor.aggregations)} aggregations, {len(visitor.type_conversions)} type conversions."
        )

        return ToolResult(
            success=True,
            data=data,
            summary=summary,
            execution_time_ms=int((time.time() - start_time) * 1000)
        )
