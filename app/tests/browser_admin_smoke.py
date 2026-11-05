"""Synthetic-only admin queue preview/freeze browser check."""
import asyncio
import json
import urllib.request

import websockets


async def main():
    target = next(t["webSocketDebuggerUrl"] for t in json.load(urllib.request.urlopen("http://127.0.0.1:9223/json/list")) if t["type"] == "page")
    async with websockets.connect(target) as ws:
        seq = 0

        async def call(method, params):
            nonlocal seq
            seq += 1
            mine = seq
            await ws.send(json.dumps({"id": mine, "method": method, "params": params}))
            while True:
                event = json.loads(await ws.recv())
                if event.get("id") == mine:
                    return event.get("result", {})

        async def js(expr):
            result = await call("Runtime.evaluate", {"expression": expr, "returnByValue": True})
            return result.get("result", {}).get("value")

        async def until(expr, target_value=True):
            for _ in range(100):
                if await js(expr) == target_value:
                    return
                await asyncio.sleep(0.1)
            raise RuntimeError("ADMIN_BROWSER_FLOW_FAILED")

        await call("Page.navigate", {"url": "http://127.0.0.1:8765/"})
        await until("!!document.getElementById('username') || !!document.getElementById('queue') || !!document.getElementById('queue-form')")
        if await js("!!document.getElementById('queue')"):
            await js("document.getElementById('logout').click()")
            await until("!!document.getElementById('username')")
        if await js("!!document.getElementById('username')"):
            await js("document.getElementById('username').value='demo_admin';document.getElementById('password').value='synthetic-only-password';document.getElementById('login').requestSubmit()")
        await until("!!document.getElementById('queue-form')")
        await js("(()=>{let f=document.getElementById('queue-form');f.querySelector('[name=queue_id]').value='admin_browser_test';f.querySelector('[name=project]').value='demo';f.querySelector('[name=cohort]').value='dev';f.querySelector('[name=mode]').value='assisted';f.querySelector('[name=patient_budget]').value='1';f.querySelector('[name=reader1]').value='demo_reader1';f.querySelector('[name=reader2]').value='demo_reader2';document.getElementById('preview').click()})()")
        await until("document.getElementById('admin-output').textContent.includes('eligible_patients')")
        await js("document.getElementById('freeze').click()")
        await until("document.getElementById('admin-output').textContent.includes('admin_browser_test')")
        print(json.dumps({"admin_browser_flow": "PASS", "preview_then_freeze": True}))


asyncio.run(main())
