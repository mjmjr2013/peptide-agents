from __future__ import annotations
"""
The manifest WEB PAGE — the thing Jason and his crew actually work from.

WHY THIS EXISTS (2026-08-31, Jordan). The page used to be a stack of plain cards
and the labelling detail lived in a spreadsheet emailed alongside it. That split
was the problem: *"I don't want it to be a separate spreadsheet that's siloed
away and isn't automatically integrated."* Anything typed into an emailed file
reaches nobody — it cannot become a tracking number in Airtable, and it cannot
message a customer.

So the page IS the manifest. It reads like the workbook we designed — collapsed
orders that expand on a tap, packages broken out, the sticker pictured beside
every row, the strength in its own column — and it is where tracking is entered,
one box per package, writing straight to Airtable.

Rendering is split out of main.py because it is a lot of HTML and main.py is the
app wiring. It takes `core.manifest.build_view` output, which is the SAME
structure the workbook renders from, so the page and any printed sheet cannot
show different pictures of an order.

NOTHING HERE TALKS TO AIRTABLE. It renders what it is given and returns a string,
which is what makes it testable without credentials.
"""
from html import escape

TAB_LABEL = "label"
TAB_PHOTO = "photo"


def _sticker(row: dict) -> str:
    if row.get("label_url"):
        return (f'<img class="sticker" src="{escape(row["label_url"])}" '
                f'alt="{escape(row["sku"])} label" loading="lazy">')
    # Never a blank space: at a bench, blank reads as "no sticker needed".
    return ('<div class="nosticker">&#9888; NO STICKER ON FILE<br>'
            'do not label &mdash; ask Jordan</div>')


def _sku_rows(pkg: dict) -> str:
    """A real table, laid out like the sheet Daniel produced and the crew already
    reads: SKU, product, size, quantity — and the sticker on the RIGHT, which is
    the column they look at while they work."""
    body = []
    for r in pkg["rows"]:
        kits = r["kits"]
        body.append(
            '<tr>'
            f'<td class="c-sku">{escape(r["sku"])}</td>'
            f'<td class="c-name">{escape(r["label_text"])}</td>'
            f'<td class="c-size">{escape(r["strength"])}</td>'
            f'<td class="c-kits">{kits}</td>'
            f'<td class="c-label">{_sticker(r)}</td>'
            '</tr>')
    return ('<table class="sheet"><thead><tr>'
            '<th class="c-sku">SKU</th>'
            '<th class="c-name">Product</th>'
            '<th class="c-size">Size</th>'
            '<th class="c-kits">Kits</th>'
            '<th class="c-label">Sticker Label</th>'
            '</tr></thead><tbody>' + "".join(body) + '</tbody></table>')


def _tracking_form(view: dict, pkg: dict, token: str) -> str:
    """One tracking box per package. This is the whole point of the rewrite:
    a three-parcel order needs three numbers, and each must reach Airtable."""
    have = view["tracking"].get(pkg["index"], "")
    hidden = (f'<input type="hidden" name="token" value="{token}">'
              f'<input type="hidden" name="order_id" value="{escape(view["id"])}">'
              f'<input type="hidden" name="package" value="{pkg["index"]}">'
              f'<input type="hidden" name="of" value="{pkg["of"]}">')
    if have:
        return ('<div class="trkdone">&#10003; Tracking: '
                f'<b>{escape(have)}</b>'
                '<form method="POST" action="/manifest/save" class="inline">'
                + hidden
                + '<input class="trk small" name="tracking" placeholder="replace" '
                  'inputmode="latin" autocapitalize="characters" required>'
                  '<button type="submit" class="ghost">Update</button></form></div>')
    return ('<form method="POST" action="/manifest/save" class="trkform">'
            + hidden
            + '<input class="trk" name="tracking" inputmode="latin" '
              'autocapitalize="characters" required placeholder="Tracking number for '
              f'package {pkg["index"]} of {pkg["of"]}">'
              '<button type="submit">Save</button></form>')


def _package(view: dict, pkg: dict, token: str, mode: str) -> str:
    """One package: its contents as a sheet, then the action for this tab.

    Tab 1 asks for that package's tracking number, tab 2 for that package's
    photo. Both are per-package for the same reason — the crew works parcels.
    """
    water = ('' if pkg["capped"]
             else ' <span class="nocap">water only &mdash; ships whole</span>')
    head = (f'<div class="pkghead">PACKAGE {pkg["index"]} of {pkg["of"]} '
            f'&mdash; {pkg["kits"]} kit{"s" if pkg["kits"] != 1 else ""} '
            f'&mdash; {pkg["gross_g"] / 1000:.2f} kg{water}</div>')
    action = (_tracking_form(view, pkg, token) if mode == TAB_LABEL
              else _photo_form(view, pkg, token))
    return f'<div class="pkg">{head}{_sku_rows(pkg)}{action}</div>'


