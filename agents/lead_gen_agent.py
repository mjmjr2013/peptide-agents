from __future__ import annotations
"""
Lead Gen Agent — creates and optimises Click-to-WhatsApp Meta ad campaigns
to drive inbound research-lab leads into the Twilio WhatsApp inbox.

Strategy
--------
Ads use Meta's "Click to WhatsApp" call-to-action so every tap opens a
WhatsApp conversation with the Twilio number that feeds the inbound
peptide agent.  No landing page, no form — the lead lands straight in
the chat.

Run cadence (via main.py loop): every 6 hours.
Each run:
  1. Pull insights for all active lead-gen campaigns (spend, clicks, leads, CPL)
  2. Feed the data to Claude with the full tool set
  3. Claude decides: scale a winner / pause a dud / launch a new creative variant
  4. Execute the decisions; log to Airtable

Env vars required (add to Railway + .env):
  META_ACCESS_TOKEN          — long-lived system-user token
  META_APP_ID / META_APP_SECRET
  META_BUSINESS_MANAGER_ID
  META_AD_ACCOUNT_ID         — primary ad account for lead gen (without "act_" prefix)
  META_PAGE_ID               — Facebook Page the ads run from
  RAILWAY_PUBLIC_URL         — e.g. https://peptide-agents-production.up.railway.app
"""

import json
import math
import time
import requests
from datetime import datetime, timezone
from typing import Any

from core.claude_client import claude
from core.airtable_client import airtable
from config import settings

META_GRAPH_URL = "https://graph.facebook.com/v21.0"

# ── Whatsapp number (strip "whatsapp:" prefix if present) ─────────────────────
def _whatsapp_number() -> str:
    raw = getattr(settings, "twilio_whatsapp_from", "") or getattr(settings, "twilio_phone_number", "")
    return raw.replace("whatsapp:", "").strip()

# ── System prompt ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a Meta Ads performance specialist running lead generation campaigns for Northline Group, a research-grade peptide supplier.

GOAL: Drive research labs and professionals to WhatsApp us so our inbound agent can qualify them and convert them to customers.

AD FORMAT: Every campaign uses Click-to-WhatsApp ads. When someone taps the ad, WhatsApp opens and they can message us directly. No landing page.

TARGET AUDIENCE: Research scientists, lab managers, principal investigators, procurement managers at biotech/pharma/academic labs.

TONE: Professional, scientific, credible. Never make medical or human-consumption claims. Always include "For Research Use Only."

DECISION RULES:
- If a campaign has spent >$15 and cost-per-lead (CPL) < $4 → scale budget 25%
- If a campaign has spent >$20 and CPL > $10 (or zero leads) → pause it and create a new variant with different copy
- If no active campaigns exist → create one immediately
- If CTR < 0.3% after $10 spend → pause and create a new variant
- Run at most 3 active campaigns simultaneously to stay focused
- Always explain your reasoning before each action

When creating ad copy, write MULTIPLE variants for A/B testing. Keep headlines under 40 chars. Body copy under 125 chars for mobile. Be direct — lead with value.

