"""Synthetic end-to-end browser check using an existing headless Chrome CDP port."""
from __future__ import annotations

import asyncio
import base64
import json
import urllib.request
from pathlib import Path

import websockets


async def run() -> None:
    targets = json.load(urllib.request.urlopen("http://127.0.0.1:9223/json/list"))
    url = next(t["webSocketDebuggerUrl"] for t in targets if t["type"] == "page")
    async with websockets.connect(url, max_size=30_000_000) as ws:
        counter = 0

        async def command(method, params=None):
            nonlocal counter
            counter += 1
            target = counter
            await ws.send(json.dumps({"id": target, "method": method, "params": params or {}}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == target:
                    if "error" in msg:
                        raise RuntimeError(msg["error"]["message"])
                    return msg.get("result", {})

        async def js(expr):
            r = await command("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
            if "exceptionDetails" in r:
                raise RuntimeError(r["exceptionDetails"].get("text", "JS_EXCEPTION"))
            return r.get("result", {}).get("value")

        async def until(expr, expected=True, seconds=30):
            for _ in range(seconds * 5):
                if (await js(expr)) == expected:
                    return
                await asyncio.sleep(0.2)
            state = await js("({status:document.getElementById('status')?.textContent,caseTitle:document.getElementById('case-title')?.textContent,login:!!document.getElementById('username')})")
            raise TimeoutError(f"browser condition failed: {expr}; state={state}")

        await command("Page.enable")
        await command("Runtime.enable")
        await command("Page.navigate", {"url": "http://127.0.0.1:8765/"})
        await until("!!document.getElementById('username') || !!document.getElementById('queue')")
        if await js("!!document.getElementById('username')"):
            await js("document.getElementById('username').value='demo_reader1';document.getElementById('password').value='synthetic-only-password';document.getElementById('login').requestSubmit();")
        await until("!!document.getElementById('queue')")
        await js("document.getElementById('queue').value='demo_blind';document.getElementById('queue').dispatchEvent(new Event('change'))")
        await js("document.getElementById('load').click()")
        await until("document.getElementById('status').textContent", "已加载", seconds=45)
        if await js("document.getElementById('report-panel').style.display") != 'none':
            raise RuntimeError('BLIND_REPORT_VISIBLE')
        dims = await js("[document.getElementById('gl').width,document.getElementById('gl').height]")
        if not dims or min(dims) < 100:
            raise RuntimeError("CANVAS_NOT_RENDERED")
        await command("Emulation.setDeviceMetricsOverride", {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        bounds = await js("(()=>{let r=document.getElementById('gl').getBoundingClientRect();return {x:r.x+r.width/4,y:r.y+r.height/4}})()")
        screenshot = await command("Page.captureScreenshot", {"format": "png"})
        debug_target = Path(__file__).resolve().parents[1] / "runtime" / "synthetic_demo" / "browser_debug.png"
        debug_target.write_bytes(base64.b64decode(screenshot["data"]))
        print(json.dumps({"canvas_dims": dims, "click_xy": bounds, "page_status": await js("document.getElementById('status').textContent"), "screenshot_bytes": debug_target.stat().st_size}))
        await command("Input.dispatchMouseEvent", {"type": "mousePressed", "x": bounds["x"], "y": bounds["y"], "button": "left", "clickCount": 1})
        await command("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": bounds["x"], "y": bounds["y"], "button": "left", "clickCount": 1})
        screenshot_after = await command("Page.captureScreenshot", {"format": "png"})
        (Path(__file__).resolve().parents[1] / "runtime" / "synthetic_demo" / "browser_after_click.png").write_bytes(base64.b64decode(screenshot_after["data"]))
        await until("!document.getElementById('coords').textContent.endsWith('—')", seconds=5)
        print(json.dumps({"cursor": await js("document.getElementById('coords').textContent"),
                          "status": await js("document.getElementById('status').textContent")}))
        await js("document.getElementById('add-point').click()")
        await until("document.querySelectorAll('.lesion').length", 1)
        await js("document.querySelector('input[value=positive]').click();document.getElementById('read-complete').click();")
        await js("document.getElementById('save').click()")
        await until("document.getElementById('status').textContent", "已保存")
        await js("document.getElementById('submit').click()")
        await until("document.getElementById('status').textContent", "已加载", seconds=45)
        if await js("document.getElementById('report-panel').style.display") == 'none':
            await js("document.querySelector('input[value=negative]').click();document.getElementById('read-complete').click();document.getElementById('submit').click();")
            await until("document.getElementById('status').textContent", "已加载", seconds=45)
        if await js("document.getElementById('report-panel').style.display") != 'block':
            raise RuntimeError('POST_LOCK_REPORT_NOT_AVAILABLE')
        if await js("document.getElementById('coords').textContent") != 'RAS mm —':
            raise RuntimeError('PREVIOUS_CASE_COORDINATE_RETAINED')
        screenshot = await command("Page.captureScreenshot", {"format": "png"})
        target = Path(__file__).resolve().parents[1] / "runtime" / "synthetic_demo" / "browser_acceptance.png"
        target.write_bytes(base64.b64decode(screenshot["data"]))
        await js("document.getElementById('queue').value='demo_dev';document.getElementById('queue').dispatchEvent(new Event('change'));document.getElementById('load').click()")
        await until("document.getElementById('status').textContent", "已加载", seconds=45)
        if await js("document.getElementById('mask-control').hidden"):
            raise RuntimeError('ASSISTED_MASK_CONTROL_MISSING')
        await js("document.getElementById('mask-toggle').click()")
        await until("document.getElementById('mask-toggle').dataset.loaded", "yes", seconds=15)
        print(json.dumps({"browser_flow": "PASS", "canvas_size": dims, "submitted_then_advanced": True,
                          "mask_overlay_loaded": True, "screenshot_bytes": target.stat().st_size}))


if __name__ == "__main__":
    asyncio.run(run())