def _photo_form(view: dict, pkg: dict, token: str) -> str:
    """One photo per PACKAGE, mirroring the tracking boxes.

    The photo shows that parcel's vials with the customer's name and address
    visible (§16). A single photo of a three-parcel order proves nothing about
    the other two.
    """
    have = view["photos"].get(pkg["index"], "")
    if have:
        return ('<div class="trkdone">&#10003; Photo taken for package '
                f'{pkg["index"]} of {pkg["of"]}'
                f'<a class="ghost link" href="{escape(have)}" target="_blank" '
                'rel="noopener">view</a></div>')
    uid = f'{escape(view["id"])}_{pkg["index"]}'
    return (
        '<form method="POST" action="/manifest/photo" enctype="multipart/form-data" '
        'class="pform">'
        f'<input type="hidden" name="token" value="{token}">'
        f'<input type="hidden" name="order_id" value="{escape(view["id"])}">'
        f'<input type="hidden" name="package" value="{pkg["index"]}">'
        f'<input type="hidden" name="of" value="{pkg["of"]}">'
        f'<label class="file" for="f{uid}">&#128247; Photo of package '
        f'{pkg["index"]} of {pkg["of"]} &mdash; take / choose picture'
        f'<input id="f{uid}" type="file" name="photo" accept="image/*" required '
        "onchange=\"this.closest('form').querySelector('button').disabled=!this.files.length;"
        "this.closest('label').classList.add('picked');"
        "this.closest('label').firstChild.textContent='\\ud83d\\udcf7 '+this.files[0].name+' ';\">"
        '</label>'
        '<button type="submit" class="photo-btn" disabled '
        "onclick=\"this.textContent='Sending…'\">Save photo</button></form>")


# How long an order has waited before it needs chasing. Amber at a week, red at
# a fortnight — the point is that an old order is impossible to scroll past, not
# that these are contractual deadlines.
AGE_WARN_DAYS = 7
AGE_LATE_DAYS = 14


def _age_badge(view: dict) -> str:
    """The date and the wait, on every row.

    Jordan's worry, exactly: fresh stock arrives and the newest order gets filled
    from it while a two-week-old one waits longer. Oldest sorts to the top, and
    this makes the wait visible rather than something you have to work out from
    an order reference.
    """
    if not view["ordered_at"]:
        return '<span class="age unknown">date unknown</span>'
    days = view["age_days"]
    cls = "age"
    if days is not None and days >= AGE_LATE_DAYS:
        cls = "age late"
    elif days is not None and days >= AGE_WARN_DAYS:
        cls = "age warn"
    when = escape(view["ordered_label"])
    if days is None:
        return f'<span class="{cls}">{when}</span>'
    if days == 0:
        return f'<span class="{cls}">{when} &middot; today</span>'
    return (f'<span class="{cls}">{when} &middot; waiting '
            f'{days} day{"s" if days != 1 else ""}</span>')


def _card(view: dict, mode: str, token: str, extra: str = "") -> str:
    """One collapsed order. Native <details> — a tap expands it, no JavaScript,
    which matters on a warehouse phone with a bad connection."""
    n = view["package_count"]
    if mode == TAB_LABEL:
        done = sum(1 for i in view["tracking"] if view["tracking"][i])
        pill = (f'<span class="pill">{done}/{n} tracked</span>' if n > 1
                else '<span class="pill">tracking needed</span>')
    else:
        done = sum(1 for i in view["photos"] if view["photos"][i])
        pill = (f'<span class="pill photo">{done}/{n} photographed</span>' if n > 1
                else '<span class="pill photo">photo needed</span>')

    phone = f' &#9742; {escape(view["phone"])}' if view["phone"] else ""
    packages = "".join(_package(view, p, token, mode) for p in view["packages"])
    return (
        '<details class="card"><summary>'
        f'<span class="ref">{escape(view["ref"])}</span>'
        f'<span class="who">{escape(view["name"])}</span>'
        f'{_age_badge(view)}'
        f'<span class="meta">{n} package{"s" if n != 1 else ""} &middot; '
        f'{view["kits"]} kit{"s" if view["kits"] != 1 else ""}</span>'
        f'{pill}</summary><div class="body">'
        f'<div class="addr">{escape(view["address"])}{phone}</div>'
        f'{extra}{packages}</div></details>')