Output structured decisions then use the appropriate tools to execute them."""

# ── Ad copy templates Claude can build on ─────────────────────────────────────

COPY_VARIANTS = [
    {
        "variant": "A",
        "headline": "Research Peptides — Fast Delivery",
        "body": "Lab-grade peptides for research. Competitive pricing, same-week dispatch. Message us for full price list. Research use only.",
    },
    {
        "variant": "B",
        "headline": "Premium Peptides for Your Lab",
        "body": "BPC-157, TB-500, Semaglutide & 150+ research compounds. Get our wholesale price list instantly on WhatsApp.",
    },
    {
        "variant": "C",
        "headline": "Wholesale Peptides — Labs Only",
        "body": "Research-grade peptides. Bulk pricing available. 150+ SKUs. Tap to get our full catalogue on WhatsApp. For research use only.",
    },
    {
        "variant": "D",
        "headline": "Research Peptides at Lab Prices",
        "body": "Serving research labs & institutions. BPC-157, TB-500, GHK-Cu & more. Message us for pricing — we reply fast.",
    },
]


# ── Meta Graph API client ──────────────────────────────────────────────────────

class MetaLeadGenClient:
    def __init__(self):
        self.token = settings.meta_access_token
        self.account_id = getattr(settings, "meta_ad_account_id", "")
        self.page_id = getattr(settings, "meta_page_id", "")
        self.railway_url = getattr(settings, "railway_public_url", "")

    def _get(self, path: str, params: dict | None = None) -> dict:
        p = dict(params or {})
        p["access_token"] = self.token
        r = requests.get(f"{META_GRAPH_URL}/{path}", params=p, timeout=30)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, data: dict | None = None) -> dict:
        d = dict(data or {})
        d["access_token"] = self.token
        r = requests.post(f"{META_GRAPH_URL}/{path}", data=d, timeout=30)
        r.raise_for_status()
        return r.json()

    # ── Campaign management ────────────────────────────────────────────────

    def create_campaign(self, name: str, objective: str = "OUTCOME_ENGAGEMENT") -> dict:
        return self._post(
            f"act_{self.account_id}/campaigns",
            {
                "name": name,
                "objective": objective,
                "status": "ACTIVE",
                "special_ad_categories": json.dumps([]),
            },
        )

    def create_adset(
        self,
        campaign_id: str,
        name: str,
        daily_budget_cents: int,
        targeting: dict,
    ) -> dict:
        whatsapp_num = _whatsapp_number()
        return self._post(
            f"act_{self.account_id}/adsets",
            {
                "name": name,
                "campaign_id": campaign_id,
                "billing_event": "IMPRESSIONS",
                "optimization_goal": "LINK_CLICKS",
                "daily_budget": str(daily_budget_cents),
                "status": "ACTIVE",
                "destination_type": "WHATSAPP",
                "promoted_object": json.dumps(
                    {"page_id": self.page_id, "whatsapp_phone_number": whatsapp_num}
                ),
                "targeting": json.dumps(targeting),
                "start_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000"),
            },
        )

    def upload_image_from_url(self, image_url: str) -> str:
        """Upload an image to the ad account by URL and return the image hash."""
        result = self._post(
            f"act_{self.account_id}/adimages",
            {"url": image_url},
        )
        # Response: {"images": {"<filename>": {"hash": "...", ...}}}
        images = result.get("images", {})
        for _fname, meta in images.items():
            return meta["hash"]
        raise ValueError(f"Image upload failed: {result}")

    def create_ad_creative(
        self,
        name: str,
        headline: str,
        body: str,
        image_hash: str | None = None,
        image_url: str | None = None,
    ) -> dict:
        whatsapp_num = _whatsapp_number()
        link_data: dict[str, Any] = {
            "message": body,
            "name": headline,
            "call_to_action": json.dumps(
                {
                    "type": "WHATSAPP_MESSAGE",
                    "value": {
                        "app_destination": "WHATSAPP",
                        "whatsapp_phone_number": whatsapp_num,
                    },
                }
            ),
        }
        if image_hash:
            link_data["image_hash"] = image_hash
        elif image_url:
            link_data["picture"] = image_url

        story_spec = {
            "page_id": self.page_id,
            "link_data": link_data,
        }
        return self._post(
            f"act_{self.account_id}/adcreatives",
            {
                "name": name,
                "object_story_spec": json.dumps(story_spec),
            },
        )

    def create_ad(self, adset_id: str, creative_id: str, name: str) -> dict:
        return self._post(
            f"act_{self.account_id}/ads",
            {
                "name": name,
                "adset_id": adset_id,
                "creative": json.dumps({"creative_id": creative_id}),
                "status": "ACTIVE",
            },
        )

    # ── Insights ───────────────────────────────────────────────────────────

    def get_campaign_insights(self, campaign_id: str, days: int = 7) -> dict:
        return self._get(
            f"{campaign_id}/insights",
            {
                "fields": "campaign_name,spend,impressions,clicks,ctr,actions,cost_per_action_type",
                "date_preset": f"last_{days}_d",
            },
        )

    def get_all_lead_gen_campaigns(self) -> list[dict]:
        """Return ACTIVE campaigns tagged as lead-gen (name contains 'LeadGen')."""
        data = self._get(
            f"act_{self.account_id}/campaigns",
            {
                "fields": "id,name,status,effective_status,daily_budget",
                "effective_status": json.dumps(["ACTIVE"]),
            },
        )
        campaigns = data.get("data", [])
        return [c for c in campaigns if "LeadGen" in c.get("name", "")]

    # ── Budget & status mutations ──────────────────────────────────────────

    def update_adset_budget(self, adset_id: str, new_daily_budget_cents: int) -> dict:
        return self._post(f"{adset_id}", {"daily_budget": str(new_daily_budget_cents)})

    def pause_entity(self, entity_id: str) -> dict:
        return self._post(f"{entity_id}", {"status": "PAUSED"})

    def get_adsets_for_campaign(self, campaign_id: str) -> list[dict]:
        data = self._get(
            f"{campaign_id}/adsets",
            {"fields": "id,name,daily_budget,status,effective_status"},
        )
        return data.get("data", [])

    def get_ads_for_adset(self, adset_id: str) -> list[dict]:
        data = self._get(
            f"{adset_id}/ads",
            {"fields": "id,name,status,effective_status,creative"},
        )
        return data.get("data", [])


meta = MetaLeadGenClient()


# ── Standard research-lab targeting ────────────────────────────────────────────

def _lab_targeting(locations: list[str] | None = None) -> dict:
    """
    Broad B2B targeting for research scientists and procurement managers.
    Adjust geo_locations to match where Northline ships.
    """
    return {
        "geo_locations": {
            "countries": locations or ["US", "GB", "CA", "AU"],
        },
        "age_min": 25,
        "age_max": 65,
        "flexible_spec": [
            {
                "interests": [
                    {"id": "6003107902433", "name": "Biochemistry"},
                    {"id": "6003348522119", "name": "Pharmaceutical industry"},
                    {"id": "6003348535483", "name": "Biotechnology"},
                    {"id": "6002996264830", "name": "Life sciences"},
                    {"id": "6003321445745", "name": "Medical research"},
                ]
            }
        ],
        "publisher_platforms": ["facebook", "instagram"],
        "facebook_positions": ["feed", "story"],
        "instagram_positions": ["stream", "story"],
        "device_platforms": ["mobile", "desktop"],
    }


# ── Claude tools ───────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "get_all_active_campaigns_with_insights",
        "description": "Fetch all active LeadGen campaigns with their 7-day performance data (spend, clicks, CTR, leads, CPL). Call this first every run.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "create_whatsapp_campaign",
        "description": (
            "Launch a new Click-to-WhatsApp lead gen campaign from scratch "
            "(campaign + adset + creative + ad). Provide ad copy and starting budget. "
            "Name must include 'LeadGen' so the agent can track it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "campaign_name": {
                    "type": "string",
                    "description": "Must include 'LeadGen', e.g. 'LeadGen — Research Labs A'",
                },
                "adset_name": {"type": "string"},
                "headline": {"type": "string", "description": "Under 40 characters"},
                "body": {
                    "type": "string",
                    "description": "Ad body copy, under 125 characters for mobile. Must include 'For Research Use Only' or similar disclaimer.",
                },
                "daily_budget_usd": {
                    "type": "number",
                    "description": "Daily budget in USD, e.g. 15.0",
                },
                "use_price_list_image": {
                    "type": "boolean",
                    "description": "If true, uses the bilingual price list PNG from Railway as the ad image",
                },
            },
            "required": ["campaign_name", "adset_name", "headline", "body", "daily_budget_usd"],
        },
    },
    {
        "name": "scale_adset_budget",
        "description": "Increase the daily budget of a winning adset by a given percentage.",
        "input_schema": {
            "type": "object",
            "properties": {
                "adset_id": {"type": "string"},
                "increase_pct": {
                    "type": "number",
                    "description": "e.g. 25 to increase by 25%",
                },
                "reason": {"type": "string"},
            },
            "required": ["adset_id", "increase_pct"],
        },
    },
    {
        "name": "pause_campaign",
        "description": "Pause an underperforming campaign.",
        "input_schema": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["campaign_id"],
        },
    },
    {
        "name": "create_ad_variant",
        "description": (
            "Add a new ad with different copy to an existing adset — used for A/B testing. "
            "Reuses the adset's targeting and budget but creates a fresh creative."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "adset_id": {"type": "string"},
                "headline": {"type": "string"},
                "body": {"type": "string"},
                "variant_label": {
                    "type": "string",
                    "description": "Short label e.g. 'B' or 'Price-angle'",
                },
                "use_price_list_image": {"type": "boolean"},
            },
            "required": ["adset_id", "headline", "body", "variant_label"],
        },
    },
    {
        "name": "log_to_airtable",
        "description": "Record a new campaign or update an existing one in Airtable.",
        "input_schema": {
            "type": "object",
            "properties": {
                "meta_campaign_id": {"type": "string"},
                "account_id": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["Active", "Paused", "Disapproved"],
                },
                "creative_variant": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["meta_campaign_id", "account_id", "status"],
        },
    },
]


# ── Tool execution ─────────────────────────────────────────────────────────────

def _price_list_image_url() -> str:
    railway_url = getattr(settings, "railway_public_url", "").rstrip("/")
    return f"{railway_url}/price-list.png" if railway_url else ""


def execute_tool(tool_name: str, tool_input: dict) -> str:  # noqa: C901
    try:
        if tool_name == "get_all_active_campaigns_with_insights":
            campaigns = meta.get_all_lead_gen_campaigns()
            if not campaigns:
                return json.dumps({"campaigns": [], "message": "No active LeadGen campaigns found — consider creating one."})

            enriched = []
            for c in campaigns:
                try:
                    insights_raw = meta.get_campaign_insights(c["id"])
                    data = insights_raw.get("data", [{}])
                    ins = data[0] if data else {}
                    # Extract lead count from actions array
                    actions = ins.get("actions", [])
                    leads = sum(
                        int(a.get("value", 0))
                        for a in actions
                        if a.get("action_type") in ("onsite_conversion.messaging_conversation_started_7d", "lead")
                    )
                    spend = float(ins.get("spend", 0))
                    clicks = int(ins.get("clicks", 0))
                    ctr = float(ins.get("ctr", 0))
                    cpl = round(spend / leads, 2) if leads > 0 else None

                    adsets = meta.get_adsets_for_campaign(c["id"])
                    enriched.append({
                        "campaign_id": c["id"],
                        "campaign_name": c["name"],
                        "status": c["effective_status"],
                        "insights_7d": {
                            "spend_usd": spend,
                            "impressions": int(ins.get("impressions", 0)),
                            "clicks": clicks,
                            "ctr_pct": round(ctr, 3),
                            "leads": leads,
                            "cpl_usd": cpl,
                        },
                        "adsets": [
                            {
                                "id": a["id"],
                                "name": a["name"],
                                "daily_budget_cents": int(a.get("daily_budget", 0)),
                                "daily_budget_usd": round(int(a.get("daily_budget", 0)) / 100, 2),
                            }
                            for a in adsets
                        ],
                    })
                except Exception as e:
                    enriched.append({"campaign_id": c["id"], "error": str(e)})

            return json.dumps({"campaigns": enriched, "total_active": len(enriched)})

        elif tool_name == "create_whatsapp_campaign":
            name = tool_input["campaign_name"]
            adset_name = tool_input["adset_name"]
            headline = tool_input["headline"]
            body = tool_input["body"]
            budget_cents = math.ceil(tool_input["daily_budget_usd"] * 100)
            use_price_image = tool_input.get("use_price_list_image", True)

            # 1. Campaign
            camp = meta.create_campaign(name)
            campaign_id = camp["id"]
            print(f"[LeadGen] Created campaign: {name} ({campaign_id})")

            # 2. Ad set
            adset = meta.create_adset(
                campaign_id=campaign_id,
                name=adset_name,
                daily_budget_cents=budget_cents,
                targeting=_lab_targeting(),
            )
            adset_id = adset["id"]
            print(f"[LeadGen] Created adset: {adset_name} ({adset_id})")

            # 3. Creative
            image_hash = None
            image_url = None
            if use_price_image:
                pl_url = _price_list_image_url()
                if pl_url:
                    try:
                        image_hash = meta.upload_image_from_url(pl_url)
                        print(f"[LeadGen] Uploaded price list image → hash {image_hash}")
                    except Exception as e:
                        print(f"[LeadGen] Image upload failed: {e} — running without image")

            creative = meta.create_ad_creative(
                name=f"{name} — Creative",
                headline=headline,
                body=body,
                image_hash=image_hash,
                image_url=image_url,
            )
            creative_id = creative["id"]

            # 4. Ad
            ad = meta.create_ad(adset_id=adset_id, creative_id=creative_id, name=f"{name} — Ad")
            print(f"[LeadGen] Created ad: {ad['id']}")

            return json.dumps({
                "success": True,
                "campaign_id": campaign_id,
                "adset_id": adset_id,
                "creative_id": creative_id,
                "ad_id": ad["id"],
                "daily_budget_usd": tool_input["daily_budget_usd"],
                "whatsapp_number": _whatsapp_number(),
            })

        elif tool_name == "scale_adset_budget":
            adset_id = tool_input["adset_id"]
            pct = tool_input["increase_pct"]

            # Fetch current budget
            resp = meta._get(adset_id, {"fields": "daily_budget"})
            current = int(resp.get("daily_budget", 0))
            new_budget = math.ceil(current * (1 + pct / 100))
            meta.update_adset_budget(adset_id, new_budget)
            print(f"[LeadGen] Scaled adset {adset_id}: ${current/100:.2f} → ${new_budget/100:.2f}/day (+{pct}%)")
            return json.dumps({
                "success": True,
                "adset_id": adset_id,
                "old_budget_usd": round(current / 100, 2),
                "new_budget_usd": round(new_budget / 100, 2),
                "reason": tool_input.get("reason", ""),
            })

        elif tool_name == "pause_campaign":
            campaign_id = tool_input["campaign_id"]
            meta.pause_entity(campaign_id)
            print(f"[LeadGen] Paused campaign {campaign_id} — {tool_input.get('reason', '')}")
            return json.dumps({"success": True, "campaign_id": campaign_id, "status": "PAUSED"})

        elif tool_name == "create_ad_variant":
            adset_id = tool_input["adset_id"]
            headline = tool_input["headline"]
            body = tool_input["body"]
            label = tool_input.get("variant_label", "V")
            use_price_image = tool_input.get("use_price_list_image", True)

            image_hash = None
            if use_price_image:
                pl_url = _price_list_image_url()
                if pl_url:
                    try:
                        image_hash = meta.upload_image_from_url(pl_url)
                    except Exception as e:
                        print(f"[LeadGen] Image upload failed for variant: {e}")

            creative = meta.create_ad_creative(
                name=f"Creative Variant {label} — {adset_id}",
                headline=headline,
                body=body,
                image_hash=image_hash,
            )
            creative_id = creative["id"]
            ad = meta.create_ad(
                adset_id=adset_id,
                creative_id=creative_id,
                name=f"Ad Variant {label} — {adset_id}",
            )
            print(f"[LeadGen] Created ad variant {label}: {ad['id']}")
            return json.dumps({"success": True, "ad_id": ad["id"], "creative_id": creative_id, "variant": label})

        elif tool_name == "log_to_airtable":
            existing = airtable.campaigns.all(
                formula=f"{{meta_campaign_id}}='{tool_input['meta_campaign_id']}'"
            )
            if existing:
                record_id = existing[0]["id"]
                airtable.update_campaign(
                    record_id,
                    status=tool_input["status"],
                    creative_variant=tool_input.get("creative_variant", ""),
                    notes=tool_input.get("notes", ""),
                )
                return json.dumps({"success": True, "action": "updated", "record_id": record_id})
            else:
                record = airtable.create_campaign(
                    meta_campaign_id=tool_input["meta_campaign_id"],
                    account_id=tool_input["account_id"],
                    creative_variant=tool_input.get("creative_variant", "default"),
                )
                return json.dumps({"success": True, "action": "created", "record_id": record["id"]})

    except requests.HTTPError as e:
        body_text = ""
        try:
            body_text = e.response.json()
        except Exception:
            body_text = e.response.text
        return json.dumps({"success": False, "error": str(e), "meta_response": body_text})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})

    return json.dumps({"error": f"Unknown tool: {tool_name}"})


# ── Main agent loop ────────────────────────────────────────────────────────────

def run_lead_gen_agent():
    """Entry point called by main.py every 6 hours."""
    print(f"[LeadGen] Starting run at {datetime.now(timezone.utc).isoformat()}")

    if not meta.account_id:
        print("[LeadGen] META_AD_ACCOUNT_ID not set — skipping.")
        return
    if not meta.page_id:
        print("[LeadGen] META_PAGE_ID not set — skipping.")
        return

    whatsapp_num = _whatsapp_number()
    price_list_url = _price_list_image_url()

    messages = [
        {
            "role": "user",
            "content": f"""You are running the Northline Group lead generation check.

