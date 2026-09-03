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
    from agents.health_monitor import check_claude, check_twilio_balance
    tz = _report_tz()
    daily_hour = int(os.environ.get("DAILY_MANIFEST_HOUR", "7"))
    last_manifest_day = None
    last_bulk_week = None
    last_canary_hour = None
    last_balance_day = None
    review_every = float(os.environ.get("REVIEW_INTERVAL_HOURS", "6")) * 3600
    last_review_ts = time.time()  # first review one interval after boot
    last_paywatch_ts = 0.0
    while True:
        try:
            now = datetime.now(tz)
            day = now.strftime("%Y-%m-%d")
            hour = now.strftime("%Y-%m-%d %H")
            if now.hour == daily_hour and last_manifest_day != day:
                print(f"[Main/Reports] daily manifest {day}:", run_daily_manifest())
                last_manifest_day = day
            if now.weekday() == 6 and now.hour == 0 and last_bulk_week != day:  # Sunday 00:xx
                print(f"[Main/Reports] weekly supplier bulk {day}:", run_supplier_bulk())
                last_bulk_week = day
            # Health: hourly Claude canary; daily Twilio balance check (see health_monitor.py)
            if last_canary_hour != hour:
                check_claude()
                last_canary_hour = hour
            if last_balance_day != day:
                check_twilio_balance()
                from agents.health_monitor import check_airtable
                check_airtable()
                last_balance_day = day
            # Payments: proactive on-chain check of awaiting orders. Every OTHER
            # tick (~10 min) — each cycle costs Airtable API calls, which are a
            # metered monthly quota (the 5-min cadence drained the free plan).
            if time.time() - last_paywatch_ts >= 600:
                from agents.payment_watcher import check_awaiting_payments
                pw = check_awaiting_payments()
                last_paywatch_ts = time.time()
                if pw.get("paid"):
                    print(f"[Main/PayWatch] {pw}")
            # QA: transcript reviewer — every REVIEW_INTERVAL_HOURS (see transcript_reviewer.py)
            if time.time() - last_review_ts >= review_every:
                from agents.transcript_reviewer import run_transcript_review
                print("[Main/Reviewer]", run_transcript_review())
                last_review_ts = time.time()
        except Exception as e:
            print(f"[Main/Reports] {e}")
        time.sleep(300)  # check every 5 minutes


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

        # The US warehouse sheet. Served at an English path because the filename
        # WhatsApp shows is taken from the last path segment, and this one goes to
        # US buyers — see _send_price_list in agents/messaging_agent.py.
        def _us_xlsx_bytes():
            from core.price_image import US_XLSX_PATH, generate_price_list_us_xlsx
            if not US_XLSX_PATH.exists():
                generate_price_list_us_xlsx()
            with open(str(US_XLSX_PATH), "rb") as f:
                return f.read()

        @app.route("/Northline_US_Warehouse_Price_List.xlsx")
        @app.route("/price-list-us.xlsx")
        def price_list_us_xlsx():
            from flask import Response
            data = _us_xlsx_bytes()
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
        # A phone-friendly page the warehouse rep opens from the daily email. Shows
        # paid orders (read-only) with TWO actions each: enter the tracking number,
        # and upload a photo of the packed vials. Each action writes to Airtable and
        # messages the customer automatically, then disappears from the card.
        # Guarded by MANIFEST_TOKEN.
        def _manifest_authorized(req):
            from config import settings
            tok = req.values.get("token", "")
            return bool(settings.manifest_token) and tok == settings.manifest_token

        @app.route("/manifest")
        def manifest_page():
            """The manifest itself, not a link to one.

            Jordan, 2026-08-31: the page should read like the labelling workbook —
            collapsed orders that expand, packages broken out, the sticker beside
            every row — AND be where tracking is entered, one box per package,
            feeding Airtable directly. A spreadsheet emailed alongside is siloed:
            anything typed into it reaches nobody.

            Rendering lives in core/manifest_page.py; the data comes from
            core.manifest.build_view, the same structure the workbook uses.
            """
            from flask import request, abort
            from html import escape
            from config import settings
            from core.airtable_client import airtable
            from core.manifest import build_view, split_by_stage
            from core import manifest_page as page
            if not _manifest_authorized(request):
                abort(403)
            saved = request.args.get("saved", "")
            photo = request.args.get("photo", "")
            # ?legacy=1 also shows pre-Jason orders the old rep is finishing off-system,
            # so tracking can still be entered for them (and the customer auto-notified).
            include_legacy = request.args.get("legacy", "") in ("1", "true", "yes")
            try:
                orders = airtable.get_orders_needing_fulfillment(include_legacy=include_legacy)
            except Exception as e:
                print(f"[Manifest] load failed: {e!r}")
                orders = []

            new_orders, photo_orders = split_by_stage(orders)
            try:
                to_label = build_view(new_orders, airtable.get_items_for_order)
                to_photo = build_view(photo_orders, airtable.get_items_for_order)
            except Exception as e:
                print(f"[Manifest] view build failed: {e!r}")
                to_label, to_photo = [], []

            banner = ""
            if saved:
                banner = f'<div class="ok">&#10003; Tracking saved for {escape(saved)}.</div>'
            elif photo:
                # The upload handler reports what it actually managed to do. A
                # blanket "sent to the customer" here once claimed success for a
                # failed upload AND for a partial job the code deliberately did
                # not send — the crew would have stopped chasing either one.
                st = request.args.get("st", "")
                if st == "sent":
                    banner = ('<div class="ok">&#10003; Vial photo sent to the customer '
                              f'for {escape(photo)}.</div>')
                elif st.startswith("partial:"):
                    _p = st.split(":")
                    have, need = (_p[1], _p[2]) if len(_p) == 3 else ("?", "?")
                    banner = ('<div class="ok">&#10003; Photo saved for '
                              f'{escape(photo)} &mdash; {escape(have)} of {escape(need)} '
                              'packages done. The customer is messaged once every '
                              'package has a photo.</div>')
                elif st == "savedonly":
                    banner = ('<div class="warn">&#9888; Photo saved for '
                              f'{escape(photo)}, but sending it to the customer '
                              'FAILED. Please tell the office.</div>')
                else:
                    banner = ('<div class="warn">&#9888; That photo did NOT save'
                              + (f' for {escape(photo)}' if photo else '')
                              + '. Please try again.</div>')

            by_id = {o["id"]: o for o in orders}

            def label_extra(order_id):
                """Labelling instructions for deals where the customer's branding
                covers only part of the order (the rest ship under our own label).
                Without this the packer sees one undifferentiated kit list."""
                rec = by_id.get(order_id)
                if rec is None:
                    return ""
                try:
                    from core.deals import get_deal, labeling_split
                    deal = get_deal(rec["fields"].get("promo_code", ""))
                    split = labeling_split(deal) if deal else None
                except Exception as e:
                    print(f"[Manifest] labeling split failed for {order_id}: {e!r}")
                    return ""
                if not split:
                    return ""
                def rows(lines):
                    return "<br>".join(escape(f"{n}x {p} {s}".strip()) for p, s, n in lines)
                if split["mixed"]:
                    return ('<div class="labels"><b>&#127991; TWO LABEL TYPES &mdash; '
                            'check before packing</b>'
                            '<div class="lgrp"><span class="tag cust">Customer branding</span>'
                            f'{split["branded_kits"]} kits<br>{rows(split["branded"])}</div>'
                            '<div class="lgrp"><span class="tag ours">Northline label</span>'
                            f'{split["unbranded_kits"]} kits<br>{rows(split["unbranded"])}</div></div>')
                return (f'<div class="labels"><b>&#127991; All {split["branded_kits"]} kits: '
                        f'customer branding</b></div>')

            return page.render(to_label, to_photo, settings.manifest_token,
                               banner=banner, label_extra=label_extra)

        @app.route("/manifest/save", methods=["POST"])
        def manifest_save():
            """Record ONE package's tracking number.

            An order that ships in three parcels has three numbers, so this merges
            into the order's tracking field rather than replacing it, and only
            marks the order tracked — and messages the customer — once EVERY
            package has a number. Telling a buyer "your shipment is booked" while
            two of three parcels have no label is worse than waiting a few hours.
            """
            from flask import request, redirect, abort
            from urllib.parse import quote
            from config import settings
            from core.airtable_client import airtable
            from core.manifest import parse_tracking, format_tracking, tracking_numbers
            from agents.messaging_agent import send_tracking_to_customer
            if not _manifest_authorized(request):
                abort(403)
            back = f"/manifest?token={quote(settings.manifest_token)}"
            order_id = request.form.get("order_id", "")
            tracking = (request.form.get("tracking", "") or "").strip()
            try:
                index = int(request.form.get("package", "1") or 1)
                total = int(request.form.get("of", "1") or 1)
            except (TypeError, ValueError):
                index, total = 1, 1
            if not order_id or not tracking:
                return redirect(back)
            ref = ""
            try:
                order = airtable.get_order(order_id)
                ref = order["fields"].get("order_ref", "")
                numbers = parse_tracking(order["fields"].get("tracking_number", ""))
                numbers[index] = tracking
                merged = format_tracking(numbers, total)
                complete = all(numbers.get(i) for i in range(1, total + 1))
                airtable.set_order_tracking(order_id, merged, complete=complete)
                if complete:
                    phone = airtable.get_lead_phone_for_order(order)
                    name = order["fields"].get("ship_name", "")
                    send_tracking_to_customer(phone, tracking_numbers(merged), name)
                else:
                    print(f"[Manifest] {ref}: {len(numbers)}/{total} packages tracked "
                          f"— customer not messaged yet")
            except Exception as e:
                print(f"[Manifest] save failed for {order_id}: {e!r}")
            return redirect(f"{back}&saved={quote(ref)}")

        @app.route("/manifest/photo", methods=["POST"])
        def manifest_photo():
            """Warehouse uploads a photo of ONE package's packed vials.

            Per package, like the tracking numbers (Jordan, 2026-08-31): a single
            photo of a three-parcel order proves nothing about the other two. The
            package number rides in the filename, so Airtable's attachment field
            (a list) carries them all with no schema change.

            The customer is messaged only once EVERY package is photographed —
            that message is the final "about to dispatch" signal (§18a), and it
            must not fire on a partial job.
            """
            from flask import request, redirect, abort
            from urllib.parse import quote
            from config import settings
            from core.airtable_client import airtable
            from core.manifest import photo_filename, parse_photo_packages
            from agents.messaging_agent import send_vial_photo_to_customer
            if not _manifest_authorized(request):
                abort(403)
            back = f"/manifest?token={quote(settings.manifest_token)}"
            order_id = request.form.get("order_id", "")
            up = request.files.get("photo")
            try:
                index = int(request.form.get("package", "1") or 1)
                total = int(request.form.get("of", "1") or 1)
            except (TypeError, ValueError):
                index, total = 1, 1
            if not order_id or not up or not up.filename:
                return redirect(back)
            ref = ""
            state = "failed"
            try:
                # Normalize whatever the phone camera produced: fix EXIF rotation,
                # flatten to RGB JPEG, cap the long edge — keeps it well under the
                # 5 MB Airtable-upload and WhatsApp-image limits.
                import io
                from PIL import Image, ImageOps
                img = ImageOps.exif_transpose(Image.open(up.stream))
                img = img.convert("RGB")
                img.thumbnail((1600, 1600))
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85)
                data = buf.getvalue()

                order = airtable.get_order(order_id)
                ref = order["fields"].get("order_ref", "")
                airtable.attach_vial_photo(
                    order_id, data, photo_filename(ref or order_id, index, total))

                # Re-read so the completeness check sees what Airtable actually holds
                # rather than what we think we just wrote.
                fresh = airtable.get_order(order_id)
                photos = parse_photo_packages(fresh["fields"])
                if all(photos.get(i) for i in range(1, total + 1)):
                    urls = [photos[i] for i in sorted(photos) if photos.get(i)]
                    phone = airtable.get_lead_phone_for_order(fresh)
                    name = fresh["fields"].get("ship_name", "")
                    if send_vial_photo_to_customer(phone, urls, name):
                        airtable.mark_vial_photo_sent(order_id)
                        state = "sent"
                    else:
                        # Saved, but the customer was NOT reached. Telling the crew
                        # it went out would end the job here and nobody would chase it.
                        state = "savedonly"
                else:
                    print(f"[Manifest] {ref}: {len(photos)}/{total} packages "
                          f"photographed — customer not messaged yet")
                    state = f"partial:{len(photos)}:{total}"
            except Exception as e:
                print(f"[Manifest] vial photo failed for {order_id}: {e!r}")
                state = "failed"
            return redirect(f"{back}&photo={quote(ref)}&st={quote(state)}")

        @app.route("/admin/email-test")
        def admin_email_test():
            """Send a test email FROM THIS SERVER — proves outbound SMTP works in
            prod (Railway blocks SMTP on Hobby; Pro unblocks). Token-guarded."""
            from flask import request, abort
            if not _manifest_authorized(request):
                abort(403)
            from agents.weekly_report import _send_email
            ok = _send_email("Northline PROD email test",
                            "This email was sent from the production server on Railway. "
                            "If you are reading it, outbound SMTP works in prod.", [])
            return {"emailed": ok}, 200

        @app.route("/admin/run-manifest")
        def admin_run_manifest():
            """Trigger the daily warehouse manifest immediately (same code the
            scheduler runs at DAILY_MANIFEST_HOUR). Token-guarded."""
            from flask import request, abort
            if not _manifest_authorized(request):
                abort(403)
            from agents.weekly_report import run_daily_manifest
            return run_daily_manifest(), 200

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

    # Run all background loops + webhook server
    print("[Main] Starting all agents...")

    threads = [
        threading.Thread(target=run_ad_loop, daemon=True),
        threading.Thread(target=run_lead_gen_loop, daemon=True),
        threading.Thread(target=run_report_scheduler, daemon=True),
    ]

    for t in threads:
        t.start()

    # Webhook server runs in the main thread
    start_webhook_server(port=8080)


if __name__ == "__main__":
    main()
