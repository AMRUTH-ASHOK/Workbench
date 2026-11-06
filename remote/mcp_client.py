"""Small synchronous MCP stdio client for the existing Arclet dbexec runtime.

No credentials are copied. Only explicitly allowed collection tools are exposed.
"""
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

READ_TOOLS = {
    "support-agent": {"get_server_info", "get_support_case", "list_support_case_comments", "list_support_case_attachments"},
    "databricks-v2": {"execute_parameterized_sql", "check_statement_status", "cancel_statement"},
    "salesforce": {"discover_salesforce_objects", "discover_salesforce_fields_on_object", "execute_soql"},
}


def decode_result(result):
    structured = result.get("structuredContent", {})
    if "result" in structured:
        value = structured["result"]
    else:
        value = "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
    if result.get("isError"):
        raise RuntimeError(str(value)[:2500])
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


class MCP:
    def __init__(self, server, directory, timeout=180):
        if server not in READ_TOOLS:
            raise ValueError("Unsupported collection server")
        self.server = server
        self.timeout = timeout
        self.counter = 0
        self.inbox = queue.Queue()
        log = open(Path(directory) / f"mcp-{server}.log", "ab", buffering=0)
        workdir = Path.home() / "universe"
        environment = os.environ.copy()
        if server == "salesforce":
            environment["SALESFORCE_GATEWAY_ENV"] = "prod"
        self.proc = subprocess.Popen(["dbexec", "repo", "run", "mcp", "start-single", server], env=environment, cwd=str(workdir if workdir.is_dir() else Path(directory)), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, start_new_session=True)
        self.log = log
        threading.Thread(target=self._read, daemon=True).start()
        self.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "support-workbench", "version": "0.1.0"}})
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _read(self):
        for line in self.proc.stdout:
            try:
                self.inbox.put(json.loads(line))
            except ValueError:
                # dbexec startup notices are not protocol messages.
                self.log.write(line)
        self.inbox.put({"eof": True})

    def send(self, value):
        self.proc.stdin.write((json.dumps(value) + "\n").encode())
        self.proc.stdin.flush()

    def request(self, method, params):
        self.counter += 1
        identifier = self.counter
        self.send({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        while True:
            wait = deadline - time.monotonic()
            if wait <= 0:
                self.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": identifier, "reason": "Workbench deadline"}})
                raise TimeoutError(f"{self.server} {method} timed out")
            try:
                message = self.inbox.get(timeout=wait)
            except queue.Empty:
                raise TimeoutError(f"{self.server} {method} timed out")
            if message.get("eof"):
                raise RuntimeError(f"{self.server} exited before replying")
            if message.get("id") == identifier:
                if "error" in message:
                    raise RuntimeError(str(message["error"])[:2000])
                return message.get("result", {})
            if "method" in message and "id" in message:
                self.send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "Client interaction is unavailable; show needs_auth in Workbench"}})

    def call(self, name, arguments):
        if name not in READ_TOOLS[self.server]:
            raise ValueError("Tool is outside the collection allowlist")
        if name == "execute_parameterized_sql":
            statement = arguments["statement"].lstrip().lower()
            if not statement.startswith(("select ", "with ", "describe ")):
                raise ValueError("Collection SQL must be read-only")
        return decode_result(self.request("tools/call", {"name": name, "arguments": arguments}))

    def close(self):
        import signal
        if self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait()
        self.log.close()


if __name__ == "__main__":
    import sys
    directory = Path.home() / "workbench-runtime"
    directory.mkdir(exist_ok=True, mode=0o700)
    client = MCP(sys.argv[1], directory)
    try:
        print(json.dumps(client.call(sys.argv[2], json.loads(sys.argv[3])), ensure_ascii=False))
    finally:
        client.close()
