"""Private real-input I/O check: no screenshot, no report, no annotation submission."""
from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.request
from pathlib import Path

import websockets


async def main() -> None:
    private = Path(os.environ["ANNOTATOR_REAL_SMOKE_DIR"]).resolve()
    user = os.environ.get("ANNOTATOR_BROWSER_USER", "tech_reader")
    credential_file = os.environ.get("ANNOTATOR_BROWSER_CREDENTIAL_FILE", "technical_passwords.json")
    password = json.loads((private / credential_file).read_text())[user]
    service_port = int(os.environ.get("ANNOTATOR_BROWSER_SERVICE_PORT", "8766"))
    queue = os.environ.get("ANNOTATOR_BROWSER_QUEUE", "REAL_INPUT_TECH_ONLY")
    targets = json.load(urllib.request.urlopen("http://127.0.0.1:9224/json/list"))
    url = next(t["webSocketDebuggerUrl"] for t in targets if t["type"] == "page")
    start = time.monotonic()
    async with websockets.connect(url, max_size=30_000_000) as ws:
        seq = 0

        async def call(method: str, params=None):
            nonlocal seq
            seq += 1
            target = seq
            await ws.send(json.dumps({"id": target, "method": method, "params": params or {}}))
            while True:
                message = json.loads(await ws.recv())
                if message.get("id") == target:
                    if "error" in message:
                        raise RuntimeError("BROWSER_COMMAND_FAILED")
                    return message.get("result", {})

        async def js(expression: str):
            data = await call("Runtime.evaluate", {"expression": expression, "returnByValue": True})
            return data.get("result", {}).get("value")

        async def until(expression: str, target=True, timeout=120):
            for _ in range(timeout * 5):
                if await js(expression) == target:
                    return
                await asyncio.sleep(0.2)
            raise RuntimeError("REAL_BROWSER_PHASE_TIMEOUT")

        await call("Page.navigate", {"url": f"http://127.0.0.1:{service_port}/"})
        await until("!!document.getElementById('username')")
        # JS string quoting is safe here; this private random password is never printed.
        await js("document.getElementById('username').value=" + json.dumps(user) + ";document.getElementById('password').value=" + json.dumps(password) + ";document.getElementById('login').requestSubmit()")
        await until("!!document.getElementById('queue')")
        await js("document.getElementById('queue').value=" + json.dumps(queue) + ";document.getElementById('queue').dispatchEvent(new Event('change'));document.getElementById('load').click()")
        await until("document.getElementById('status').textContent", "已加载", timeout=180)
        ct_seconds = round(time.monotonic() - start, 1)
        canvas = await js("[document.getElementById('gl').width,document.getElementById('gl').height]")
        if min(canvas) < 100:
            raise RuntimeError("REAL_CT_CANVAS_FAILED")
        point = await js("(()=>{let r=document.getElementById('gl').getBoundingClientRect();return{x:r.x+r.width/4,y:r.y+r.height/4}})()")
        await call("Input.dispatchMouseEvent", {"type": "mousePressed", "x": point["x"], "y": point["y"], "button": "left", "clickCount": 1})
        await call("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": point["x"], "y": point["y"], "button": "left", "clickCount": 1})
        await until("!document.getElementById('coords').textContent.endsWith('—')", timeout=10)
        if await js("document.getElementById('mask-control').hidden"):
            raise RuntimeError("REAL_MASK_CONTROL_MISSING")
        await js("document.getElementById('mask-toggle').click()")
        await until("document.getElementById('mask-toggle').dataset.loaded", "yes", timeout=120)
        print(json.dumps({"real_ct_browser_load": "PASS", "ras_cursor": "PASS", "aligned_mask_overlay": "PASS",
                          "ct_load_seconds": ct_seconds, "total_seconds": round(time.monotonic() - start, 1),
                          "annotation_submitted": False, "report_displayed": False}))


if __name__ == "__main__":
    asyncio.run(main())
