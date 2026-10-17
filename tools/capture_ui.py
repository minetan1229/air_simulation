import asyncio
import os
from playwright.async_api import async_playwright

async def capture_all():
    out_dir = os.path.abspath("docs/images")
    os.makedirs(out_dir, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--enable-webgl",
                "--ignore-gpu-blocklist",
                "--use-gl=angle",
                "--use-angle=gl",
            ]
        )
        context = await browser.new_context(
            viewport={"width": 1600, "height": 980},
            device_scale_factor=1.25,
        )
        page = await context.new_page()

        print("1. Opening app...")
        await page.goto("http://localhost:5000", wait_until="networkidle")
        await asyncio.sleep(2.5)

        # 1. Main console overview
        path_main = os.path.join(out_dir, "01_main_console.png")
        await page.screenshot(path=path_main)
        print("Saved 01_main_console.png")

        # 2. Launch flight (click #btn-top-launch)
        print("2. Launching single flight...")
        launch_btn = page.locator("#btn-top-launch")
        if await launch_btn.count() > 0:
            await launch_btn.click()
            print("Waiting for launch request...")
            # Wait until flight finish notification or recorder status updates
            await page.wait_for_selector(".toast:has-text('発射完了'), .flight-finished", timeout=25000)
            await asyncio.sleep(2.0)
            path_traj = os.path.join(out_dir, "02_flight_trajectory.png")
            await page.screenshot(path=path_traj)
            print("Saved 02_flight_trajectory.png")

        # 3. Wind tunnel toggle
        print("3. Toggling wind tunnel...")
        tunnel_label = page.locator(".toprail-switch, label:has(#tunnel-toggle)").first
        if await tunnel_label.count() > 0:
            await tunnel_label.click()
            await asyncio.sleep(2.5)
            path_tunnel = os.path.join(out_dir, "03_wind_tunnel_mode.png")
            await page.screenshot(path=path_tunnel)
            print("Saved 03_wind_tunnel_mode.png")
            await tunnel_label.click()
            await asyncio.sleep(1.0)

        # 4. Workbench: Auto Investigate, Optimizer, 3D Mesh
        print("4. Opening Workbench...")
        wb_toggle = page.locator("#wb-toggle")
        if await wb_toggle.count() > 0:
            await wb_toggle.click()
            await asyncio.sleep(1.2)
            path_wb_inv = os.path.join(out_dir, "04_workbench_investigate.png")
            await page.screenshot(path=path_wb_inv)
            print("Saved 04_workbench_investigate.png")

            # Switch to Optimizer tab
            opt_tab = page.locator("#wb-tab-optimizer")
            if await opt_tab.count() > 0:
                await opt_tab.click()
                await asyncio.sleep(1.2)
                path_wb_opt = os.path.join(out_dir, "05_workbench_optimizer.png")
                await page.screenshot(path=path_wb_opt)
                print("Saved 05_workbench_optimizer.png")

            # Switch to 3D Model tab
            mesh_tab = page.locator("#wb-tab-model")
            if await mesh_tab.count() > 0:
                await mesh_tab.click()
                await asyncio.sleep(1.2)
                path_wb_mesh = os.path.join(out_dir, "06_workbench_3d_mesh.png")
                await page.screenshot(path=path_wb_mesh)
                print("Saved 06_workbench_3d_mesh.png")

        # 5. 100-plane ensemble mode screenshot
        print("5. 100-plane ensemble simulation...")
        if await wb_toggle.count() > 0 and (await page.locator("#workbench").get_attribute("data-open")) == "1":
            await wb_toggle.click()
            await asyncio.sleep(0.5)

        ensemble_btn = page.locator("#btn-top-gpu-ensemble")
        if await ensemble_btn.count() > 0:
            await ensemble_btn.click()
            print("Waiting for 100-plane ensemble calculation and animation...")
            await page.wait_for_selector(".toast:has-text('完了'), #eta-widget:has-text('GPU完了')", timeout=30000)
            await asyncio.sleep(3.0)
            path_ensemble = os.path.join(out_dir, "07_ensemble_100_planes.png")
            await page.screenshot(path=path_ensemble)
            print("Saved 07_ensemble_100_planes.png")

        await browser.close()
        print("All screenshots successfully captured!")

if __name__ == "__main__":
    asyncio.run(capture_all())
