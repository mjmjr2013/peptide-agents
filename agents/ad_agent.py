from __future__ import annotations
"""
Ad Agent — monitors Meta Ads for disapprovals, submits appeals,
and activates backup ad accounts to relaunch campaigns.
"""
import json
import time
import requests
from datetime import datetime, timezone

from core.claude_client import claude
from core.airtable_client import airtable
from config import settings

META_GRAPH_URL = "https://graph.facebook.com/v21.0"

SYSTEM_PROMPT = """You are an expert Meta Ads compliance and appeal specialist for a peptide research supply company.

Your responsibilities:
1. Analyze why a Meta ad campaign was disapproved
2. Craft a compelling, policy-compliant appeal message
3. Recommend whether to appeal or relaunch with a new creative
4. Identify which backup ad account to activate for continuity

Peptide research supplies are sold exclusively to licensed research laboratories and professionals.
All claims are research-use-only. When writing appeals, emphasize:
- Products sold for research purposes only, not for human consumption
- Target audience is verified research institutions and licensed professionals
- All required disclaimers are present in ad creative
- Compliance with Meta's pharmaceutical and supplement advertising policies

Be concise, professional, and policy-aware. Output structured JSON when asked."""


class MetaAdsClient:
    def __init__(self):
        self.access_token = settings.meta_access_token
        self.app_id = settings.meta_app_id
        self.app_secret = settings.meta_app_secret
        self.business_id = settings.meta_business_manager_id

    def _get(self, path: str, params: dict = None) -> dict:
        params = params or {}
        params["access_token"] = self.access_token
        resp = requests.get(f"{META_GRAPH_URL}/{path}", params=params)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, data: dict = None) -> dict:
        data = data or {}
        data["access_token"] = self.access_token
        resp = requests.post(f"{META_GRAPH_URL}/{path}", data=data)
        resp.raise_for_status()
        return resp.json()

    def get_ad_accounts(self) -> list[dict]:
        """List all ad accounts accessible by the current user."""
        data = self._get(
            "me/adaccounts",
            {"fields": "id,name,account_status,disable_reason"},
        )
        return data.get("data", [])

    def get_campaigns(self, account_id: str) -> list[dict]:
        """Fetch campaigns with their effective status."""
        data = self._get(
            f"act_{account_id}/campaigns",
            {"fields": "id,name,status,effective_status,issues_info"},
        )
        return data.get("data", [])

    def get_ads(self, campaign_id: str) -> list[dict]:
        """Fetch ads within a campaign with review feedback."""
        data = self._get(
            f"{campaign_id}/ads",
            {"fields": "id,name,status,effective_status,review_feedback"},
        )
        return data.get("data", [])

    def submit_appeal(self, ad_id: str, message: str) -> dict:
        """Submit an appeal for a disapproved ad."""
        return self._post(
            f"{ad_id}/appeals",
            {"message": message},
        )

    def copy_campaign(self, campaign_id: str, target_account_id: str) -> dict:
        """Copy a campaign to another ad account."""
        return self._post(
            f"{campaign_id}/copies",
            {
                "deep_copy": "true",
                "replace_custom_targets": "false",
                "ad_account": f"act_{target_account_id}",
            },
        )

    def update_campaign_status(self, campaign_id: str, status: str) -> dict:
        """Set campaign status: ACTIVE, PAUSED, DELETED."""
        return self._post(f"{campaign_id}", {"status": status})

    def get_disapproved_campaigns_from_meta(self, account_id: str) -> list[dict]:
        """Return campaigns whose effective_status is DISAPPROVED."""
        campaigns = self.get_campaigns(account_id)
        return [c for c in campaigns if c.get("effective_status") == "DISAPPROVED"]


meta = MetaAdsClient()


