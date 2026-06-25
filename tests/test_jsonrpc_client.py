import json
import queue
import unittest

from codex_usage_widget import AppServerClient, JsonRpcError


class FakeStdin:
    def __init__(self, process):
        self.process = process

    def write(self, line):
        self.process.sent_lines.append(line)
        message = json.loads(line)
        if "id" in message:
            if message["method"] == "initialize":
                self.process.stdout_lines.put(json.dumps({"id": message["id"], "result": {}}) + "\n")
            elif message["method"] == "account/rateLimits/read":
                self.process.stdout_lines.put(
                    json.dumps(
                        {
                            "id": message["id"],
                            "result": {
                                "rateLimits": {
                                    "limitId": "codex",
                                    "primary": {"usedPercent": 25, "windowDurationMins": 15, "resetsAt": 1},
                                }
                            },
                        }
                    )
                    + "\n"
                )

    def flush(self):
        pass


class FakeStdout:
    def __init__(self):
        self.lines = queue.Queue()

    def put(self, line):
        self.lines.put(line)

    def __iter__(self):
        return self

    def __next__(self):
        line = self.lines.get(timeout=1)
        if line is None:
            raise StopIteration
        return line


class FakeProcess:
    def __init__(self, *args, **kwargs):
        self.sent_lines = []
        self.stdout_lines = FakeStdout()
        self.stdin = FakeStdin(self)
        self.stdout = self.stdout_lines
        self.stderr = FakeStdout()
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0
        self.stdout_lines.put(None)


class JsonRpcClientTests(unittest.TestCase):
    def test_initialize_then_initialized_are_sent_in_order(self):
        process = FakeProcess()
        client = AppServerClient(popen_factory=lambda *args, **kwargs: process)

        client.start()

        messages = [json.loads(line) for line in process.sent_lines]
        self.assertEqual(messages[0]["method"], "initialize")
        self.assertEqual(messages[1]["method"], "initialized")
        client.stop()

    def test_request_id_maps_to_response(self):
        process = FakeProcess()
        client = AppServerClient(popen_factory=lambda *args, **kwargs: process)
        client.start()

        snapshots = client.read_rate_limits()

        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].used_percent, 25)
        self.assertEqual(snapshots[0].remaining_percent, 75)
        client.stop()

    def test_jsonrpc_error_is_raised_for_ui(self):
        process = FakeProcess()
        client = AppServerClient(popen_factory=lambda *args, **kwargs: process)
        client.start()

        original_write = process.stdin.write

        def write_error(line):
            message = json.loads(line)
            if message.get("method") == "account/rateLimits/read":
                process.sent_lines.append(line)
                process.stdout_lines.put(
                    json.dumps({"id": message["id"], "error": {"code": 401, "message": "Unauthorized"}}) + "\n"
                )
            else:
                original_write(line)

        process.stdin.write = write_error

        with self.assertRaises(JsonRpcError) as raised:
            client.read_rate_limits()

        self.assertEqual(raised.exception.code, 401)
        self.assertEqual(raised.exception.message, "Unauthorized")
        client.stop()


if __name__ == "__main__":
    unittest.main()
