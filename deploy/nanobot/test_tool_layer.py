#!/usr/bin/env python3

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT_DIR = Path(__file__).resolve().parents[2]
PYTHON_BIN = ROOT_DIR / ".venv" / "bin" / "python"
MCP_SERVER = ROOT_DIR / "deploy" / "nanobot" / "mcp_server.py"
EXPECTED_TOOLS = {"get_latest_observation", "get_metric", "get_threshold_status"}


def flatten_text(result) -> str:
    chunks: list[str] = []
    for item in result.content:
        text = getattr(item, "text", None)
        if text is not None:
            chunks.append(text)
        else:
            chunks.append(str(item))
    return "\n".join(chunks).strip()


async def main() -> int:
    params = StdioServerParameters(command=str(PYTHON_BIN), args=[str(MCP_SERVER)])

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            tool_names = {tool.name for tool in listed.tools}
            missing = EXPECTED_TOOLS.difference(tool_names)
            if missing:
                print(json.dumps({"ok": False, "error": f"missing tools: {sorted(missing)}"}))
                return 1

            latest_result = await session.call_tool("get_latest_observation", arguments={})
            latest_payload = json.loads(flatten_text(latest_result))
            if not latest_payload.get("ok"):
                print(json.dumps({"ok": False, "error": "latest observation tool returned failure"}))
                return 1

            metrics: dict[str, float] = {}
            for metric_name in ("temperature", "humidity", "pressure"):
                metric_result = await session.call_tool("get_metric", arguments={"metric": metric_name})
                metric_payload = json.loads(flatten_text(metric_result))
                if not metric_payload.get("ok") or metric_payload.get("metric") != metric_name:
                    print(json.dumps({"ok": False, "error": f"metric tool failed for {metric_name}"}))
                    return 1
                metrics[metric_name] = float(metric_payload["value"])

            threshold_result = await session.call_tool("get_threshold_status", arguments={})
            threshold_payload = json.loads(flatten_text(threshold_result))
            if not threshold_payload.get("ok"):
                print(json.dumps({"ok": False, "error": "threshold status tool returned failure"}))
                return 1

            statuses = threshold_payload.get("thresholdStatus", {})
            for metric_name in ("temperature", "humidity", "pressure"):
                if metric_name not in statuses:
                    print(json.dumps({"ok": False, "error": f"threshold status missing {metric_name}"}))
                    return 1

    print(
        json.dumps(
            {
                "ok": True,
                "tools": sorted(EXPECTED_TOOLS),
                "metrics": metrics,
                "thresholdMetrics": sorted(statuses),
                "source": "validated local files via workspace/tools wrappers",
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))