# ── Claude tool definitions ────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "analyze_disapproval",
        "description": "Analyze why a campaign was disapproved and recommend next steps",
        "input_schema": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string"},
                "campaign_name": {"type": "string"},
                "issues_info": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Meta issues_info array from the campaign object",
                },
                "ad_review_feedback": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Review feedback from individual ads in the campaign",
                },
            },
            "required": ["campaign_id", "campaign_name"],
        },
    },
    {
        "name": "draft_appeal",
        "description": "Draft a policy-compliant appeal message for a disapproved ad",
        "input_schema": {
            "type": "object",
            "properties": {
                "ad_id": {"type": "string"},
                "disapproval_reason": {"type": "string"},
                "product_type": {"type": "string", "description": "e.g. 'research peptide'"},
            },
            "required": ["ad_id", "disapproval_reason"],
        },
    },
    {
        "name": "submit_appeal_action",
        "description": "Submit the appeal to Meta for a specific ad",
        "input_schema": {
            "type": "object",
            "properties": {
                "ad_id": {"type": "string"},
                "appeal_message": {"type": "string"},
            },
            "required": ["ad_id", "appeal_message"],
        },
    },
    {
        "name": "activate_backup_account",
        "description": "Copy a disapproved campaign to a backup ad account and activate it",
        "input_schema": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string"},
                "backup_account_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["campaign_id", "backup_account_id"],
        },
    },
    {
        "name": "update_airtable_campaign",
        "description": "Update campaign status in Airtable CRM",
        "input_schema": {
            "type": "object",
            "properties": {
                "airtable_record_id": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["Active", "Disapproved", "Appealing", "Paused"],
                },
                "appeal_status": {
                    "type": "string",
                    "enum": ["Not needed", "Submitted", "Won", "Lost"],
                },
            },
            "required": ["airtable_record_id", "status"],
        },
    },
    {
        "name": "get_backup_accounts",
        "description": "List available backup ad accounts that are in good standing",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


# ── Tool execution ─────────────────────────────────────────────────────────────

def execute_tool(tool_name: str, tool_input: dict, context: dict) -> str:
    if tool_name == "analyze_disapproval":
        issues = tool_input.get("issues_info", [])
        feedback = tool_input.get("ad_review_feedback", [])
        summary = {
            "campaign_id": tool_input["campaign_id"],
            "issues_count": len(issues),
            "issues": issues,
            "ad_feedback_count": len(feedback),
            "ad_feedback": feedback,
        }
        return json.dumps(summary)

    elif tool_name == "draft_appeal":
        # Return structured data for Claude to fill in; Claude already drafts the message
        return json.dumps({
            "ad_id": tool_input["ad_id"],
            "draft_ready": True,
            "note": "Use the appeal_message from your analysis as the content for submit_appeal_action",
        })

    elif tool_name == "submit_appeal_action":
        ad_id = tool_input["ad_id"]
        message = tool_input["appeal_message"]
        try:
            result = meta.submit_appeal(ad_id, message)
            return json.dumps({"success": True, "result": result})
        except requests.HTTPError as e:
            return json.dumps({"success": False, "error": str(e), "response": e.response.text})

    elif tool_name == "activate_backup_account":
        campaign_id = tool_input["campaign_id"]
        backup_id = tool_input["backup_account_id"]
        try:
            copy_result = meta.copy_campaign(campaign_id, backup_id)
            new_campaign_id = copy_result.get("copied_campaign_id")
            if new_campaign_id:
                meta.update_campaign_status(new_campaign_id, "ACTIVE")
            return json.dumps({
                "success": True,
                "new_campaign_id": new_campaign_id,
                "target_account": backup_id,
            })
        except requests.HTTPError as e:
            return json.dumps({"success": False, "error": str(e)})

    elif tool_name == "update_airtable_campaign":
        record_id = tool_input["airtable_record_id"]
        fields = {"status": tool_input["status"]}
        if "appeal_status" in tool_input:
            fields["appeal_status"] = tool_input["appeal_status"]
        try:
            result = airtable.update_campaign(record_id, **fields)
            return json.dumps({"success": True, "record_id": result["id"]})
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})

    elif tool_name == "get_backup_accounts":
        try:
            accounts = meta.get_ad_accounts()
            # Status 1 = ACTIVE, 2 = DISABLED, 3 = UNSETTLED, 7 = PENDING_RISK_REVIEW, 9 = IN_GRACE_PERIOD
            available = [
                {"id": a["id"].replace("act_", ""), "name": a.get("name", ""), "status": a.get("account_status")}
                for a in accounts
                if a.get("account_status") == 1
            ]
            return json.dumps({"accounts": available, "count": len(available)})
        except requests.HTTPError as e:
            return json.dumps({"error": str(e)})

    return json.dumps({"error": f"Unknown tool: {tool_name}"})


# ── Airtable sync helpers ──────────────────────────────────────────────────────

def get_airtable_record_for_campaign(meta_campaign_id: str) -> dict | None:
    """Find the Airtable record matching a Meta campaign ID."""
    records = airtable.campaigns.all(formula=f"{{meta_campaign_id}}='{meta_campaign_id}'")
    return records[0] if records else None


def ensure_campaign_in_airtable(meta_campaign_id: str, account_id: str) -> str:
    """Get or create the Airtable record for a campaign. Returns Airtable record_id."""
    existing = get_airtable_record_for_campaign(meta_campaign_id)
    if existing:
        return existing["id"]
    record = airtable.create_campaign(
        meta_campaign_id=meta_campaign_id,
        account_id=account_id,
        creative_variant="default",
    )
    return record["id"]


