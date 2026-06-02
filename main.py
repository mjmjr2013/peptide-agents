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
from agents.fulfillment_agent import route_all_pending_orders
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


def run_fulfillment_loop(interval: int = 120):
    while True:
        try:
            route_all_pending_orders()
        except Exception as e:
            print(f"[Main/FulfillmentAgent] {e}")
        time.sleep(interval)


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

        @app.route("/price-list.xlsx")
        def price_list_xlsx():
            from flask import send_file
            from core.price_image import XLSX_PATH, generate_price_list_xlsx
            if not XLSX_PATH.exists():
                generate_price_list_xlsx()
            return send_file(str(XLSX_PATH),
                             mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             as_attachment=True,
                             download_name="Northline_Price_List.xlsx")

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

    if mode == "fulfillment":
        run_fulfillment_loop()
        return

    if mode == "tracking":
        run_tracking_loop()
        return

    # Run all background loops + webhook server
    print("[Main] Starting all agents...")

    threads = [
        threading.Thread(target=run_ad_loop, daemon=True),
        threading.Thread(target=run_lead_gen_loop, daemon=True),
        threading.Thread(target=run_fulfillment_loop, daemon=True),
        threading.Thread(target=run_tracking_loop, daemon=True),
    ]

    for t in threads:
        t.start()

    # Webhook server runs in the main thread
    start_webhook_server(port=8080)


if __name__ == "__main__":
    main()
