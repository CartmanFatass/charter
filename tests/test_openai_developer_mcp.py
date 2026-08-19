"""Tests for OpenAI Developer Toolkit & Specifications MCP Server."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

MCP_SERVER_SCRIPT = Path(__file__).parent.parent / "mcp" / "openai_developer" / "server.py"

class TestOpenAIDeveloperMCP(unittest.TestCase):
    def setUp(self):
        self.proc = subprocess.Popen(
            [sys.executable, str(MCP_SERVER_SCRIPT)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )

    def tearDown(self):
        if self.proc.stdin:
            self.proc.stdin.close()
        if self.proc.stdout:
            self.proc.stdout.close()
        if self.proc.stderr:
            self.proc.stderr.close()
        if self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait()

    def _rpc_call(self, method: str, params: dict | None = None, req_id: int = 1) -> dict:
        req = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {}
        }
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        self.assertTrue(line, "Server returned empty output")
        return json.loads(line)

    def test_initialize(self):
        resp = self._rpc_call("initialize")
        self.assertEqual(resp.get("jsonrpc"), "2.0")
        self.assertEqual(resp.get("id"), 1)
        result = resp.get("result", {})
        self.assertEqual(result.get("serverInfo", {}).get("name"), "openai-developer-toolkit")
        self.assertIn("tools", result.get("capabilities", {}))

    def test_ping(self):
        resp = self._rpc_call("ping")
        self.assertEqual(resp.get("result"), {})

    def test_tools_list(self):
        resp = self._rpc_call("tools/list")
        tools = resp.get("result", {}).get("tools", [])
        tool_names = {t["name"] for t in tools}
        expected = {
            "openai_search_docs",
            "openai_get_api_spec",
            "openai_mcpkit_spec",
            "openai_model_matrix",
            "openai_agents_sdk_guide",
            "openai_cookbook_recipe",
        }
        self.assertTrue(expected.issubset(tool_names), f"Missing tools: {expected - tool_names}")

    def test_tools_call_search_docs(self):
        resp = self._rpc_call("tools/call", {
            "name": "openai_search_docs",
            "arguments": {"query": "responses api"}
        })
        text = resp["result"]["content"][0]["text"]
        self.assertIn("Responses API", text)

    def test_tools_call_get_api_spec(self):
        resp = self._rpc_call("tools/call", {
            "name": "openai_get_api_spec",
            "arguments": {"endpoint_name": "chat_completions"}
        })
        text = resp["result"]["content"][0]["text"]
        self.assertIn("Chat Completions API", text)
        self.assertIn("reasoning_effort", text)
        self.assertIn("Python SDK Usage", text)

    def test_tools_call_mcpkit_spec(self):
        resp = self._rpc_call("tools/call", {
            "name": "openai_mcpkit_spec",
            "arguments": {"section": "all"}
        })
        text = resp["result"]["content"][0]["text"]
        self.assertIn("OpenAI Model Context Protocol", text)
        self.assertIn("FastMCP", text)

    def test_tools_call_model_matrix(self):
        resp = self._rpc_call("tools/call", {
            "name": "openai_model_matrix",
            "arguments": {}
        })
        text = resp["result"]["content"][0]["text"]
        self.assertIn("o3-mini", text)
        self.assertIn("gpt-4o", text)

    def test_tools_call_agents_sdk(self):
        resp = self._rpc_call("tools/call", {
            "name": "openai_agents_sdk_guide",
            "arguments": {}
        })
        text = resp["result"]["content"][0]["text"]
        self.assertIn("OpenAI Agents SDK", text)
        self.assertIn("Handoff", text)

    def test_tools_call_cookbook_recipe(self):
        resp = self._rpc_call("tools/call", {
            "name": "openai_cookbook_recipe",
            "arguments": {"recipe_name": "structured_outputs"}
        })
        text = resp["result"]["content"][0]["text"]
        self.assertIn("Structured Outputs", text)
        self.assertIn("pydantic", text.lower())
    def test_unknown_tool_error(self):
        resp = self._rpc_call("tools/call", {
            "name": "unknown_tool_xyz",
            "arguments": {}
        })
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32601)

if __name__ == "__main__":
    unittest.main()
