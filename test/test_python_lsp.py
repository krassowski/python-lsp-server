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
                req = {
                    "jsonrpc": "2.0",
                    "id": idx,
                    "method": "initialize",
                    "params": {},
                }

                try:
                    await asyncio.wait_for(
                        ws.send(json.dumps(req, ensure_ascii=False)), timeout=5
                    )
                    raw = await asyncio.wait_for(ws.recv(), timeout=5)
                except asyncio.TimeoutError:
                    return None
                obj = json.loads(raw)
                return obj.get("id")

        returned_id = asyncio.run(do_initialize(i))
        with lock:
            received.append(returned_id)

    # launch threads
    threads = []
    for i in range(1, NUM_REQUESTS + 1):
        t = threading.Thread(target=thread_target, args=(i,))
        t.start()
        threads.append(t)

    # wait for them all
    for t in threads:
        t.join(timeout=20)
        assert not t.is_alive(), f"Worker thread {t} hung!"

    # validate
    assert set(received) == set(range(1, NUM_REQUESTS + 1)), f"got IDs {received}"
