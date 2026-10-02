import asyncio
import json
import os
import subprocess
import time
import urllib.request
import websockets

async def run_verification():
    chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    user_data = os.path.join(os.environ.get("TEMP", "C:\\temp"), "chrome_cdp_profile_py")
    proc = subprocess.Popen([
        chrome_path,
        "--headless=new",
        "--remote-debugging-port=9222",
        "--no-first-run",
        "--no-default-browser-check",
        f"--user-data-dir={user_data}",
        "http://localhost:3001"
    ])
    print(f"Spawned Chrome pid: {proc.pid}")
    await asyncio.sleep(3)

    try:
        resp = urllib.request.urlopen("http://127.0.0.1:9222/json/list")
        tabs = json.loads(resp.read().decode())
        page_tab = next(t for t in tabs if t.get("type") == "page" and "localhost:3001" in t.get("url", ""))
        ws_url = page_tab["webSocketDebuggerUrl"]
        print(f"Connecting to page: {ws_url}")

        async with websockets.connect(ws_url) as ws:
            async def call(method, params=None):
                call.id += 1
                msg = {"id": call.id, "method": method, "params": params or {}}
                await ws.send(json.dumps(msg))
                while True:
                    res = json.loads(await ws.recv())
                    if res.get("id") == call.id:
                        return res.get("result", {})
            call.id = 0

            # Wait for hydration
            await asyncio.sleep(2)

            # 1. Verify headers: check for "RS vs Sector" and absence of "% Float Short"
            check_ui = """
            (() => {
                const bodyText = document.body.innerText;
                const thTexts = Array.from(document.querySelectorAll('th')).map(th => th.innerText.trim());
                const rows = Array.from(document.querySelectorAll('tbody tr'));
                const firstRowCells = rows.length > 0 ? Array.from(rows[0].querySelectorAll('td')).map(td => td.innerText.trim()) : [];
                return {
                    title: document.title,
                    hasFloatShort: bodyText.includes('% Float Short'),
                    hasRsVsSectorHeader: thTexts.some(t => t.toUpperCase().includes('RS VS SECTOR')),
                    thTexts: thTexts,
                    firstRowCells: firstRowCells.slice(0, 14),
                    totalRows: rows.length
                };
            })()
            """
            res1 = await call("Runtime.evaluate", {"expression": check_ui, "returnByValue": True})
            ui_data = res1.get("result", {}).get("value", {})
            print("\n--- UI INITIAL VERIFICATION ---")
            print("Page Title:", ui_data.get("title"))
            print("'% Float Short' present anywhere on page:", ui_data.get("hasFloatShort"))
            print("'RS vs Sector' column header present:", ui_data.get("hasRsVsSectorHeader"))
            print("Total rows:", ui_data.get("totalRows"))
            print("First row sample cells:", ui_data.get("firstRowCells"))

            # 2. Click first row to open modal
            click_row = """
            (() => {
                const firstRow = document.querySelector('tbody tr');
                if (firstRow) {
                    firstRow.click();
                    return true;
                }
                return false;
            })()
            """
            await call("Runtime.evaluate", {"expression": click_row})
            await asyncio.sleep(1)

            check_modal = """
            (() => {
                const modal = document.querySelector('.fixed.inset-0');
                if (!modal) return { modalFound: false };
                
                const siBtn = Array.from(modal.querySelectorAll('button')).find(b => b.innerText.includes('Short Interest'));
                if (siBtn) siBtn.click();
                
                const whyBtn = Array.from(modal.querySelectorAll('button')).find(b => b.innerText.includes('Why It Triggered'));
                if (whyBtn) whyBtn.click();

                return {
                    modalFound: true
                };
            })()
            """
            res2 = await call("Runtime.evaluate", {"expression": check_modal, "returnByValue": True})
            await asyncio.sleep(0.5)

            check_modal_panels = """
            (() => {
                const modal = document.querySelector('.fixed.inset-0');
                if (!modal) return { modalFound: false };
                const text = modal.innerText;
                return {
                    modalFound: true,
                    hasFloatShortInModal: text.includes('% Float Short') || text.includes('Float Short'),
                    hasDaysToCover: text.includes('Days to Cover'),
                    hasSharesShort: text.includes('Shares Short'),
                    hasRsVsSectorInChecklist: text.includes('RS vs Sector')
                };
            })()
            """
            res3 = await call("Runtime.evaluate", {"expression": check_modal_panels, "returnByValue": True})
            modal_data = res3.get("result", {}).get("value", {})
            print("\n--- MODAL VERIFICATION ---")
            print("Modal opened:", modal_data.get("modalFound"))
            print("'% Float Short' in Short Interest panel:", modal_data.get("hasFloatShortInModal"))
            print("'Days to Cover' in panel:", modal_data.get("hasDaysToCover"))
            print("'Shares Short' in panel:", modal_data.get("hasSharesShort"))
            print("'RS vs Sector' in Why It Triggered checklist:", modal_data.get("hasRsVsSectorInChecklist"))

            # Close modal
            close_modal = """
            (() => {
                const closeBtn = document.querySelector('button[aria-label=\"Close modal\"]');
                if (closeBtn) closeBtn.click();
            })()
            """
            await call("Runtime.evaluate", {"expression": close_modal})
            await asyncio.sleep(0.5)

            # 3. Test Feature 2: Portfolio Risk View selection (select 3 signals)
            select_signals = """
            (() => {
                const rowCheckboxes = Array.from(document.querySelectorAll('tbody tr td input[type=\"checkbox\"]'));
                if (rowCheckboxes.length >= 3) {
                    rowCheckboxes[0].click();
                    rowCheckboxes[1].click();
                    rowCheckboxes[2].click();
                    return { clicked: 3 };
                }
                return { clicked: rowCheckboxes.length };
            })()
            """
            res4 = await call("Runtime.evaluate", {"expression": select_signals, "returnByValue": True})
            await asyncio.sleep(1)

            check_portfolio = """
            (() => {
                const panel = Array.from(document.querySelectorAll('section')).find(s => s.innerText.includes('Portfolio Risk View'));
                if (!panel) return { panelFound: false };
                return {
                    panelFound: true,
                    panelText: panel.innerText
                };
            })()
            """
            res5 = await call("Runtime.evaluate", {"expression": check_portfolio, "returnByValue": True})
            port_data = res5.get("result", {}).get("value", {})
            print("\n--- PORTFOLIO RISK PANEL VERIFICATION (3 SIGNALS SELECTED) ---")
            print("Portfolio Risk View Panel Visible:", port_data.get("panelFound"))
            print("Portfolio Panel Content:\n", port_data.get("panelText"))

            # 4. Filter test: hide selected signals
            test_filter = """
            (() => {
                const sectorSelect = Array.from(document.querySelectorAll('select')).find(s => Array.from(s.options).some(o => o.value === 'Technology'));
                if (sectorSelect) {
                    sectorSelect.value = 'Technology';
                    sectorSelect.dispatchEvent(new Event('change', { bubbles: true }));
                }
                return true;
            })()
            """
            await call("Runtime.evaluate", {"expression": test_filter})
            await asyncio.sleep(1)

            check_hidden_warning = """
            (() => {
                const panel = Array.from(document.querySelectorAll('section')).find(s => s.innerText.includes('Portfolio Risk View'));
                if (!panel) return { panelFound: false };
                return {
                    panelFound: true,
                    hasHiddenNotice: panel.innerText.includes('hidden by active table filters'),
                    panelText: panel.innerText
                };
            })()
            """
            res6 = await call("Runtime.evaluate", {"expression": check_hidden_warning, "returnByValue": True})
            warning_data = res6.get("result", {}).get("value", {})
            print("\n--- HIDDEN FILTER WARNING VERIFICATION ---")
            print("Hidden warning displayed:", warning_data.get("hasHiddenNotice"))
            print("Panel Text with filter applied:\n", warning_data.get("panelText"))
    finally:
        proc.kill()

asyncio.run(run_verification())