CSS = """
  :root{--navy:#1f2a44;--band:#dce3ef;--line:#e3e7ee}
  *{box-sizing:border-box}
  body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#f2f3f5;
       margin:0;padding:14px;color:#1c1c1e}
  .wrap{max-width:880px;margin:0 auto}
  h1{font-size:20px;margin:4px 0 2px}
  .sub{color:#666;font-size:13px;margin-bottom:14px}
  .tabs{display:flex;gap:8px;margin-bottom:14px}
  .tabs button{flex:1;font-size:14px;font-weight:700;padding:12px 8px;border:0;
       border-radius:10px;background:#fff;color:var(--navy);
       box-shadow:0 1px 3px rgba(0,0,0,.08);cursor:pointer}
  .tabs button.on{background:var(--navy);color:#fff}
  .card{background:#fff;border-radius:14px;margin-bottom:10px;
        box-shadow:0 1px 3px rgba(0,0,0,.08);overflow:hidden}
  summary{list-style:none;cursor:pointer;padding:14px 16px;display:flex;
          flex-wrap:wrap;align-items:baseline;gap:8px}
  summary::-webkit-details-marker{display:none}
  summary::before{content:"\\25B8";color:#8a94a6;font-size:13px;margin-right:2px}
  details[open] summary::before{content:"\\25BE"}
  details[open] summary{border-bottom:1px solid var(--line)}
  .ref{font-weight:700;font-size:15px}
  .who{font-weight:600;color:#333}
  .meta{color:#6b7280;font-size:13px}
  .age{font-size:12px;font-weight:700;background:#eef2f9;color:#475569;
       border-radius:20px;padding:3px 9px;white-space:nowrap}
  .age.warn{background:#fff4e0;color:#8a5a00}
  .age.late{background:#fdecea;color:#b3261e}
  .age.unknown{background:#f3f4f6;color:#6b7280;font-weight:600}
  .pill{margin-left:auto;font-size:12px;font-weight:700;background:#eef2f9;
        color:var(--navy);border-radius:20px;padding:4px 10px;white-space:nowrap}
  .pill.photo{background:#e7f8ec;color:#16692e}
  .body{padding:12px 16px 16px}
  .addr{color:#444;font-size:14px;margin-bottom:10px}
  .pkghead{background:var(--band);color:var(--navy);font-weight:700;font-size:13px;
           border-radius:8px;padding:8px 10px;margin:14px 0 6px}
  .nocap{font-weight:600;color:#4a5568}
  /* The sheet. Column order and the sticker on the right match the workbook
     Daniel produced, which the crew already knows how to read. */
  .sheet{width:100%;border-collapse:collapse;table-layout:fixed}
  .sheet th{background:var(--navy);color:#fff;font-size:12px;font-weight:700;
            text-align:left;padding:8px 10px}
  .sheet td{border-bottom:1px solid var(--line);padding:8px 10px;vertical-align:middle}
  .sheet tr:last-child td{border-bottom:0}
  .c-sku{width:14%;font-weight:700;font-size:14px}
  .c-name{width:32%;color:#374151;font-size:14px;word-break:break-word}
  .c-size{width:13%;font-weight:800;font-size:19px;color:var(--navy);text-align:center}
  .sheet th.c-size,.sheet th.c-kits{text-align:center}
  .c-kits{width:9%;font-weight:800;font-size:19px;text-align:center}
  .c-label{width:32%}
  .sticker{width:100%;max-width:190px;display:block;border:1px solid var(--line);
           border-radius:6px}
  .nosticker{font-size:11px;font-weight:700;color:#c0392b;border:1.5px dashed #c0392b;
             border-radius:6px;padding:8px;text-align:center;max-width:190px}
  .pform{margin-top:12px}
  .link{display:inline-block;text-decoration:none;margin-left:auto}
  .trkform{display:flex;gap:8px;margin-top:10px}
  .trk{flex:1;font-size:16px;padding:11px;border:1px solid #ccc;border-radius:10px}
  .trk.small{font-size:14px;padding:8px;max-width:150px}
  button{font-size:15px;font-weight:600;padding:11px 16px;border:0;border-radius:10px;
         background:#0a84ff;color:#fff;cursor:pointer}
  button.ghost{background:#eef2f9;color:var(--navy);padding:8px 12px;font-size:13px}
  .trkdone{display:flex;flex-wrap:wrap;align-items:center;gap:8px;color:#16692e;
           font-weight:600;font-size:14px;margin-top:10px}
  .inline{display:flex;gap:6px;margin-left:auto}
  .ok{background:#e7f8ec;color:#16692e;border-radius:10px;padding:12px;
      margin-bottom:14px;font-weight:600}
  /* A photo that did not save, or did not reach the customer, must not look like
     the green success banner — the crew reads colour before words. */
  .warn{background:#fdecea;color:#8a1c12;border:1px solid #f0b7b1;border-radius:10px;
        padding:12px;margin-bottom:14px;font-weight:700}
  .empty{background:#fff;border-radius:14px;padding:28px 16px;text-align:center;color:#555}
  .file{display:block;font-size:15px;font-weight:600;color:#0a84ff;
        border:1.5px dashed #0a84ff;border-radius:10px;padding:12px;text-align:center;
        margin:14px 0 10px;cursor:pointer}
  .file.picked{border-style:solid;background:#eef6ff}
  .file input{display:none}
  .photo-btn{background:#34c759;width:100%}
  button:disabled{opacity:.45}
  .labels{background:#fff8e6;border:1px solid #f0d089;border-radius:10px;
          padding:10px 12px;margin:10px 0;font-size:14px;color:#4a3a12}
  .lgrp{margin-top:8px;line-height:1.45}
  .tag{display:inline-block;font-size:12px;font-weight:700;color:#fff;
       border-radius:6px;padding:2px 7px;margin-right:6px}
  .tag.cust{background:#0a84ff}
  .tag.ours{background:#8e8e93}
  @media print{
    .tabs,form,.pill{display:none}
    details{page-break-inside:avoid}
    details>.body{display:block!important}
    body{background:#fff}
  }
  @media (max-width:600px){
    .sheet th,.sheet td{padding:6px 5px}
    .c-sku{width:17%;font-size:12px}
    .c-name{width:27%;font-size:12px}
    .c-size{width:14%;font-size:15px}
    .c-kits{width:10%;font-size:15px}
    .c-label{width:32%}
    .sheet th{font-size:11px}
  }
"""