Context:
- WhatsApp inbound number (all ad CTAs point here): {whatsapp_num}
- Price list image URL (use as ad creative): {price_list_url or "not configured"}
- Ad account ID: {meta.account_id}
- Facebook Page ID: {meta.page_id}

Available copy variants for inspiration:
{json.dumps(COPY_VARIANTS, indent=2)}

Please:
1. Call get_all_active_campaigns_with_insights to see current performance
2. Based on the data, make optimisation decisions following the decision rules in your system prompt
3. Execute all decisions using the available tools
4. Log any new campaigns to Airtable

Be decisive. If there are no campaigns, create one now. If there are winners, scale them. If there are duds, pause and replace.""",
        }
    ]

    # Agentic loop
    while True:
        response = claude.create(
            system=SYSTEM_PROMPT,
            messages=messages,
            tools=TOOLS,
            max_tokens=8192,
        )

        # Print Claude's reasoning
        for block in response.content:
            if hasattr(block, "text") and block.text:
                print(f"[Claude/LeadGen] {block.text}")

        if response.stop_reason == "end_turn":
            print("[LeadGen] Run complete.")
            break

        if response.stop_reason != "tool_use":
            print(f"[LeadGen] Unexpected stop reason: {response.stop_reason}")
            break

        # Execute tools
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                print(f"[LeadGen] → {block.name}({json.dumps(block.input)[:200]})")
                result = execute_tool(block.name, block.input)
                print(f"[LeadGen] ← {result[:300]}")
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": result}
                )

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})


if __name__ == "__main__":
    run_lead_gen_agent()
