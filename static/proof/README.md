# Proof / legitimacy media

Drop lab videos and product/warehouse photos here, then describe each in
`manifest.json` so the sales agent can send them to prospects on request.

manifest.json is a JSON array of objects:

    [
      {
        "key": "lab_tour",
        "file": "lab_tour.mp4",
        "type": "video",
        "description": "Walkthrough of our lab with today's date on paper — best general proof we're a real lab."
      }
    ]

- `key`: short unique id (no spaces).
- `file`: exact filename in this folder.
- `type`: "image" or "video".
- `description`: what it shows + when it's the right thing to send (the agent picks by this).

Keep files under ~16 MB (WhatsApp media limit); compress long videos.
iPhone photos are often HEIC even if named .jpg — convert to real JPEG first:
`sips -s format jpeg in.heic --out out.jpg`. After editing, commit and redeploy.
