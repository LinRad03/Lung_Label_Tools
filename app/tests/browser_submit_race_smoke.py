"""Synthetic browser acceptance: a submit waits for an in-flight draft save."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import urllib.request
from pathlib import Path

import websockets


async def main() -> None:
    root = Path(__file__).resolve().parents[1]
    work = Path(tempfile.mkdtemp(prefix="annotator-submit-qa-", dir="/tmp"))
    profile = work / "chrome"
    python = root / ".venv/bin/python"
    server = chrome = None
    try:
        env = dict(os.environ, ANNOTATOR_DEMO_ROOT=str(work / "demo"))
        subprocess.run([str(python), "-m", "scripts.make_synthetic_demo"], cwd=root, env=env,
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        env.update(ANNOTATOR_DB=str(work / "demo/annotations.sqlite3"),
                   ANNOTATOR_DATA_ROOTS=str(work / "demo/data"),
                   ANNOTATOR_DIRECT_USER="demo_reader1")
        server = subprocess.Popen([str(python), "-m", "uvicorn", "annotator.app:app",
                                   "--host", "127.0.0.1", "--port", "8768", "--no-access-log"],
                                  cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                urllib.request.urlopen("http://127.0.0.1:8768/", timeout=1).close()
                break
            except Exception:
                await asyncio.sleep(.1)
        else:
            raise RuntimeError("SYNTHETIC_SERVICE_NOT_READY")
        chrome = subprocess.Popen(["/usr/bin/google-chrome", "--headless=new", "--disable-gpu",
                                   "--disable-webgl", "--no-first-run", "--no-default-browser-check",
                                   "--remote-debugging-port=9227", f"--user-data-dir={profile}", "about:blank"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                targets = json.load(urllib.request.urlopen("http://127.0.0.1:9227/json/list", timeout=1))
                break
            except Exception:
                await asyncio.sleep(.1)
        else:
            raise RuntimeError("SYNTHETIC_CHROME_NOT_READY")
        endpoint = next(x["webSocketDebuggerUrl"] for x in targets if x["type"] == "page")
        async with websockets.connect(endpoint, max_size=30_000_000) as ws:
            seq = 0

            async def call(method: str, params: dict | None = None) -> dict:
                nonlocal seq
                seq += 1
                index = seq
                await ws.send(json.dumps({"id": index, "method": method, "params": params or {}}))
                while True:
                    answer = json.loads(await ws.recv())
                    if answer.get("id") == index:
                        if "error" in answer:
                            raise RuntimeError("SYNTHETIC_CDP_ERROR")
                        return answer.get("result", {})

            async def js(expression: str):
                result = await call("Runtime.evaluate", {"expression": expression,
                                                         "returnByValue": True, "awaitPromise": True})
                if "exceptionDetails" in result:
                    raise RuntimeError("SYNTHETIC_JS_ERROR")
                return result.get("result", {}).get("value")

            async def until(expression: str, check=lambda x: bool(x), seconds=45):
                for _ in range(seconds * 5):
                    value = await js(expression)
                    if check(value):
                        return value
                    await asyncio.sleep(.2)
                raise RuntimeError("SYNTHETIC_BROWSER_TIMEOUT")

            await call("Page.navigate", {"url": "http://127.0.0.1:8768/"})
            await until("!!document.getElementById('queue')")
            await js("document.getElementById('queue').value='demo_dev';document.getElementById('queue').dispatchEvent(new Event('change'))")
            await js("document.getElementById('load').click()")
            await until("document.getElementById('status').textContent", lambda x: x and '已加载' in x)
            await js("document.querySelector('input[name=state][value=negative]').click()")
            assert await js("!document.getElementById('read-complete-control').hidden && document.getElementById('submit').disabled")
            await js("document.getElementById('read-complete').click()")
            assert await js("!document.getElementById('submit').disabled")
            await js("""window.__submitAudit={draft:0,submit:0};const original=window.fetch.bind(window);
                window.fetch=(url,options)=>{const path=String(url);
                  if(path.endsWith('/draft')){window.__submitAudit.draft++;return new Promise(resolve=>setTimeout(()=>resolve(original(url,options)),700));}
                  if(path.endsWith('/submit'))window.__submitAudit.submit++;
                  return original(url,options);};""")
            await js("document.getElementById('save').click();document.getElementById('submit').click()")
            await asyncio.sleep(.2)
            if await js("window.__submitAudit.submit") != 0:
                raise RuntimeError("SUBMIT_RACED_IN_FLIGHT_DRAFT")
            await until("window.__submitAudit.submit", lambda x: x == 1)
            await until("document.getElementById('status').textContent", lambda x: x and '下一例已加载' in x)
        db = sqlite3.connect(work / "demo/annotations.sqlite3")
        try:
            submitted = db.execute("SELECT COUNT(*) FROM tasks WHERE queue_id='demo_dev' AND reader='demo_reader1' AND status='submitted'").fetchone()[0]
        finally:
            db.close()
        if submitted != 1:
            raise RuntimeError("SYNTHETIC_SUBMISSION_NOT_PERSISTED")
        print(json.dumps({"in_flight_draft_then_submit": "PASS", "synthetic_submitted_tasks": submitted,
                          "real_patient_annotations_written": False}))
    finally:
        for process in (chrome, server):
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
