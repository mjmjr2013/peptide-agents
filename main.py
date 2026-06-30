"""
Orchestrator — runs all agents in a coordinated loop.
Also exposes a minimal Flask webhook server for Twilio inbound messages.
"""
import os
import threading
import time
import sys

from agents.ad_agent import run_ad_agent
from agents.lead_gen_agent import run_lead_gen_agent
from agents.tracking_agent import check_and_notify_all


def run_ad_loop(interval: int = 300):
    while True:
        try:
            run_ad_agent()
        except Exception as e:
            print(f"[Main/AdAgent] {e}")
        time.sleep(interval)


def run_lead_gen_loop(interval: int = 21600):  # every 6 hours
    while True:
        try:
            run_lead_gen_agent()
        except Exception as e:
            print(f"[Main/LeadGen] {e}")
        time.sleep(interval)


def _report_tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(os.environ.get("REPORT_TIMEZONE", "America/Denver"))
    except Exception:
        return None


def run_report_scheduler():
    """Fire fulfillment reports from inside the webhook process (prod mode):
      • Warehouse manifest — DAILY at DAILY_MANIFEST_HOUR (default 07:00, report TZ).
      • Supplier bulk order — WEEKLY at Sunday 00:00 (week just closed).
    Guarded so each fires at most once per day / per week."""
    from datetime import datetime
    from agents.weekly_report import run_daily_manifest, run_supplier_bulk
    tz = _report_tz()
    daily_hour = int(os.environ.get("DAILY_MANIFEST_HOUR", "7"))
    last_manifest_day = None
    last_bulk_week = None
    while True:
        try:
            now = datetime.now(tz)
            day = now.strftime("%Y-%m-%d")
            if now.hour == daily_hour and last_manifest_day != day:
                print(f"[Main/Reports] daily manifest {day}:", run_daily_manifest())
                last_manifest_day = day
            if now.weekday() == 6 and now.hour == 0 and last_bulk_week != day:  # Sunday 00:xx
                print(f"[Main/Reports] weekly supplier bulk {day}:", run_supplier_bulk())
                last_bulk_week = day
        except Exception as e:
            print(f"[Main/Reports] {e}")
        time.sleep(300)  # check every 5 minutes


def run_tracking_loop(interval: int = 600):
    while True:
        try:
            check_and_notify_all()
        except Exception as e:
            print(f"[Main/TrackingAgent] {e}")
        time.sleep(interval)


def start_webhook_server(port: int = 5000):
    """Start the Twilio webhook Flask server for inbound SMS."""
    try:
        from flask import Flask, request
        from agents.messaging_agent import twilio_webhook_handler

        app = Flask(__name__)

        @app.route("/sms", methods=["POST"])
        def sms_reply():
            twiml = twilio_webhook_handler(request.form.to_dict())
            return twiml, 200, {"Content-Type": "text/xml"}

        @app.route("/voice", methods=["POST"])
        def voice_answer():
            """Answer incoming call and record it — used to capture WhatsApp verification codes."""
            twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Pause length="2"/>
    <Record maxLength="30" playBeep="false" recordingStatusCallback="/recording-done"/>