# Remembering the tab is a convenience only — the page is fully usable if
# localStorage throws (private mode, a locked-down browser), so every access is
# wrapped. Expanding an order needs no JavaScript at all.
JS = """
function show(n){
  document.getElementById('p1').style.display = n===1 ? '' : 'none';
  document.getElementById('p2').style.display = n===2 ? '' : 'none';
  document.getElementById('t1').className = n===1 ? 'on' : '';
  document.getElementById('t2').className = n===2 ? 'on' : '';
  try{ localStorage.setItem('nl_tab', String(n)); }catch(e){}
}
try{ if(localStorage.getItem('nl_tab')==='2') show(2); }catch(e){}
"""


def render(to_label: list[dict], to_photo: list[dict], token: str,
           banner: str = "", label_extra=None) -> str:
    """The whole page.

    `label_extra(order_id) -> str` injects the per-order labelling-split notice
    for white-label deals; passed in so this module needs no deals import.
    """
    token = escape(token or "")

    def body(views, mode, empty):
        if not views:
            return f'<div class="empty">{empty}</div>'
        return "".join(
            _card(v, mode, token,
                  extra=(label_extra(v["id"]) if (label_extra and mode == TAB_LABEL) else ""))
            for v in views)

    tab1 = body(to_label, TAB_LABEL, "&#127881; Nothing new to label.")
    tab2 = body(to_photo, TAB_PHOTO, "&#127881; No orders waiting on a photo.")

    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>Northline &mdash; Shipping Manifest</title>'
        f'<style>{CSS}</style></head><body><div class="wrap">'
        '<h1>&#128230; Shipping Manifest</h1>'
        '<div class="sub"><b>Oldest orders are at the top &mdash; please work down '
        'the list.</b> Tap an order to open it. Check the strength against the sticker '
        'before you label &mdash; that is the one thing we cannot fix after it ships.</div>'
        '<div class="tabs">'
        f'<button id="t1" class="on" onclick="show(1)">1 &middot; Label &amp; Ship ({len(to_label)})</button>'
        f'<button id="t2" onclick="show(2)">2 &middot; Photo Before Ship ({len(to_photo)})</button>'
        '</div>'
        f'{banner}'
        f'<div id="p1">{tab1}</div>'
        f'<div id="p2" style="display:none">{tab2}</div>'
        f'</div><script>{JS}</script></body></html>')
