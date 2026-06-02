from __future__ import annotations
"""
Order Intake Agent — captures and validates orders from qualified leads,
writes confirmed orders to Airtable.
"""
import json
import re

from core.claude_client import claude
from core.airtable_client import airtable
from config import settings

SYSTEM_PROMPT = """You are an order intake specialist for Northline Group, a peptide research supply company.

Your job is to:
1. Extract structured order information from a lead's message or conversation
2. Validate that the order is complete and feasible
3. Calculate pricing based on product and quantity
4. Confirm the order with the customer before writing it to the system

Products and pricing (research grade):
- BPC-157: $0.045/mg (min 50mg)
- TB-500: $0.055/mg (min 100mg)
- Semaglutide: $0.18/mg (min 10mg)
- Tirzepatide: $0.22/mg (min 10mg)
- Ipamorelin: $0.038/mg (min 100mg)
- CJC-1295: $0.042/mg (min 100mg)
- Melanotan II: $0.048/mg (min 50mg)
- AOD-9604: $0.052/mg (min 100mg)
- Custom synthesis: contact for quote

Validation rules:
- Lead must have status "Qualified" in CRM
- Product must be in our catalog (or marked as custom)
- Quantity must meet minimum order
- Total must be calculable (or noted as custom quote)

Output a JSON block like:
{
  "status": "valid" | "invalid" | "needs_info",
  "product": "...",
  "quantity_mg": 0,
  "total_price": 0.0,
  "validation_errors": [],
  "confirmation_message": "...",
  "notes": "..."
}"""

PRICING = {
    "BPC-157": {"price_per_mg": 0.045, "min_mg": 50},
    "TB-500": {"price_per_mg": 0.055, "min_mg": 100},
    "Semaglutide": {"price_per_mg": 0.18, "min_mg": 10},
    "Tirzepatide": {"price_per_mg": 0.22, "min_mg": 10},
    "Ipamorelin": {"price_per_mg": 0.038, "min_mg": 100},
    "CJC-1295": {"price_per_mg": 0.042, "min_mg": 100},
    "Melanotan II": {"price_per_mg": 0.048, "min_mg": 50},
    "AOD-9604": {"price_per_mg": 0.052, "min_mg": 100},
}


def calculate_price(product: str, quantity_mg: float) -> float | None:
    info = PRICING.get(product)
    if not info:
        return None
    return round(info["price_per_mg"] * quantity_mg, 2)


def validate_order_locally(product: str, quantity_mg: float) -> list[str]:
    errors = []
    if product not in PRICING:
        if product.lower() != "custom":
            errors.append(f"Product '{product}' not in catalog. Available: {', '.join(PRICING.keys())}, Custom")
    else:
        min_mg = PRICING[product]["min_mg"]
        if quantity_mg < min_mg:
            errors.append(f"Minimum order for {product} is {min_mg}mg. Requested: {quantity_mg}mg")
    return errors


def process_order_request(lead_record_id: str, order_text: str) -> dict:
    """
    Main entry point. Takes a lead's Airtable record ID and their order message.
    Returns the created order record or an error dict.
    """
    # Fetch lead from Airtable
    lead = airtable.get_lead(lead_record_id)
    lead_fields = lead["fields"]

    if lead_fields.get("status") not in ("Qualified", "Converted"):
        return {
            "success": False,
            "error": f"Lead status is '{lead_fields.get('status')}'. Must be Qualified before placing an order.",
        }

    lead_context = (
        f"Lead: {lead_fields.get('name', '')}, "
        f"type={lead_fields.get('buyer_type', '')}, "
        f"email={lead_fields.get('email', '')}"
    )

    messages = [
        {
            "role": "user",
            "content": f"""Process this order request from a qualified lead.

{lead_context}

Order message:
\"{order_text}\"

Extract the product, quantity, calculate pricing, validate it, and provide a confirmation message.""",
        }
    ]

    response = claude.create(
        system=SYSTEM_PROMPT,
        messages=messages,
        max_tokens=1024,
    )

    response_text = ""
    for block in response.content:
        if hasattr(block, "text"):
            response_text += block.text

    order_data = _parse_order_json(response_text)

    if order_data.get("status") == "needs_info":
        return {
            "success": False,
            "needs_info": True,
            "message": order_data.get("confirmation_message", "We need more details about your order."),
        }

    if order_data.get("status") == "invalid":
        return {
            "success": False,
            "errors": order_data.get("validation_errors", []),
            "message": order_data.get("confirmation_message", "Order could not be validated."),
        }

    # Run local validation as a safety check
    product = order_data.get("product", "")
    quantity_mg = float(order_data.get("quantity_mg", 0))
    local_errors = validate_order_locally(product, quantity_mg)
    if local_errors:
        return {"success": False, "errors": local_errors}

    # Calculate or verify price
    total_price = order_data.get("total_price")
    if not total_price and product in PRICING:
        total_price = calculate_price(product, quantity_mg)

    # Write order to Airtable
    try:
        order_record = airtable.create_order(
            lead_id=lead_record_id,
            product=product,
            quantity_mg=quantity_mg,
            total_price=total_price or 0.0,
        )

        # Update lead status to Converted
        airtable.update_lead_status(
            lead_record_id,
            "Converted",
            notes=order_data.get("notes", ""),
        )

        print(f"[OrderIntakeAgent] Order created: {order_record['id']} — {product} {quantity_mg}mg @ ${total_price}")

        return {
            "success": True,
            "order_record_id": order_record["id"],
            "product": product,
            "quantity_mg": quantity_mg,
            "total_price": total_price,
            "confirmation_message": order_data.get("confirmation_message", ""),
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


def process_bulk_order(lead_record_id: str, line_items: list[dict]) -> list[dict]:
    """
    Process multiple products in one order session.
    Each item: {"product": "BPC-157", "quantity_mg": 500}
    """
    results = []
    for item in line_items:
        product = item.get("product", "")
        quantity_mg = float(item.get("quantity_mg", 0))

        errors = validate_order_locally(product, quantity_mg)
        if errors:
            results.append({"product": product, "success": False, "errors": errors})
            continue

        total_price = calculate_price(product, quantity_mg) or 0.0
        try:
            order = airtable.create_order(
                lead_id=lead_record_id,
                product=product,
                quantity_mg=quantity_mg,
                total_price=total_price,
            )
            results.append({
                "product": product,
                "success": True,
                "order_record_id": order["id"],
                "total_price": total_price,
            })
        except Exception as e:
            results.append({"product": product, "success": False, "error": str(e)})

    # Convert lead if at least one order succeeded
    if any(r["success"] for r in results):
        airtable.update_lead_status(lead_record_id, "Converted")

    return results


def _parse_order_json(text: str) -> dict:
    try:
        start = text.rfind("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            return json.loads(text[start:end])
    except (json.JSONDecodeError, ValueError):
        pass
    return {"status": "needs_info", "confirmation_message": "Could you clarify your order details?"}


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python order_intake_agent.py <lead_record_id> '<order message>'")
        sys.exit(1)

    lead_id = sys.argv[1]
    order_msg = sys.argv[2]

    result = process_order_request(lead_id, order_msg)
    print(json.dumps(result, indent=2))
