"""Runtime confirmation of three planted Python defects in samples/catalog-api-fastapi.

Run from the sample root with its dependencies installed:
    python ../../benchmarks/dev/runtime_checks/catalog_api_runtime_check.py

Uses a local TCP server that accepts connections and never responds. No network access.
"""

import asyncio
import os
import socket
import threading
import time

server = socket.socket()
server.bind(("127.0.0.1", 0))
server.listen(100)
port = server.getsockname()[1]
connections: list[float] = []
open_sockets: list[socket.socket] = []  # keep references so connections stay open


def accept_forever() -> None:
    while True:
        conn, _ = server.accept()
        open_sockets.append(conn)
        connections.append(time.monotonic())


threading.Thread(target=accept_forever, daemon=True).start()
os.environ["INVENTORY_URL"] = f"http://127.0.0.1:{port}"
os.environ["RECOMMENDATIONS_URL"] = f"http://127.0.0.1:{port}"

from app.clients import inventory, recommendations  # noqa: E402


async def main() -> None:
    t0, n0 = time.monotonic(), len(connections)
    try:
        async with asyncio.timeout(4.0):
            await inventory.fetch_stock("sku1")
    except TimeoutError:
        pass
    print(f"FN-RES-002: outer timeout after {time.monotonic() - t0:.1f}s, attempts started={len(connections) - n0} "
          "of 3 configured (worst-case window 3x2s + 2x1s = 8s)")

    t0, n0 = time.monotonic(), len(connections)
    task = asyncio.create_task(inventory.fetch_warehouses("sku1"))
    await asyncio.sleep(12)
    still_running = not task.done()
    task.cancel()
    print(f"FN-RES-004: after 12s still retrying={still_running}, attempts so far={len(connections) - n0}")

    t0 = time.monotonic()
    try:
        async with asyncio.timeout(10):
            await recommendations.related("sku1")
    except TimeoutError:
        print(f"FN-RES-001: timeout=None call still waiting at {time.monotonic() - t0:.1f}s (cut by harness)")


asyncio.run(main())
