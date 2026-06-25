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
