"""Exercise the repaired UI in real headless Chrome and persist evidence.

Browser runs shorten only simulation request durations to half a second. This
checks modes, rendering and playback, not full-flight aerodynamic performance.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import app
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server


def main() -> int:
    output = ROOT / "artifacts" / "physics_repair"
    output.mkdir(parents=True, exist_ok=True)
    report = {"scope": "Real browser UI/GPU rendering; simulation requests capped to 0.5 s, not performance validation",
              "checks": [], "console_errors": [], "page_errors": [], "failed_requests": [], "shortened_requests": []}
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def check(name, passed, evidence):
        report["checks"].append({"name": name, "passed": bool(passed), "evidence": evidence})
        (output / "ui_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path="C:/Program Files/Google/Chrome/Application/chrome.exe",
                headless=True, args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1600, "height": 1050}, device_scale_factor=1)
            page.on("console", lambda message: report["console_errors"].append(message.text) if message.type == "error" else None)
            page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
            page.on("requestfailed", lambda request: report["failed_requests"].append({
                "url": request.url, "error": request.failure,
                "classification": "local" if request.url.startswith(base) else "external"}))

            def cap_duration(route):
                request = route.request
                payload = request.post_data_json
                original = payload.get("duration_s")
                payload["duration_s"] = 0.5
                report["shortened_requests"].append({"url": request.url, "original_duration_s": original, "test_duration_s": 0.5})
                route.continue_(post_data=json.dumps(payload), headers={**request.headers, "content-type": "application/json"})

            page.route("**/api/v1/simulation/launch", cap_duration)
            page.route("**/api/v1/guinness/simulate", cap_duration)
            page.goto(base, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_function("window.consoleApp?.viewport?.renderer && window.consoleApp?.recordPresetsMap?.size > 0", timeout=60000)
            report["gpu"] = page.evaluate("""() => {
                const v=consoleApp.viewport, gl=v.renderer.getContext();
                const extension=gl.getExtension('WEBGL_debug_renderer_info');
                return {gpuInfo:v.gpuInfo, webglVersion:gl.getParameter(gl.VERSION),
                    vendor:extension?gl.getParameter(extension.UNMASKED_VENDOR_WEBGL):gl.getParameter(gl.VENDOR),
                    renderer:extension?gl.getParameter(extension.UNMASKED_RENDERER_WEBGL):gl.getParameter(gl.RENDERER),
                    particles:v.particles?.activeCount, renderCalls:v.renderer.info.render.calls,
                    cudaVerified:false, note:'Browser graphics backend only; no CUDA inference'};
            }""")
            check("three_rendered", report["gpu"]["renderCalls"] > 0, report["gpu"])

            for mode, expected in (("full_flight", [2.0, 26.0, 80.0]), ("apex_glide", [17.3, 3.3, -2.8])):
                normal = page.evaluate("""async mode => {
                    const a=consoleApp;
                    document.getElementById('record-preset-select').value='unite_31s';
                    await a._showRecordDetails('unite_31s');
                    a.recordFlightMode=mode; a._updateRecordSpecsDisplay();
                    await a._applyRecordPreset(false); a.transport.pause();
                    const l=a.store.data.launch;
                    return {mode:a.recordFlightMode, launch:[l.launch_height_m,l.launch_speed,l.launch_angle_deg],
                        specs:['rec-height','rec-speed','rec-angle'].map(id=>document.getElementById(id).textContent),
                        samples:a.transport.history.length, first:a.transport.history[0]};
                }""", mode)
                check(f"normal_{mode}", normal["launch"] == expected and normal["mode"] == mode, normal)
                studio = page.evaluate("""async mode => {
                    const a=consoleApp; await a._loadGuinnessStudioPreset('unite_31s');
                    a.studioFlightMode=mode; a._updateGuinnessStudioSpecs();
                    await a._runGuinnessStudioFlight(); a.transport.pause();
                    return {mode:a.studioFlightMode, specs:['wb-g-speed','wb-g-angle'].map(id=>document.getElementById(id).textContent),
                        samples:a.transport.history.length, first:a.transport.history[0],
                        title:document.getElementById('wb-g-res-title').textContent};
                }""", mode)
                first = studio["first"]
                check(f"studio_{mode}", studio["mode"] == mode and abs(first["altitude_m"] - expected[0]) < 1e-6,
                      studio)
                check(f"normal_studio_initial_state_{mode}", all(abs(normal["first"][key] - first[key]) < 1e-6
                    for key in ("x_m", "y_m", "z_m", "vx_mps", "vy_mps", "vz_mps")),
                    {"normal": normal["first"], "studio": first})

            controls = page.evaluate("""async () => {
                const a=consoleApp,t=a.transport; t.pause(); t.seek(0);
                const start=t.playIndex; t.stepForward(); const forward=t.playIndex;
                t.stepBack(); const back=t.playIndex;
                t.seek(t.history.length-1); const last=t.playIndex;
                t.seek(0); t.play(); const playing=t.playing; t.pause();
                t.seek(t.history.length-1); a.flightDirty=false;
                await a._togglePlay(); const replayFromEnd=t.playing&&t.playIndex===0; t.pause();
                const v=a.viewport;
                return {start,forward,back,last,frames:t.history.length,playing,replayFromEnd,paused:!t.playing,
                    cameraFinite:[...v.camera.position.toArray(),...v.controls.target.toArray()].every(Number.isFinite),
                    trackFinite:v.trackPoints.every(p=>p.slice(0,3).every(Number.isFinite))};
            }""")
            check("play_pause_step_seek", controls["forward"] == 1 and controls["back"] == 0 and controls["last"] == controls["frames"]-1
                  and controls["playing"] and controls["replayFromEnd"] and controls["paused"] and controls["cameraFinite"] and controls["trackFinite"], controls)

            frame = page.evaluate("""() => {
                const a=consoleApp,v=a.viewport; a.transport.pause(); v.follow=false;
                v.frameTrajectory(a.transport.history); v.camera.updateMatrixWorld(true);
                const points=a.transport.history.map(s=>v.camera.position.clone().set(s.x_m,-s.z_m,s.y_m).project(v.camera));
                const values=points.flatMap(p=>[p.x,p.y,p.z]);
                return {samples:points.length,finite:values.every(Number.isFinite),
                    maxX:Math.max(...points.map(p=>Math.abs(p.x))),maxY:Math.max(...points.map(p=>Math.abs(p.y))),
                    minZ:Math.min(...points.map(p=>p.z)),maxZ:Math.max(...points.map(p=>p.z))};
            }""")
            check("frameTrajectory_projects_inside_viewport", frame["finite"] and frame["maxX"] <= 1 and frame["maxY"] <= 1
                  and frame["minZ"] >= -1 and frame["maxZ"] <= 1, frame)
            page.screenshot(path=str(output / "ui_apex_framed.png"), full_page=True)

            for mode in ("full_flight", "apex_glide"):
                saved = page.evaluate("""async mode => {
                    const post=async(path,data)=>{const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});return await r.json();};
                    await post('/api/v1/records/apply',{key:'unite_31s',flight_mode:mode});
                    const exp=await post('/api/v1/data/export',{kind:'config',format:'json'});
                    const bundle=JSON.parse(exp.payload);
                    await post('/api/v1/records/apply',{key:'ayoob_69_1m',flight_mode:'full_flight'});
                    const restored=await post('/api/v1/data/import',{bundle});
                    return {saved:bundle.launch.flight_mode,restored:restored.bundle.launch.flight_mode};
                }""", mode)
                check(f"save_restore_{mode}", saved["saved"] == mode and saved["restored"] == mode, saved)

            disabled = page.evaluate("""async () => {
                const a=consoleApp; a.recordFlightMode='apex_glide';
                await a._showRecordDetails('ayoob_69_1m');
                a.studioFlightMode='apex_glide'; await a._loadGuinnessStudioPreset('ayoob_69_1m');
                return {normalDisabled:document.getElementById('btn-mode-raw').disabled,
                    studioDisabled:document.getElementById('wb-guinness-btn-cal').disabled,
                    normalMode:a.recordFlightMode,studioMode:a.studioFlightMode};
            }""")
            check("distance_apex_disabled", disabled["normalDisabled"] and disabled["studioDisabled"]
                  and disabled["normalMode"] == disabled["studioMode"] == "full_flight", disabled)
            page.screenshot(path=str(output / "ui_distance_mode.png"), full_page=True)
            browser.close()
            software_browser = playwright.chromium.launch(
                executable_path="C:/Program Files/Google/Chrome/Application/chrome.exe",
                headless=True, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
            fallback_page = software_browser.new_page(viewport={"width": 1400, "height": 900})
            fallback_page.goto(base, wait_until="domcontentloaded", timeout=60000)
            fallback_page.wait_for_function("window.consoleApp?.viewport?.renderer?.info.render.calls > 0", timeout=60000)
            software = fallback_page.evaluate("""() => {
                const v=consoleApp.viewport,gl=v.renderer.getContext(),ext=gl.getExtension('WEBGL_debug_renderer_info');
                return {renderer:ext?gl.getParameter(ext.UNMASKED_RENDERER_WEBGL):gl.getParameter(gl.RENDERER),
                    renderCalls:v.renderer.info.render.calls,particles:v.particles.activeCount,cudaVerified:false};
            }""")
            check("software_graphics_fallback", software["renderCalls"] > 0 and "SwiftShader" in software["renderer"], software)
            fallback_page.screenshot(path=str(output / "ui_software_fallback.png"), full_page=True)
            software_browser.close()
    except Exception as error:
        report["fatal_error"] = f"{type(error).__name__}: {error}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        report["passed"] = not report.get("fatal_error") and all(item["passed"] for item in report["checks"]) and not report["page_errors"]
        (output / "ui_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"passed": report["passed"], "checks": len(report["checks"]), "fatal_error": report.get("fatal_error"),
                          "console_errors": len(report["console_errors"]), "failed_requests": len(report["failed_requests"]),
                          "artifact": str(output / "ui_report.json")}, ensure_ascii=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