# ── Main agent loop ────────────────────────────────────────────────────────────

def run_ad_agent(account_ids: list[str] | None = None):
    """
    Main entry point. Scans all (or specified) ad accounts for disapprovals
    and uses Claude to reason through appeals and backup activation.
    """
    print(f"[AdAgent] Starting scan at {datetime.now(timezone.utc).isoformat()}")

    # If no account IDs specified, pull them from Meta
    if not account_ids:
        try:
            all_accounts = meta.get_ad_accounts()
            account_ids = [a["id"].replace("act_", "") for a in all_accounts]
        except requests.HTTPError as e:
            print(f"[AdAgent] Failed to fetch ad accounts: {e}")
            return

    disapproved: list[dict] = []
    for acct_id in account_ids:
        try:
            campaigns = meta.get_disapproved_campaigns_from_meta(acct_id)
            for c in campaigns:
                c["account_id"] = acct_id
            disapproved.extend(campaigns)
        except requests.HTTPError as e:
            print(f"[AdAgent] Error fetching campaigns for account {acct_id}: {e}")

    if not disapproved:
        print("[AdAgent] No disapproved campaigns found. All clear.")
        return

    print(f"[AdAgent] Found {len(disapproved)} disapproved campaign(s). Engaging Claude...")

    for campaign in disapproved:
        _handle_disapproved_campaign(campaign)


def _handle_disapproved_campaign(campaign: dict):
    campaign_id = campaign["id"]
    account_id = campaign["account_id"]
    campaign_name = campaign.get("name", campaign_id)

    print(f"[AdAgent] Processing campaign: {campaign_name} ({campaign_id})")

    # Fetch ad-level review feedback
    try:
        ads = meta.get_ads(campaign_id)
        ad_feedback = [
            {"ad_id": a["id"], "feedback": a.get("review_feedback", {})}
            for a in ads
        ]
    except requests.HTTPError:
        ad_feedback = []

    # Ensure campaign exists in Airtable and mark as disapproved
    airtable_id = ensure_campaign_in_airtable(campaign_id, account_id)
    airtable.update_campaign(airtable_id, status="Disapproved")

    # Build context for Claude
    context = {
        "campaign_id": campaign_id,
        "account_id": account_id,
        "airtable_record_id": airtable_id,
        "campaign_name": campaign_name,
    }

    initial_message = {
        "role": "user",
        "content": f"""A Meta Ads campaign has been disapproved and needs your attention.

Campaign: {campaign_name}
Campaign ID: {campaign_id}
Account ID: {account_id}
Airtable Record ID: {airtable_id}
Issues info: {json.dumps(campaign.get('issues_info', []), indent=2)}
Ad feedback: {json.dumps(ad_feedback, indent=2)}

Please:
1. Use analyze_disapproval to understand the issue
2. Use get_backup_accounts to see what backup accounts are available
3. For each disapproved ad, use draft_appeal then submit_appeal_action
4. If the appeal is unlikely to succeed (e.g. policy violation is severe), use activate_backup_account instead
5. Update the Airtable record with the final status using update_airtable_campaign

Be decisive — we need continuous ad coverage.""",
    }

    messages = [initial_message]

    # Agentic loop
    while True:
        response = claude.create(
            system=SYSTEM_PROMPT,
            messages=messages,
            tools=TOOLS,
            max_tokens=4096,
        )

        # Collect text output
        for block in response.content:
            if hasattr(block, "text"):
                print(f"[Claude] {block.text}")

        if response.stop_reason == "end_turn":
            print(f"[AdAgent] Campaign {campaign_name} handled.")
            break

        if response.stop_reason != "tool_use":
            print(f"[AdAgent] Unexpected stop reason: {response.stop_reason}")
            break

        # Process tool calls
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                print(f"[AdAgent] → {block.name}({json.dumps(block.input)})")
                result = execute_tool(block.name, block.input, context)
                print(f"[AdAgent] ← {result[:200]}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

        # Append assistant turn + tool results
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})


# ── Polling loop ───────────────────────────────────────────────────────────────

def poll(interval_seconds: int = 300, account_ids: list[str] | None = None):
    """Run the ad agent on a schedule. Default: every 5 minutes."""
    print(f"[AdAgent] Polling every {interval_seconds}s. Ctrl-C to stop.")
    while True:
        try:
            run_ad_agent(account_ids=account_ids)
        except Exception as e:
            print(f"[AdAgent] Unhandled error: {e}")
        time.sleep(interval_seconds)


if __name__ == "__main__":
    import sys

    if "--poll" in sys.argv:
        poll()
    else:
        run_ad_agent()
