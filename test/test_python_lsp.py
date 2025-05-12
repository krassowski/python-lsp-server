import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time

import pytest
import websockets

NUM_CLIENTS = 10
NUM_REQUESTS = 10
TEST_PORT = 5102
HOST = "127.0.0.1"
MAX_STARTUP_SECONDS = 5.0
CHECK_INTERVAL = 0.1


@pytest.fixture(scope="module", autouse=True)
def ws_server_subprocess():
    cmd = [
        sys.executable,
        "-m",
        "pylsp.__main__",
        "--ws",
        "--host",
        HOST,
        "--port",
        str(TEST_PORT),
        # TODO: enabling verbose logging while stderr is piped
        # makes this crash; I believe I saw this in a
        # deployment too.
        # "-vv",
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=os.environ.copy(),
    )

    deadline = time.time() + MAX_STARTUP_SECONDS
    while True:
        try:
            with socket.create_connection(
                ("127.0.0.1", TEST_PORT), timeout=CHECK_INTERVAL
            ):
                break
        except (ConnectionRefusedError, OSError):
            if time.time() > deadline:
                proc.kill()
                out, err = proc.communicate(timeout=1)
                raise RuntimeError(
                    f"Server didn’t start listening on port {TEST_PORT} in time.\n"
                    f"STDOUT:\n{out.decode()}\nSTDERR:\n{err.decode()}"
                )
            time.sleep(CHECK_INTERVAL)

    yield  # run the tests

    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_concurrent_ws_requests():
    received = []
    lock = threading.Lock()

    def thread_target(i: int):
        async def do_initialize(idx):
            uri = f"ws://{HOST}:{TEST_PORT}"
            async with websockets.connect(uri) as ws:
                # send initialize
                init_request = {
                    "jsonrpc": "2.0",
                    "id": 2 * idx,
                    "method": "initialize",
                    "params": {},
                }
                did_open_request = {
                    "jsonrpc": "2.0",
                    "id": 2 * idx + 1,
                    "method": "textDocument/didOpen",
                    "params": {
                        "textDocument": {
                            "uri": "test.py",
                            "languageId": "python",
                            "version": 0,
                            "text": "def test(): pass\ntest",
                        }
                    },
                }
                hover_request = {
                    "jsonrpc": "2.0",
                    "id": 2 * idx + 1,
                    "method": "textDocument/hover",
                    "params": {
                        "textDocument": {
                            "uri": "test.py",
                        },
                        "position": {
                            "line": 1,
                            "character": 1,
                        },
                    },
                }

                async def communicate_and_parse_json(request: dict):
                    await asyncio.wait_for(
                        ws.send(json.dumps(request, ensure_ascii=False)), timeout=5
                    )
                    raw = await asyncio.wait_for(ws.recv(), timeout=10)
                    # test it can be parsed
                    json.loads(raw)

                try:
                    await communicate_and_parse_json(init_request)
                    await communicate_and_parse_json(did_open_request)
                    # requests = []
                    for i in range(NUM_REQUESTS):
                        # requests.append(communicate_and_parse_json(hover_request))
                        await communicate_and_parse_json(hover_request)
                    # await asyncio.gather(*requests)
                except json.JSONDecodeError:
                    return False
                return True

        returned_id = asyncio.run(do_initialize(i))
        with lock:
            received.append(returned_id)

    # launch threads
    threads = []
    for i in range(1, NUM_CLIENTS + 1):
        t = threading.Thread(target=thread_target, args=(i,))
        t.start()
        threads.append(t)

    # wait for them all
    for t in threads:
        t.join(timeout=20)
        assert not t.is_alive(), f"Worker thread {t} hung!"

    # validate
    assert set(received) == {True}