</Response>"""
            return twiml, 200, {"Content-Type": "text/xml"}

        @app.route("/recording-done", methods=["POST"])
        def recording_done():
            recording_url = request.form.get("RecordingUrl", "")
            recording_sid = request.form.get("RecordingSid", "")
            print(f"[Voice] Recording complete: {recording_url}")
            print(f"[Voice] Recording SID: {recording_sid}")
            return "", 204

        @app.route("/price-list.png")
        def price_list_image():
            """Serve the generated price list image (Chinese/English bilingual)."""
            from flask import send_file
            from core.price_image import CN_OUTPUT_PATH, generate_price_list_image_cn
            if not CN_OUTPUT_PATH.exists():
                generate_price_list_image_cn()
            return send_file(str(CN_OUTPUT_PATH), mimetype="image/png")

        def _xlsx_bytes():
            from core.price_image import XLSX_PATH, generate_price_list_xlsx
            if not XLSX_PATH.exists():
                generate_price_list_xlsx()
            with open(str(XLSX_PATH), "rb") as f:
                return f.read()

        _XLSX_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

        @app.route("/price-list.xlsx")
        def price_list_xlsx():
            from flask import Response
            data = _xlsx_bytes()
            return Response(data, status=200, headers={
                "Content-Type": _XLSX_CT,
                "Content-Length": str(len(data)),
            })

        # WhatsApp/Twilio derives the displayed document name from the media URL's
        # last path segment. We must NOT send a Content-Disposition with an ASCII
        # filename here — Twilio prefers it and would override the Chinese name.
        # Serving at this Chinese path with no Content-Disposition is what makes
        # the received file show 北线集团研究肽价格表.xlsx
        # ("Northline Group Research Peptide Price List").
        @app.route("/北线集团研究肽价格表.xlsx")
        def price_list_xlsx_cn():
            from flask import Response
            data = _xlsx_bytes()
            return Response(data, status=200, headers={
                "Content-Type": _XLSX_CT,
                "Content-Length": str(len(data)),
            })

        @app.route("/price-list.xls")
        def price_list_xls():
            from flask import Response
            from core.price_image import XLS_PATH, generate_price_list_xls
            if not XLS_PATH.exists():
                generate_price_list_xls()
            with open(str(XLS_PATH), "rb") as f:
                data = f.read()
            return Response(data, status=200, headers={
                "Content-Type": "application/vnd.ms-excel",
                "Content-Length": str(len(data)),
            })

        @app.route("/price-list.pdf")
        def price_list_pdf():
            from flask import send_file
            from core.price_image import PDF_PATH, generate_price_list_pdf
            if not PDF_PATH.exists():
                generate_price_list_pdf()
            return send_file(str(PDF_PATH), mimetype="application/pdf",
                             as_attachment=False,
                             download_name="Northline_Price_List.pdf")

        @app.route("/proof/<path:filename>")
        def proof_media(filename):
            """Serve a proof/legitimacy asset (lab video or product photo) so it can
            be sent to a prospect as a WhatsApp media attachment. Only serves files
            that are listed in the proof manifest — never arbitrary paths."""
            from flask import send_file, abort
            from core.proof_media import PROOF_DIR, load_manifest
            allowed = {e["file"] for e in load_manifest()}
            if filename not in allowed:
                abort(404)
            return send_file(str(PROOF_DIR / filename))

        # ── Warehouse tracking page ──────────────────────────────────────────
        # A phone-friendly page the warehouse rep opens from a WhatsApp link. Shows
        # today's paid orders (read-only) with ONE editable field each: tracking #.
        # On save, the tracking number is written to Airtable and texted to the
        # customer automatically. Guarded by MANIFEST_TOKEN.
        def _manifest_authorized(req):
            from config import settings
            tok = req.values.get("token", "")
            return bool(settings.manifest_token) and tok == settings.manifest_token

        @app.route("/manifest")
        def manifest_page():
            from flask import request, abort
            from html import escape
            from config import settings
            from core.airtable_client import airtable
            if not _manifest_authorized(request):
                abort(403)
            token = escape(settings.manifest_token)
            saved = request.args.get("saved", "")
            try:
                orders = airtable.get_orders_needing_tracking()
            except Exception as e:
                print(f"[Manifest] load failed: {e!r}")
                orders = []
            orders.sort(key=lambda o: o["fields"].get("order_ref", ""))

            banner = (f'<div class="ok">✓ Tracking saved and sent to the customer'
                      f'{(" for " + escape(saved)) if saved else ""}.</div>') if saved else ""
            cards = []
            for o in orders:
                f = o["fields"]
                items = "<br>".join(
                    escape(f"{int(it['fields'].get('kits') or 0)}x {it['fields'].get('product','')} "
                           f"{it['fields'].get('spec','')}".strip())
                    for it in airtable.get_items_for_order(o)) or "—"
                addr = "<br>".join(escape(x) for x in [
                    f.get("address_line1"), f.get("address_line2"),
                    " ".join(y for y in [f.get("city"), f.get("state_province"), f.get("postal_code")] if y),
                    f.get("country")] if x)
                ref = escape(f.get("order_ref", "") or o["id"])
                cards.append(f"""
                <div class="card">
                  <div class="ref">{ref}</div>
                  <div class="name">{escape(f.get('ship_name',''))}</div>
                  <div class="addr">{addr}</div>
                  <div class="items"><b>Items:</b><br>{items}</div>
                  <form method="POST" action="/manifest/save">
                    <input type="hidden" name="token" value="{token}">
                    <input type="hidden" name="order_id" value="{escape(o['id'])}">
                    <input class="trk" name="tracking" inputmode="latin" autocapitalize="characters"
                           placeholder="Enter tracking number" required>
                    <button type="submit">Save &amp; send to customer</button>
                  </form>
                </div>""")
            body = "".join(cards) if cards else '<div class="empty">🎉 All caught up — no orders waiting for tracking.</div>'
            html = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Northline — Shipping Tracking</title>
<style>
  body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#f2f3f5;margin:0;padding:16px;color:#1c1c1e}}
  .wrap{{max-width:640px;margin:0 auto}}
  h1{{font-size:20px;margin:4px 0 2px}} .sub{{color:#666;font-size:13px;margin-bottom:14px}}
  .card{{background:#fff;border-radius:14px;padding:14px 16px;margin-bottom:14px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
  .ref{{font-weight:700;font-size:16px}} .name{{font-weight:600;margin-top:2px}}
  .addr{{color:#444;font-size:14px;margin:6px 0}} .items{{font-size:14px;margin:8px 0 12px;color:#333}}
  .trk{{width:100%;box-sizing:border-box;font-size:17px;padding:12px;border:1px solid #ccc;border-radius:10px;margin-bottom:10px}}
  button{{width:100%;font-size:16px;font-weight:600;padding:13px;border:0;border-radius:10px;background:#0a84ff;color:#fff}}
  button:active{{background:#0768cc}}
  .ok{{background:#e7f8ec;color:#16692e;border-radius:10px;padding:12px;margin-bottom:14px;font-weight:600}}
  .empty{{background:#fff;border-radius:14px;padding:28px 16px;text-align:center;color:#555}}
</style></head><body>
<div class="wrap">
<h1>📦 Shipping Tracking</h1>
<div class="sub">Enter the tracking number for each order. It is sent to the customer right away.</div>
{banner}{body}
</div>
</body></html>"""
            return html

        @app.route("/manifest/save", methods=["POST"])
        def manifest_save():
            from flask import request, redirect, abort
            from urllib.parse import quote
            from config import settings
            from core.airtable_client import airtable
            from agents.messaging_agent import send_tracking_to_customer
            if not _manifest_authorized(request):
                abort(403)
            order_id = request.form.get("order_id", "")
            tracking = (request.form.get("tracking", "") or "").strip()
            if not order_id or not tracking:
                return redirect(f"/manifest?token={quote(settings.manifest_token)}")
            try:
                order = airtable.get_order(order_id)
                airtable.set_order_tracking(order_id, tracking)
                phone = airtable.get_lead_phone_for_order(order)
                name = order["fields"].get("ship_name", "")
                send_tracking_to_customer(phone, tracking, name)
                ref = order["fields"].get("order_ref", "")
            except Exception as e:
                print(f"[Manifest] save failed for {order_id}: {e!r}")
                ref = ""
            return redirect(f"/manifest?token={quote(settings.manifest_token)}&saved={quote(ref)}")

        @app.route("/health")
        def health():
            return {"status": "ok"}, 200

        # Fulfillment report scheduler (daily manifest + weekly bulk) runs in-process.
        threading.Thread(target=run_report_scheduler, daemon=True).start()
        print("[Main] Report scheduler started (daily manifest + weekly bulk)")

        print(f"[Main] Webhook server starting on port {port}")
        app.run(host="0.0.0.0", port=port, debug=False)
    except ImportError:
        print("[Main] Flask not installed. Skipping webhook server. Run: pip install flask")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    if mode == "webhook":
        port = int(os.environ.get("PORT", 8080))
        start_webhook_server(port=port)
        return

    if mode == "ads":
        run_ad_loop()
        return

    if mode == "leadgen":
        run_lead_gen_agent()
        return

    if mode in ("weekly", "daily", "report"):  # one-shot report runs
        from agents.weekly_report import run_for_week, run_supplier_bulk, run_daily_manifest
        if mode == "weekly":
            print(run_supplier_bulk())
        elif mode == "daily":
            print(run_daily_manifest())
        else:  # report <week-tag>
            print(run_for_week(sys.argv[2]))
        return

    if mode == "tracking":
        run_tracking_loop()
        return

    # Run all background loops + webhook server
    print("[Main] Starting all agents...")

    threads = [
        threading.Thread(target=run_ad_loop, daemon=True),
        threading.Thread(target=run_lead_gen_loop, daemon=True),
        threading.Thread(target=run_report_scheduler, daemon=True),
        threading.Thread(target=run_tracking_loop, daemon=True),
    ]

    for t in threads:
        t.start()

    # Webhook server runs in the main thread
    start_webhook_server(port=8080)


if __name__ == "__main__":
    main()
