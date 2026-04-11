"""Code Surgery tools — repo map, symbol operations, file read/write."""
import os
import subprocess
from tool_registry import ToolRegistry
from spine_client import SpineClient


def register_code_surgery_tools(registry: ToolRegistry, client: SpineClient):
    """Register code surgery tools."""

    @registry.tool(
        description="Scan the entire repository and return an index of all symbols and their locations.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Root directory to scan (default: /app)"},
            },
        }
    )
    def generate_repo_map(path: str = "/app") -> str:
        client.emit_event("cortex.generate_repo_map", {"path": path})
        try:
            result = subprocess.run(
                ["find", path, "-name", "*.py", "-o", "-name", "*.go", "-o", "-name", "*.js"],
                capture_output=True, text=True, timeout=30
            )
            files = result.stdout.strip().split("\n") if result.stdout.strip() else []
            return f"[REPO MAP] Found {len(files)} source files in {path}"
        except Exception as e:
            return f"[ERROR] Failed to generate repo map: {e}"

    @registry.tool(
        description="Read a file's contents. Use start_line and end_line for bounded reading.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to read"},
                "start_line": {"type": "integer", "description": "Start line (1-indexed, default: 1)"},
                "end_line": {"type": "integer", "description": "End line (default: end of file)"},
            },
            "required": ["path"],
        }
    )
    def read_file(path: str, start_line: int = 1, end_line: int = 0) -> str:
        client.emit_event("cortex.read_file", {"path": path})
        try:
            with open(path, "r") as f:
                lines = f.readlines()
            if end_line > 0:
                selected = lines[start_line - 1:end_line]
            else:
                selected = lines[start_line - 1:]
            return "".join(selected)
        except FileNotFoundError:
            return f"[ERROR] File not found: {path}"
        except Exception as e:
            return f"[ERROR] Failed to read file: {e}"

    @registry.tool(
        description="Write content to a file. Creates the file if it doesn't exist.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to write"},
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["path", "content"],
        }
    )
    def write_file(path: str, content: str) -> str:
        client.emit_event("cortex.write_file", {"path": path, "content_len": len(content)})
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(content)
            return f"[WRITTEN] {path} ({len(content)} bytes)"
        except Exception as e:
            return f"[ERROR] Failed to write file: {e}"

    @registry.tool(
        description="Replace a symbol (function/class) in a file by name.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "symbol_name": {"type": "string", "description": "Name of the symbol to replace"},
                "new_code": {"type": "string", "description": "New code for the symbol"},
            },
            "required": ["path", "symbol_name", "new_code"],
        }
    )
    def replace_symbol(path: str, symbol_name: str, new_code: str) -> str:
        client.emit_event("cortex.replace_symbol", {"path": path, "symbol": symbol_name})
        # Basic implementation: read file, find and replace the function/class definition
        # Full AST-based implementation would use tree-sitter
        try:
            with open(path, "r") as f:
                content = f.read()
            # Simple regex-free approach: find the symbol and replace up to the next def/class at same indent
            lines = content.split("\n")
            start_idx = None
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith(f"def {symbol_name}(") or stripped.startswith(f"class {symbol_name}"):
                    start_idx = i
                    break
            if start_idx is None:
                return f"[ERROR] Symbol '{symbol_name}' not found in {path}"

            # Replace from start_idx to the next def/class at same or lower indentation
            base_indent = len(lines[start_idx]) - len(lines[start_idx].lstrip())
            end_idx = len(lines)
            for i in range(start_idx + 1, len(lines)):
                if lines[i].strip() and not lines[i].startswith(" " * (base_indent + 1)):
                    if lines[i].strip().startswith(("def ", "class ", "@")):
                        end_idx = i
                        break

            new_lines = lines[:start_idx] + new_code.split("\n") + lines[end_idx:]
            with open(path, "w") as f:
                f.write("\n".join(new_lines))
            return f"[REPLACED] {symbol_name} in {path}"
        except Exception as e:
            return f"[ERROR] Failed to replace symbol: {e}"

    @registry.tool(
        description="Apply a unified diff patch to a file.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to patch"},
                "patch": {"type": "string", "description": "Unified diff patch content"},
            },
            "required": ["path", "patch"],
        }
    )
    def patch_file(path: str, patch: str) -> str:
        client.emit_event("cortex.patch_file", {"path": path})
        try:
            result = subprocess.run(
                ["patch", "-p1", path],
                input=patch, capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                return f"[ERROR] Patch failed: {result.stderr}"
            return f"[PATCHED] {path}"
        except Exception as e:
            return f"[ERROR] Failed to patch file: {e}"