from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters


async def verify() -> dict:
    project_root = Path(__file__).resolve().parents[1]
    source_root = project_root / "src"
    env = {
        "PYTHONPATH": str(source_root),
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    if os.getenv("PATH"):
        env["PATH"] = os.environ["PATH"]
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "fin_agent.mcp_server"],
        cwd=project_root,
        env=env,
    )
    async with Client(parameters) as client:
        tools_result = await client.list_tools()
        tool_names = sorted(tool.name for tool in tools_result.tools)
        expected = {
            "financial_agent_status",
            "list_financial_reports",
            "analyze_financial_report",
            "read_analysis_artifact",
        }
        missing = sorted(expected - set(tool_names))
        if missing:
            raise RuntimeError(f"MCP tools missing: {missing}")
        status_result = await client.call_tool("financial_agent_status", {})
        if status_result.is_error:
            raise RuntimeError(f"financial_agent_status failed: {status_result.content}")
        status = status_result.structured_content
        if not isinstance(status, dict) or status.get("pdf_count") != 21:
            raise RuntimeError(f"Unexpected MCP status result: {status}")
        return {
            "protocol_version": str(client.protocol_version),
            "server": (
                client.server_info.model_dump(mode="json")
                if client.server_info is not None
                else None
            ),
            "tools": tool_names,
            "status": status,
        }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(asyncio.run(verify()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
