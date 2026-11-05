"""Headless Chrome read-only UI check on one real development CT; no submission."""
import asyncio
import base64
import io
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets
from PIL import Image


async def main():
    visual = os.environ.get("ANNOTATOR_VISUAL_QA") == "1"
    gpu = os.environ.get("ANNOTATOR_GPU_QA") == "1"
    readonly_task = os.environ.get("ANNOTATOR_READONLY_TASK_ID")
    if readonly_task:
        username, password, queue = "1", "1", "DEV_PULMONARY_BULLA_535_V1"
    elif visual:
        private = Path(os.environ["ANNOTATOR_PILOT_DIR"])
        username = "pilot_reader2"
        password = json.loads((private / "pilot_accounts.json").read_text())[username]
        queue = os.environ.get("ANNOTATOR_VISUAL_QUEUE", "DEV_BRONCHIECTASIS_V1")
    else:
        username, password, queue = "1", "1", "DEV_BRONCHIECTASIS_435_EXTRA_V1"
    profile = tempfile.mkdtemp(prefix="jepa-annotator-qa-", dir="/tmp")
    flags = (["--enable-webgl", "--ignore-gpu-blocklist", "--use-gl=angle", "--use-angle=gl"] if os.environ.get("ANNOTATOR_EGL_QA") == "1"
             else ["--enable-webgl", "--ignore-gpu-blocklist"] if os.environ.get("ANNOTATOR_HARDWARE_QA") == "1"
             else (["--enable-webgl", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
                   if gpu else ["--disable-gpu", "--disable-webgl"]))
    chrome = subprocess.Popen([
        "/usr/bin/google-chrome", "--headless=new", "--no-first-run", "--no-default-browser-check",
        *(["--window-size=1600,900"] if os.environ.get("ANNOTATOR_LAYOUT_QA") == "1" else []),
        *flags, "--remote-debugging-port=9226",
        f"--user-data-dir={profile}", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        targets = None
        for _ in range(100):
            try:
                targets = json.load(urllib.request.urlopen("http://127.0.0.1:9226/json/list", timeout=1))
                break
            except Exception:
                await asyncio.sleep(.1)
        if not targets:
            raise RuntimeError("CHROME_NOT_READY")
        endpoint = next(t["webSocketDebuggerUrl"] for t in targets if t["type"] == "page")
        async with websockets.connect(endpoint, max_size=30_000_000) as ws:
            seq = 0

            async def call(method, params=None):
                nonlocal seq
                seq += 1
                target = seq
                await ws.send(json.dumps({"id": target, "method": method, "params": params or {}}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == target:
                        if "error" in msg:
                            raise RuntimeError("CDP_COMMAND_FAILED")
                        return msg.get("result", {})

            async def js(expression):
                data = await call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
                return data.get("result", {}).get("value")

            async def until(expression, check=lambda x: bool(x), seconds=120):
                for _ in range(seconds * 5):
                    value = await js(expression)
                    if check(value):
                        return value
                    await asyncio.sleep(.2)
                raise RuntimeError("BROWSER_UI_TIMEOUT")

            async def colored_canvas_pixels():
                rect = await js("(()=>{const r=document.getElementById('gl').getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height}})()")
                shot = await call("Page.captureScreenshot", {"format":"png","captureBeyondViewport":False,
                    "clip":{**rect,"scale":1}})
                pixels = Image.open(io.BytesIO(base64.b64decode(shot["data"]))).convert("RGB").getdata()
                return sum(1 for red, green, blue in pixels if max(red, green, blue)-min(red, green, blue)>45)

            await call("Page.navigate", {"url": "http://127.0.0.1:8767/"})
            await until("!!document.getElementById('username')", seconds=20)
            await js("document.getElementById('username').value=" + json.dumps(username) +
                     ";document.getElementById('password').value=" + json.dumps(password) +
                     ";document.getElementById('login').requestSubmit()")
            await until("!!document.getElementById('queue')", seconds=20)
            await js("document.getElementById('queue').value=" + json.dumps(queue) + ";document.getElementById('queue').dispatchEvent(new Event('change'))")
            await until("document.getElementById('review-jump').options.length", lambda x: x > 2, seconds=20)
            if not readonly_task:
                await js("document.getElementById('load').click()")
            if readonly_task:
                await until("document.getElementById('review-jump').options.length", lambda x: x > 2, seconds=20)
                await js("document.getElementById('review-jump').value=" + json.dumps(readonly_task) + ";document.getElementById('review-jump').dispatchEvent(new Event('change'))")
                try:
                    await until("document.getElementById('status').textContent", lambda x: x and x.startswith("只读回顾"), seconds=int(os.environ.get("ANNOTATOR_READONLY_TIMEOUT", "180")))
                except RuntimeError:
                    state = await js("({status:document.getElementById('status')?.textContent,mode:document.getElementById('render-mode')?.textContent,selected:document.getElementById('review-jump')?.value,reportLength:document.getElementById('report')?.textContent.length})")
                    raise RuntimeError("READONLY_CASE_NOT_READY:" + json.dumps(state, ensure_ascii=False))
                safe = await js("['save','submit','add-point','box-start','box-end'].every(id=>document.getElementById(id).disabled)")
                if not safe:
                    raise RuntimeError("READONLY_CONTROLS_ACTIVE")
                if await js("!!document.getElementById('skip')"):
                    raise RuntimeError("SKIP_OPTION_STILL_VISIBLE")
                if os.environ.get("ANNOTATOR_ORIENTATION_QA") == "1":
                    if await js("document.getElementById('render-mode').textContent") != "WebGL 阅片":
                        raise RuntimeError("ORIENTATION_REQUIRES_WEBGL_SLICE")
                    for offset in (-40, 40):
                        spot = await js("(()=>{const m=document.getElementById('markup'),r=m.getBoundingClientRect(),c=JSON.parse(m.dataset.tileCenter);return {x:r.left+(c[0]/m.width)*r.width+" + str(offset) + ",y:r.top+(c[1]/m.height)*r.height}})()")
                        await call("Input.dispatchMouseEvent", {"type":"mouseMoved","x":spot["x"],"y":spot["y"]})
                        await asyncio.sleep(.12)
                        value = await js("Number(document.getElementById('hover-coords').textContent.split('RAS mm ')[1]?.split(',')[0])")
                        if offset < 0:
                            patient_right = value
                        else:
                            patient_left = value
                    if not patient_right > patient_left:
                        raise RuntimeError("AXIAL_LEFT_RIGHT_MIRRORED")
                if os.environ.get("ANNOTATOR_LAYOUT_QA") == "1":
                    layout = await js("(()=>{const r=document.getElementById('report-panel').getBoundingClientRect(),v=document.querySelector('.viewer').getBoundingClientRect(),t=document.querySelector('.task').getBoundingClientRect();return {width:innerWidth,reportRight:r.right,viewerLeft:v.left,viewerRight:v.right,taskLeft:t.left}})()")
                    if layout["width"] <= 1150 or layout["reportRight"] > layout["viewerLeft"] or layout["viewerRight"] > layout["taskLeft"]:
                        raise RuntimeError("REPORT_NOT_LEFT_OF_CT")
                    split = await js("(()=>{const a=document.querySelector('.report-source').getBoundingClientRect(),b=document.querySelector('.annotation-overview').getBoundingClientRect();return {first:a.height,second:b.height,lesions:document.querySelector('.annotation-overview #lesions')!==null}})()")
                    if not split["lesions"] or min(split["first"], split["second"]) < 100:
                        raise RuntimeError("REPORT_ANNOTATION_SPLIT_MISSING")
                if os.environ.get("ANNOTATOR_REVIEW_PANEL_QA") == "1":
                    progress_center = await js("(()=>{const r=document.querySelector('.review-progress .progress-label').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()")
                    await call("Input.dispatchMouseEvent", {"type":"mousePressed","x":progress_center["x"],"y":progress_center["y"],"button":"left","clickCount":1})
                    await call("Input.dispatchMouseEvent", {"type":"mouseReleased","x":progress_center["x"],"y":progress_center["y"],"button":"left","clickCount":1})
                    await until("document.getElementById('review-panel').hidden", lambda x: x is False, seconds=5)
                    await until("document.querySelectorAll('#review-panel-list [data-review-task]').length", lambda x: x > 0, seconds=10)
                    skipped = await js("document.querySelector('#review-panel-list [data-review-task]').dataset.reviewTask")
                    await js("document.querySelector('#review-panel-list [data-review-task]').click()")
                    await until("document.getElementById('review-jump').value", lambda x: x == skipped, seconds=180)
                    await until("document.getElementById('status').textContent", lambda x: x and x.startswith('已加载'), seconds=180)
                    await js("document.getElementById('review-jump').value=" + json.dumps(readonly_task) + ";document.getElementById('review-jump').dispatchEvent(new Event('change'))")
                    await until("document.getElementById('review-jump').value", lambda x: x == readonly_task, seconds=180)
                    await until("document.getElementById('status').textContent", lambda x: x and x.startswith('只读回顾'), seconds=180)
                    await js("document.getElementById('review-progress').click();document.querySelector('[data-review-kind=discordant]').click()")
                    await until("document.getElementById('review-panel-list').textContent", lambda x: x and x != '正在读取…', seconds=10)
                    await js("document.getElementById('review-close').click()")
                lesions = await js("document.querySelectorAll('#lesions .lesion').length")
                await until("document.querySelectorAll('#block-lesion option').length", lambda x: x == lesions and x > 0, seconds=20)
                if os.environ.get("ANNOTATOR_PROGRESS_QA") == "1":
                    progress = await js("(async()=>{const q=document.getElementById('queue').value;const r=await (await fetch('/api/queues/'+encodeURIComponent(q)+'/summary')).json();const total=r.counts.reduce((n,x)=>n+Number(x.n),0);const submitted=r.counts.filter(x=>x.status==='submitted').reduce((n,x)=>n+Number(x.n),0);const bar=document.getElementById('review-progress-bar');return {total,submitted,max:Number(bar.max),value:Number(bar.value),label:document.getElementById('review-progress-text').textContent}})()")
                    if progress["max"] != progress["total"] or progress["value"] != progress["submitted"] or "%" not in progress["label"]:
                        raise RuntimeError("REVIEW_PROGRESS_MISMATCH")
                    await js("document.getElementById('review-progress').click()")
                    await until("document.getElementById('review-panel').hidden", lambda x: x is False, seconds=5)
                    await until("document.querySelector('[data-review-kind=agree] span').textContent", lambda x: x != '—', seconds=10)
                    await js("document.getElementById('review-close').click()")
                blocks = await until("document.querySelectorAll('#block-grid .active').length", lambda x: x and x > 0, seconds=180)
                if os.environ.get("ANNOTATOR_FINE_GRID_QA") == "1":
                    for size in (16, 32):
                        await js(f"(()=>{{const s=document.getElementById('grid-resolution');s.value='{size}';s.dispatchEvent(new Event('change',{{bubbles:true}}));}})()")
                        await until("document.querySelectorAll('#block-grid [data-block]').length", lambda n: n == size*size, seconds=90)
                        note = await js("document.getElementById('block-summary').textContent")
                        if "标注细网格" not in note or "模型仍使用 4³" not in note:
                            raise RuntimeError("FINE_GRID_MISLABELED")
                    await js("(()=>{const s=document.getElementById('grid-resolution');s.value='4';s.dispatchEvent(new Event('change',{bubbles:true}));})()")
                    await until("document.querySelectorAll('#block-grid [data-block]').length", lambda n: n == 16, seconds=10)
                highlighted = await js("document.querySelectorAll('#report mark.report-keyword').length")
                if highlighted < 1:
                    raise RuntimeError("BULLA_KEYWORD_NOT_HIGHLIGHTED")
                if os.environ.get("ANNOTATOR_REVISION_QA") == "1":
                    await js("window.prompt=()=> 'QA preview only';document.getElementById('revise-case').click()")
                    await until("document.getElementById('submit').textContent", lambda x: x == '提交修订', seconds=5)
                    if await js("document.querySelectorAll('input[name=state]:not(:disabled)').length") != 3:
                        raise RuntimeError("REVISION_STATES_NOT_EDITABLE")
                    await js("document.querySelector('#block-grid button.active').click()")
                    manual = await js("document.getElementById('block-summary').textContent.includes('人工调整')")
                    if not manual:
                        raise RuntimeError("BLOCK_EDIT_NOT_APPLIED")
                    choices = await js("document.querySelectorAll('#bulla-lobe-choices input').length")
                    if choices != 5:
                        raise RuntimeError("BULLA_LOBE_CHOICES_MISSING")
                    await js("document.querySelector('#bulla-lobe-choices input').checked=true;document.getElementById('bulla-confirm').click()")
                    confirmed = await js("document.getElementById('bulla-confirmed').textContent.includes('已确认')")
                    if not confirmed:
                        raise RuntimeError("BULLA_LOBE_NOT_CONFIRMED_IN_UI")
                if os.environ.get("ANNOTATOR_3D_RETRY_QA") == "1":
                    await js("localStorage.setItem('jepa_viewer_preference','cpu');location.reload()")
                    await until("document.getElementById('render-mode')?.textContent", lambda x: x == 'CPU 阅片', seconds=180)
                    await until("document.getElementById('status')?.textContent", lambda x: x and x.startswith('只读回顾'), seconds=180)
                    await js("document.querySelector('[data-view=render]').click()")
                    await until("document.getElementById('render-mode')?.textContent", lambda x: x == 'WebGL 3D 阅片', seconds=180)
                    if await js("localStorage.getItem('jepa_viewer_preference')") != 'webgl':
                        raise RuntimeError("THREE_DIMENSIONAL_RETRY_NOT_PERSISTED")
                if os.environ.get("ANNOTATOR_3D_QA") == "1":
                    await js("document.querySelector('[data-view=render]').click()")
                    await until("document.getElementById('render-mode').textContent", lambda x: x == 'WebGL 3D 阅片', seconds=20)
                    await until("Number(document.getElementById('block-grid').dataset.meshTriangles)", lambda x: x and x > 0, seconds=30)
                    await until("document.getElementById('mask-toggle').dataset.loaded", lambda x: x == 'yes', seconds=120)
                    await asyncio.sleep(.5)
                    if await colored_canvas_pixels() < 500:
                        raise RuntimeError("THREE_DIMENSIONAL_COLOR_RENDER_EMPTY")
                    if os.environ.get("ANNOTATOR_3D_GESTURE_QA") == "1":
                        center = await js("(()=>{const r=document.getElementById('gl').getBoundingClientRect();return{x:r.x+r.width*.45,y:r.y+r.height*.5}})()")
                        previous_mask = await js("document.getElementById('mask-toggle').checked")
                        await call("Input.dispatchMouseEvent", {"type":"mousePressed","x":center["x"],"y":center["y"],"button":"right","clickCount":1})
                        await call("Input.dispatchMouseEvent", {"type":"mouseMoved","x":center["x"],"y":center["y"]-60,"button":"right","buttons":2})
                        await call("Input.dispatchMouseEvent", {"type":"mouseReleased","x":center["x"],"y":center["y"]-60,"button":"right","clickCount":1})
                        zoom = await js("document.getElementById('zoom-value').textContent")
                        current_mask = await js("document.getElementById('mask-toggle').checked")
                        if zoom == '100%' or current_mask != previous_mask:
                            raise RuntimeError("THREE_DIMENSIONAL_RIGHT_DRAG_ZOOM_FAILED")
                        before = await call("Page.captureScreenshot", {"format":"png","captureBeyondViewport":False})
                        await call("Input.dispatchMouseEvent", {"type":"mousePressed","x":center["x"],"y":center["y"],"button":"left","clickCount":1})
                        await call("Input.dispatchMouseEvent", {"type":"mouseMoved","x":center["x"]+80,"y":center["y"]+30,"button":"left","buttons":1})
                        await call("Input.dispatchMouseEvent", {"type":"mouseReleased","x":center["x"]+80,"y":center["y"]+30,"button":"left","clickCount":1})
                        await asyncio.sleep(.5)
                        after = await call("Page.captureScreenshot", {"format":"png","captureBeyondViewport":False})
                        if hashlib.sha256(before["data"].encode()).digest() == hashlib.sha256(after["data"].encode()).digest():
                            raise RuntimeError("THREE_DIMENSIONAL_LEFT_ROTATION_FAILED")
                if os.environ.get("ANNOTATOR_DUAL_PAN_QA") == "1":
                    for view_name in (["render", "axial"] if os.environ.get("ANNOTATOR_3D_QA") == "1" else ["axial"]):
                        if view_name == "axial":
                            await js("document.querySelector('[data-view=axial]').click()")
                            await until("document.getElementById('gl').dataset.viewMode", lambda x: x == "axial", seconds=10)
                        location = await js("(()=>{const r=document.getElementById('gl').getBoundingClientRect();return{x:r.x+r.width*.45,y:r.y+r.height*.5}})()")
                        old = await js("document.getElementById('gl').dataset.panOffset||''")
                        old_mask = await js("document.getElementById('mask-toggle').checked")
                        await call("Input.dispatchMouseEvent", {"type":"mousePressed","x":location["x"],"y":location["y"],"button":"left","clickCount":1})
                        await call("Input.dispatchMouseEvent", {"type":"mousePressed","x":location["x"],"y":location["y"],"button":"right","clickCount":1})
                        await call("Input.dispatchMouseEvent", {"type":"mouseMoved","x":location["x"]+65,"y":location["y"]+35,"button":"left","buttons":3})
                        await call("Input.dispatchMouseEvent", {"type":"mouseReleased","x":location["x"]+65,"y":location["y"]+35,"button":"right","clickCount":1})
                        await call("Input.dispatchMouseEvent", {"type":"mouseReleased","x":location["x"]+65,"y":location["y"]+35,"button":"left","clickCount":1})
                        new = await js("document.getElementById('gl').dataset.panOffset||''")
                        new_mask = await js("document.getElementById('mask-toggle').checked")
                        if not new or new == old or new_mask != old_mask:
                            raise RuntimeError("DUAL_BUTTON_PAN_FAILED:" + view_name)
                print(json.dumps({"readonly_historical_case":"PASS","save_and_submit_disabled":"PASS",
                                  "graphics_backend":await js("document.getElementById('gpu-backend').textContent"),
                                  "prior_lesions":lesions,"automatic_jepa_blocks":"PASS","bulla_highlight":"PASS",
                                  "fine_annotation_subgrids":"PASS" if os.environ.get("ANNOTATOR_FINE_GRID_QA") == "1" else "NOT_TESTED",
                                  "radiological_left_right":"PASS" if os.environ.get("ANNOTATOR_ORIENTATION_QA") == "1" else "NOT_TESTED",
                                  "block_revision_ui":"PASS" if os.environ.get("ANNOTATOR_REVISION_QA") == "1" else "NOT_TESTED",
                                  "cpu_to_3d_recovery":"PASS" if os.environ.get("ANNOTATOR_3D_RETRY_QA") == "1" else "NOT_TESTED",
                                  "review_progress":"PASS" if os.environ.get("ANNOTATOR_PROGRESS_QA") == "1" else "NOT_TESTED",
                                  "report_left_of_ct":"PASS" if os.environ.get("ANNOTATOR_LAYOUT_QA") == "1" else "NOT_TESTED",
                                  "review_panel_navigation":"PASS" if os.environ.get("ANNOTATOR_REVIEW_PANEL_QA") == "1" else "NOT_TESTED",
                                  "three_dimensional_lobes_ct_blocks":"PASS" if os.environ.get("ANNOTATOR_3D_QA") == "1" else "NOT_TESTED",
                                  "three_dimensional_mouse_rotation_zoom":"PASS" if os.environ.get("ANNOTATOR_3D_GESTURE_QA") == "1" else "NOT_TESTED",
                                  "dual_button_pan":"PASS" if os.environ.get("ANNOTATOR_DUAL_PAN_QA") == "1" else "NOT_TESTED",
                                  "annotation_submitted":False}))
                return
            await until("document.getElementById('status').textContent", lambda x: x and x.startswith("已加载"), seconds=180)
            if os.environ.get("ANNOTATOR_SLICE_RAIL_QA") == "1":
                rail = await js("(()=>{const s=document.getElementById('slice-slider');return{visible:!document.getElementById('slice-rail').hidden,max:Number(s.max),value:Number(s.value),label:document.getElementById('slice-position').textContent}})()")
                if not rail["visible"] or rail["max"] < 10 or "层" not in rail["label"]:
                    raise RuntimeError("SLICE_RAIL_MISSING")
                target = min(rail["max"], rail["value"] + 2)
                await js(f"(()=>{{const s=document.getElementById('slice-slider');s.value='{target}';s.dispatchEvent(new Event('input',{{bubbles:true}}));}})()")
                if await js("Number(document.getElementById('slice-slider').value)") != target:
                    raise RuntimeError("SLICE_RAIL_DID_NOT_MOVE")
            if os.environ.get("ANNOTATOR_REQUIRE_STATE_QA") == "1":
                guard = await js("({selected:!!document.querySelector('input[name=state]:checked'),disabled:document.getElementById('submit').disabled,skip:!!document.getElementById('skip')})")
                if guard["skip"] or (not guard["selected"] and not guard["disabled"]):
                    raise RuntimeError("SUBMIT_STATE_GUARD_FAILED")
            if os.environ.get("ANNOTATOR_AXIAL_QA") == "1":
                default_view = await js("document.getElementById('gl').dataset.viewMode")
                if default_view != "axial":
                    raise RuntimeError("INITIAL_VIEW_NOT_AXIAL")
            if os.environ.get("ANNOTATOR_NAV_QA") == "1":
                options = await js("document.getElementById('review-jump').options.length")
                if options < 3:
                    raise RuntimeError("REVIEW_LIST_EMPTY")
                first = await js("document.getElementById('review-jump').value")
                if os.environ.get("ANNOTATOR_ZOOM_QA") == "1":
                    await js("const z=document.getElementById('zoom-slider');z.value='250';z.dispatchEvent(new Event('input'))")
                    await until("document.getElementById('zoom-value').textContent", lambda x: x == '250%', seconds=10)
                if os.environ.get("ANNOTATOR_AXIAL_QA") == "1":
                    await js("document.querySelector('[data-view=multi]').click()")
                    await until("document.getElementById('gl').dataset.viewMode", lambda x: x == "multi", seconds=10)
                await js("document.getElementById('next-case').click()")
                await until("document.getElementById('review-jump').value", lambda x: x and x != first, seconds=180)
                await until("document.getElementById('status').textContent", lambda x: x and x.startswith("已加载"), seconds=180)
                if os.environ.get("ANNOTATOR_ZOOM_QA") == "1":
                    zoom = await js("Number((document.getElementById('render-mode').textContent==='CPU 阅片'?document.getElementById('gl'):document.getElementById('markup')).dataset.zoom)")
                    if abs(zoom - 2.5) > 0.01 or await js("document.getElementById('zoom-value').textContent") != '250%':
                        raise RuntimeError("NEXT_CASE_ZOOM_NOT_PRESERVED")
                if os.environ.get("ANNOTATOR_AXIAL_QA") == "1" and await js("document.getElementById('gl').dataset.viewMode") != "axial":
                    raise RuntimeError("NEXT_CASE_VIEW_NOT_AXIAL")
                await js("document.getElementById('previous-case').click()")
                await until("document.getElementById('review-jump').value", lambda x: x == first, seconds=180)
                await until("document.getElementById('status').textContent", lambda x: x and x.startswith("已加载"), seconds=180)
                await js("document.getElementById('patient-next').click()")
                await until("document.getElementById('review-jump').value", lambda x: x and x != first, seconds=180)
                await until("document.getElementById('status').textContent", lambda x: x and x.startswith("已加载"), seconds=180)
                await js("document.getElementById('patient-prev').click()")
                await until("document.getElementById('review-jump').value", lambda x: x == first, seconds=180)
                await until("document.getElementById('status').textContent", lambda x: x and x.startswith("已加载"), seconds=180)
                if os.environ.get("ANNOTATOR_RESUME_QA") == "1":
                    await js("location.reload()")
                    await until("document.getElementById('review-jump')?.value", lambda x: x == first, seconds=180)
                    await until("document.getElementById('status')?.textContent", lambda x: x and x.startswith("已加载"), seconds=180)
                    if os.environ.get("ANNOTATOR_ZOOM_QA") == "1" and await js("document.getElementById('zoom-value').textContent") != '250%':
                        raise RuntimeError("RELOADED_CASE_ZOOM_NOT_PRESERVED")
            mode = await js("document.getElementById('render-mode').textContent")
            backend = await js("document.getElementById('gpu-backend').textContent")
            if gpu and mode != "WebGL 阅片":
                raise RuntimeError("WEBGL_VIEWER_NOT_ACTIVE")
            if os.environ.get("ANNOTATOR_SLIDER_QA") == "1":
                await js("(()=>{const s=document.getElementById('zoom-slider');s.value='200';s.dispatchEvent(new Event('input',{bubbles:true}));})()")
                scale = await until("Number(document.getElementById('" + ("markup" if gpu else "gl") + "').dataset.zoom)", lambda x: x and abs(x-2)<0.02, seconds=10)
                label = await js("document.getElementById('zoom-value').textContent")
                if label != "200%":
                    raise RuntimeError("ZOOM_SLIDER_LABEL_MISMATCH")
                await js("(()=>{const s=document.getElementById('zoom-slider');s.value='100';s.dispatchEvent(new Event('input',{bubbles:true}));})()")
            count = await js("document.getElementById('report').textContent.length")
            if count < 5:
                raise RuntimeError("REPORT_PANEL_EMPTY")
            if os.environ.get("ANNOTATOR_HIGHLIGHT_QA") == "1":
                report_check = await js("(async()=>{const id=document.getElementById('review-jump').value;const r=await(await fetch('/api/tasks/'+id+'/report')).json();return {status:r.status,actual:document.getElementById('report').textContent.length,source:(r.text||'').length,equal:document.getElementById('report').textContent===r.text}})()")
                if not report_check or not report_check["equal"]:
                    raise RuntimeError("ORIGINAL_REPORT_TEXT_CHANGED:" + json.dumps(report_check))
            surface = "markup" if gpu else "gl"
            canvas = await js(f"[document.getElementById('{surface}').width,document.getElementById('{surface}').height]")
            if min(canvas) < 100:
                raise RuntimeError("CT_CANVAS_EMPTY")
            spot = await js(f"(()=>{{const r=document.getElementById('{surface}').getBoundingClientRect();return{{x:r.x+r.width/2,y:r.y+r.height/2}}}})()")
            if gpu:
                spot = await js("(()=>{const c=document.getElementById('markup'),r=c.getBoundingClientRect(),p=JSON.parse(c.dataset.tileCenter);return{x:r.x+p[0]*r.width/c.width,y:r.y+p[1]*r.height/c.height}})()")
            await call("Input.dispatchMouseEvent", {"type":"mouseMoved","x":spot["x"],"y":spot["y"]})
            if gpu and os.environ.get("ANNOTATOR_ZOOM_QA") == "1":
                old_zoom = await js("Number(document.getElementById('markup').dataset.zoom)")
                await call("Input.dispatchMouseEvent", {"type":"mouseWheel","x":spot["x"],"y":spot["y"],"deltaX":0,"deltaY":-80,"modifiers":2})
                new_zoom = await until("Number(document.getElementById('markup').dataset.zoom)", lambda x: x and x > old_zoom, seconds=10)
            await until("document.getElementById('hover-coords').textContent", lambda x: x and not x.endswith("—"), seconds=10)
            await call("Input.dispatchMouseEvent", {"type":"mousePressed","x":spot["x"],"y":spot["y"],"button":"left","clickCount":1})
            await call("Input.dispatchMouseEvent", {"type":"mouseReleased","x":spot["x"],"y":spot["y"],"button":"left","clickCount":1})
            await until("document.getElementById('coords').textContent", lambda x: x and not x.endswith("—"), seconds=10)
            if os.environ.get("ANNOTATOR_CROSSHAIR_QA") == "1":
                visible = await js("document.querySelector('.canvas-stack').dataset.crosshairVisible" if gpu else "document.getElementById('gl').dataset.crosshairVisible")
                if visible != "yes":
                    raise RuntimeError("CROSSHAIR_NOT_SHOWN_ON_CLICK")
                await asyncio.sleep(1.4)
                hidden = await js("document.querySelector('.canvas-stack').dataset.crosshairVisible" if gpu else "document.getElementById('gl').dataset.crosshairVisible")
                if hidden != "no":
                    raise RuntimeError("CROSSHAIR_NOT_HIDDEN_AFTER_CLICK")
            await js("document.getElementById('map-point').click()")
            await until("document.getElementById('grid-info').textContent", lambda x: x and "目标块" in x, seconds=60)
            if visual:
                # Suppress autosave in this disposable QA browser. Neither a
                # draft nor a clinician label is written to the live database.
                await js("window.setTimeout=((original)=>function(fn,ms,...args){return ms===2500?0:original(fn,ms,...args)})(window.setTimeout)")
                if os.environ.get("ANNOTATOR_AUTO_POSITIVE_QA") == "1":
                    await js("document.querySelector('input[name=state][value=negative]').click()")
                count_before = await js("document.querySelectorAll('#lesions .lesion').length")
                await js("document.querySelector('[data-drawing=point]').click()")
                await call("Input.dispatchMouseEvent", {"type":"mousePressed","x":spot["x"],"y":spot["y"],"button":"left","clickCount":1})
                await call("Input.dispatchMouseEvent", {"type":"mouseReleased","x":spot["x"],"y":spot["y"],"button":"left","clickCount":1})
                await until("document.querySelectorAll('#lesions .lesion').length", lambda x: x == count_before + 1, seconds=10)
                if os.environ.get("ANNOTATOR_AUTO_POSITIVE_QA") == "1":
                    positive = await js("document.querySelector('input[name=state][value=positive]').checked && document.getElementById('read-complete').checked && document.getElementById('read-complete-control').hidden && !document.getElementById('submit').disabled")
                    if not positive:
                        raise RuntimeError("DRAWN_MARK_DID_NOT_SET_POSITIVE_AND_COMPLETE")
                if os.environ.get("ANNOTATOR_NEGATIVE_QA") == "1":
                    await js("document.querySelector('input[name=state][value=negative]').click()")
                    negative_requires_click = await js("!document.getElementById('read-complete').checked && !document.getElementById('read-complete-control').hidden && document.getElementById('submit').disabled")
                    if not negative_requires_click:
                        raise RuntimeError("NEGATIVE_DID_NOT_REQUIRE_EXPLICIT_COMPLETE_READ")
                    await js("document.querySelector('input[name=state][value=positive]').click()")
                if os.environ.get("ANNOTATOR_REPORT_COMPARE_QA") == "1":
                    radio_values = await js("[...document.querySelectorAll('input[name=report-agreement]')].map(x=>x.value)")
                    if radio_values != ["agree", "disagree"]:
                        raise RuntimeError("REPORT_AGREEMENT_NOT_BINARY")
                    await until("document.querySelector('input[name=report-agreement]:checked')?.value", lambda x: x in {"agree", "disagree", "uncertain"}, seconds=10)
                    source = await js("document.getElementById('report-agreement-hint').textContent")
                    if "自动提示" not in source:
                        raise RuntimeError("REPORT_AUTO_COMPARISON_MISSING")
                    await js("document.querySelector(`input[name=report-agreement][value=${document.querySelector('input[name=report-agreement]:checked').value==='agree'?'disagree':'agree'}]`).click()")
                    manual = await js("document.getElementById('report-agreement-hint').textContent")
                    if "医生已手动修改" not in manual:
                        raise RuntimeError("REPORT_MANUAL_OVERRIDE_MISSING")
                green = await js(f"(()=>{{const c=document.getElementById('{surface}');const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let n=0;for(let i=0;i<d.length;i+=4)if(d[i]<120&&d[i+1]>220&&d[i+2]>80&&d[i+2]<180)n++;return n}})()")
                if green < 10:
                    debug = await js("(()=>{const d=document.getElementById('markup').dataset;return{drawn:d.markerCount,slices:d.sliceCount,annotations:d.annotationCount,fracFinite:d.fracFinite,fracInside:d.fracInside,direct:d.directProjected}})()") if gpu else None
                    raise RuntimeError(f"POINT_MARKER_NOT_VISIBLE:debug={debug}:pixels={green}")
                await js("document.querySelector('[data-drawing=box]').click()")
                dest = {"x": spot["x"]+40, "y": spot["y"]+30}
                await call("Input.dispatchMouseEvent", {"type":"mousePressed","x":spot["x"],"y":spot["y"],"button":"left","clickCount":1})
                await call("Input.dispatchMouseEvent", {"type":"mouseMoved","x":dest["x"],"y":dest["y"],"button":"left","buttons":1})
                await call("Input.dispatchMouseEvent", {"type":"mouseReleased","x":dest["x"],"y":dest["y"],"button":"left","clickCount":1})
                await until("document.querySelectorAll('#lesions .lesion').length", lambda x: x == count_before + 2, seconds=10)
                cyan = await js(f"(()=>{{const c=document.getElementById('{surface}');const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let n=0;for(let i=0;i<d.length;i+=4)if(d[i]<100&&d[i+1]>190&&d[i+2]>200)n++;return n}})()")
                if cyan < 10:
                    raise RuntimeError("BOX_MARKER_NOT_VISIBLE")
                if os.environ.get("ANNOTATOR_LOBE_QA") == "1":
                    lobe_state = await until("document.getElementById('lobe-info').textContent", lambda x: x and ("伪标签已加载" in x or "鼠标位置：" in x or "失败" in x or "没有可用" in x), seconds=45)
                    if "伪标签已加载" not in lobe_state and "鼠标位置：" not in lobe_state:
                        raise RuntimeError("LOBE_MASK_LOAD_FAILED:" + lobe_state)
                    before_colors = await colored_canvas_pixels() if gpu and os.environ.get("ANNOTATOR_COLOR_QA") == "1" else None
                    await js("document.getElementById('mask-toggle').click()")
                    await until("document.getElementById('mask-toggle').dataset.loaded", lambda x: x == "yes", seconds=120)
                    if os.environ.get("ANNOTATOR_RIGHT_CLICK_QA") == "1":
                        await call("Input.dispatchMouseEvent", {"type":"mousePressed","x":spot["x"],"y":spot["y"],"button":"right","clickCount":1})
                        await call("Input.dispatchMouseEvent", {"type":"mouseReleased","x":spot["x"],"y":spot["y"],"button":"right","clickCount":1})
                        await until("document.getElementById('mask-toggle').checked", lambda x: x is False, seconds=10)
                        await call("Input.dispatchMouseEvent", {"type":"mousePressed","x":spot["x"],"y":spot["y"],"button":"right","clickCount":1})
                        await call("Input.dispatchMouseEvent", {"type":"mouseReleased","x":spot["x"],"y":spot["y"],"button":"right","clickCount":1})
                        await until("document.getElementById('mask-toggle').checked", lambda x: x is True, seconds=10)
                    result = await js("document.querySelector('#lesions .pseudo-lobe')?.textContent")
                    if not result or "nnUNet肺叶提示" not in result:
                        raise RuntimeError("LOBE_HINT_NOT_VISIBLE")
                    if before_colors is not None:
                        await asyncio.sleep(1)
                        after_colors = await colored_canvas_pixels()
                        if after_colors-before_colors < 500:
                            raise RuntimeError(f"LOBE_COLOR_OVERLAY_NOT_VISIBLE:before={before_colors}:after={after_colors}")
            print(json.dumps({"browser_real_ct":"PASS","raw_report_panel":"PASS","render_mode":mode,
                              "graphics_backend":backend,
                              "mouse_ras_coordinate":"PASS","jepa_preview":"PASS",
                              "persistent_point_and_box":"PASS" if visual else "NOT_TESTED",
                              "drawn_mark_sets_positive":"PASS" if os.environ.get("ANNOTATOR_AUTO_POSITIVE_QA") == "1" else "NOT_TESTED",
                              "negative_requires_explicit_confirmation":"PASS" if os.environ.get("ANNOTATOR_NEGATIVE_QA") == "1" else "NOT_TESTED",
                              "transient_click_crosshair":"PASS" if os.environ.get("ANNOTATOR_CROSSHAIR_QA") == "1" else "NOT_TESTED",
                              "report_auto_comparison_and_override":"PASS" if os.environ.get("ANNOTATOR_REPORT_COMPARE_QA") == "1" else "NOT_TESTED",
                              "right_click_lobe_toggle":"PASS" if os.environ.get("ANNOTATOR_RIGHT_CLICK_QA") == "1" else "NOT_TESTED",
                              "lobe_mask_loaded":"PASS" if os.environ.get("ANNOTATOR_LOBE_QA") == "1" else "NOT_TESTED",
                              "lobe_color_overlay":"PASS" if os.environ.get("ANNOTATOR_COLOR_QA") == "1" else "NOT_TESTED",
                              "previous_next":"PASS" if os.environ.get("ANNOTATOR_NAV_QA") == "1" else "NOT_TESTED",
                              "zoom_inherited_between_patients":"PASS" if os.environ.get("ANNOTATOR_NAV_QA") == "1" and os.environ.get("ANNOTATOR_ZOOM_QA") == "1" else "NOT_TESTED",
                              "submit_requires_state":"PASS" if os.environ.get("ANNOTATOR_REQUIRE_STATE_QA") == "1" else "NOT_TESTED",
                              "axial_default":"PASS" if os.environ.get("ANNOTATOR_AXIAL_QA") == "1" else "NOT_TESTED",
                              "login_resume":"PASS" if os.environ.get("ANNOTATOR_RESUME_QA") == "1" else "NOT_TESTED",
                              "trackpad_zoom":"PASS" if gpu and os.environ.get("ANNOTATOR_ZOOM_QA") == "1" else "NOT_TESTED",
                              "zoom_slider":"PASS" if os.environ.get("ANNOTATOR_SLIDER_QA") == "1" else "NOT_TESTED",
                              "report_verbatim":"PASS" if os.environ.get("ANNOTATOR_HIGHLIGHT_QA") == "1" else "NOT_TESTED",
                              "annotation_submitted":False,"browser_profile_persisted":False}))
    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=5)
        except subprocess.TimeoutExpired:
            chrome.kill()
            chrome.wait(timeout=5)
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
