"""
Trade-In Agent — agent definitions and function tools.

Five specialised agents:
  1. Email Intake Agent   — parse + completeness gate
  2. Stock Agent          — desired car availability + alternatives
  3. Valuation Agent      — trade-in value range (lower/upper bound)
  4. Scheduling Agent     — free appointment slots (read-only, no allocation)
  5. Reply Agent          — customer-facing reply draft

Usage:
    python agents.py          # smoke test: create agents and process one email
    (full batch: see workflow.py)

The implementation follows the Microsoft Foundry / Azure AI Agents SDK pattern
from the FrontierWeekHack labs: agents are created with PromptAgentDefinition,
grounded with FunctionTool definitions, and run via a conversation + function
call loop.
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import FunctionTool, PromptAgentDefinition
from azure.identity import DefaultAzureCredential
from openai.types.responses.response_input_param import FunctionCallOutput

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")

PROJECT_CONNECTION_STRING = os.getenv("PROJECT_CONNECTION_STRING")
MODEL_DEPLOYMENT_NAME = os.getenv("MODEL_DEPLOYMENT_NAME", "gpt-5.4")

# Deterministic demo settings (fixed reference date keeps runs reproducible)
DEMO_TODAY = datetime(2026, 9, 17)
BUSINESS_START_HOUR = 9
BUSINESS_END_HOUR = 17
APPOINTMENT_DURATION_MIN = 60      # fixed assumption for the prototype
SLOTS_TO_PROPOSE = 3
LOOKAHEAD_DAYS = 7

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _load(filename):
    with open(DATA_DIR / filename, "r", encoding="utf-8") as f:
        return json.load(f)


# =============================================================================
# Tool implementations (deterministic — no LLM logic here)
# =============================================================================

def check_inventory(make: str, model: str, trim: str = None) -> str:
    """Check whether the desired car is in dealership stock. Returns JSON."""
    data = _load("inventory.json")
    matches = [
        c for c in data["cars"]
        if c["make"].lower() == make.lower() and c["model"].lower() == model.lower()
    ]
    if trim:
        exact = [c for c in matches if trim.lower() in c["trim"].lower()]
        if exact:
            matches = exact
    if not matches:
        return json.dumps({
            "make": make,
            "model": model,
            "status": "not_found",
            "note": "No matching car in stock; suggest_alternatives should be called next."
        })
    available = [c for c in matches if c["status"] == "available"]
    if available:
        car = available[0]
        return json.dumps({
            "make": car["make"], "model": car["model"], "trim": car["trim"],
            "year": car["year"], "mileage_km": car["mileage_km"],
            "price_eur": car["price_eur"], "status": "available",
            "listing_url": car["listing_url"]
        })
    car = matches[0]
    return json.dumps({
        "make": car["make"], "model": car["model"], "trim": car["trim"],
        "status": "sold",
        "note": "Matching car found but already sold; suggest_alternatives should be called next."
    })


def suggest_alternatives(make: str, model: str) -> str:
    """Suggest up to 3 available alternatives (same make preferred). Returns JSON."""
    data = _load("inventory.json")
    available = [c for c in data["cars"] if c["status"] == "available"]
    same_make = [c for c in available if c["make"].lower() == make.lower()]
    picked = same_make[:3]
    for c in available:
        if len(picked) >= 3:
            break
        if c not in picked:
            picked.append(c)
    return json.dumps({
        "requested": make + " " + model,
        "alternatives": [
            {"make": c["make"], "model": c["model"], "trim": c["trim"],
             "year": c["year"], "price_eur": c["price_eur"],
             "listing_url": c["listing_url"]}
            for c in picked
        ]
    })


def rdw_lookup(license_plate: str) -> str:
    """Look up vehicle facts by license plate (mock of RDW Open Data SODA API)."""
    data = _load("rdw_lookup.json")
    for v in data["vehicles"]:
        if v["license_plate"].upper().replace("-", "") == license_plate.upper().replace("-", ""):
            return json.dumps({"status": "found", "vehicle": v})
    return json.dumps({
        "status": "not_found",
        "license_plate": license_plate,
        "note": "Plate not registered in RDW mock; fall back to email data if complete."
    })


def lookup_value_range(make: str, model: str, year_of_manufacture: int,
                       mileage_km: int, fuel_type: str = None,
                       transmission_type: str = None) -> str:
    """Look up indicative trade-in value range from the valuation guide. Returns JSON."""
    data = _load("valuation_guide.json")
    for e in data["entries"]:
        if e["make"].lower() != make.lower() or e["model"].lower() != model.lower():
            continue
        if not (e["year_min"] <= year_of_manufacture <= e["year_max"]):
            continue
        if not (e["mileage_min_km"] <= mileage_km <= e["mileage_max_km"]):
            continue
        if fuel_type and e["fuel_type"].lower() != fuel_type.lower():
            continue
        if transmission_type and e["transmission_type"].lower() != transmission_type.lower():
            continue
        return json.dumps({
            "status": "found",
            "make": e["make"], "model": e["model"],
            "year_of_manufacture": year_of_manufacture,
            "mileage_km": mileage_km,
            "lower_bound_eur": e["lower_bound_eur"],
            "upper_bound_eur": e["upper_bound_eur"],
            "source": data["source"]
        })
    return json.dumps({
        "status": "not_found",
        "note": "Vehicle not covered by valuation guide; on-site valuation required."
    })


def find_available_slots() -> str:
    """Compute free 60-minute slots, next 7 days, 09:00-17:00. Read-only, no allocation."""
    cal = _load("calendar.json")
    busy = {}
    for a in cal["appointments"]:
        busy.setdefault(a["date"], []).append((a["start_time"], a["end_time"]))

    slots = []
    for day_offset in range(1, LOOKAHEAD_DAYS + 1):
        day = DEMO_TODAY + timedelta(days=day_offset)
        date_str = day.strftime("%Y-%m-%d")
        if day.weekday() >= 5:  # skip weekends (dealership closed)
            continue
        for hour in range(BUSINESS_START_HOUR, BUSINESS_END_HOUR):
            start = f"{hour:02d}:00"
            end = f"{hour + 1:02d}:00"
            overlap = False
            for (b_start, b_end) in busy.get(date_str, []):
                if start < b_end and end > b_start:
                    overlap = True
                    break
            if not overlap:
                slots.append({"date": date_str, "start_time": start, "end_time": end})
            if len(slots) >= SLOTS_TO_PROPOSE:
                break
        if len(slots) >= SLOTS_TO_PROPOSE:
            break

    if not slots:
        return json.dumps({"status": "no_slots",
                           "note": "No free slots in the coming week."})
    return json.dumps({"status": "ok", "proposed_slots": slots,
                       "note": "Slots are proposals only; no calendar allocation is made."})


# =============================================================================
# Tool schema definitions (Foundry FunctionTool format)
# =============================================================================

CHECK_INVENTORY_TOOL = FunctionTool(
    name="check_inventory",
    description="Check whether the desired car (the car the customer wants to buy) is in dealership stock. Returns status available/sold/not_found plus price and listing URL.",
    parameters={
        "type": "object",
        "properties": {
            "make": {"type": "string", "description": "Make, e.g. 'Volkswagen'"},
            "model": {"type": "string", "description": "Model, e.g. 'Golf'"},
            "trim": {"type": "string", "description": "Optional trim/version, e.g. '1.5 TSI Comfortline'"},
        },
        "required": ["make", "model"],
        "additionalProperties": False,
    },
    strict=False,
)

SUGGEST_ALTERNATIVES_TOOL = FunctionTool(
    name="suggest_alternatives",
    description="Suggest up to 3 available alternatives from stock for a desired car that is not available.",
    parameters={
        "type": "object",
        "properties": {
            "make": {"type": "string", "description": "Make of the requested car"},
            "model": {"type": "string", "description": "Model of the requested car"},
        },
        "required": ["make", "model"],
        "additionalProperties": False,
    },
    strict=False,
)

RDW_LOOKUP_TOOL = FunctionTool(
    name="rdw_lookup",
    description="Look up official vehicle facts (make, model, year, fuel, transmission, APK) by Dutch license plate via RDW Open Data. Use when the customer provides a license plate.",
    parameters={
        "type": "object",
        "properties": {
            "license_plate": {"type": "string", "description": "Dutch license plate, e.g. 'X-123-AB'"},
        },
        "required": ["license_plate"],
        "additionalProperties": False,
    },
    strict=False,
)

LOOKUP_VALUE_RANGE_TOOL = FunctionTool(
    name="lookup_value_range",
    description="Look up the indicative trade-in value range (lower and upper bound in EUR) for a trade-in car from the valuation guide. Never state a value outside the tool result.",
    parameters={
        "type": "object",
        "properties": {
            "make": {"type": "string", "description": "Make of the trade-in car"},
            "model": {"type": "string", "description": "Model of the trade-in car"},
            "year_of_manufacture": {"type": "integer", "description": "Year of manufacture (e.g. 2018)"},
            "mileage_km": {"type": "integer", "description": "Current mileage in kilometres"},
            "fuel_type": {"type": "string", "description": "Optional fuel type: petrol/diesel/hybrid/electric"},
            "transmission_type": {"type": "string", "description": "Optional transmission: manual/automatic"},
        },
        "required": ["make", "model", "year_of_manufacture", "mileage_km"],
        "additionalProperties": False,
    },
    strict=False,
)

FIND_AVAILABLE_SLOTS_TOOL = FunctionTool(
    name="find_available_slots",
    description="Find free appointment slots for the coming week (Mon-Fri, 09:00-17:00, 60 minutes). Read-only: slots are proposals, no calendar booking is made.",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
    strict=False,
)


# Tool dispatch registry: maps tool names to Python callables
TOOL_REGISTRY = {
    "check_inventory": lambda args: check_inventory(
        args["make"], args["model"], args.get("trim")),
    "suggest_alternatives": lambda args: suggest_alternatives(
        args["make"], args["model"]),
    "rdw_lookup": lambda args: rdw_lookup(args["license_plate"]),
    "lookup_value_range": lambda args: lookup_value_range(
        args["make"], args["model"], args["year_of_manufacture"],
        args["mileage_km"], args.get("fuel_type"), args.get("transmission_type")),
    "find_available_slots": lambda args: find_available_slots(),
}


# =============================================================================
# Generic agent base class
# =============================================================================

class BaseAgent:
    """Base class: creates a Foundry agent, runs it with a function-call loop."""

    agent_name = "base-agent"
    system_prompt = ""
    tools = []

    def __init__(self):
        self.agent = None
        self.client = None
        self.openai = None

    def create(self):
        self.client = AIProjectClient(
            endpoint=PROJECT_CONNECTION_STRING,
            credential=DefaultAzureCredential(),
        )
        self.openai = self.client.get_openai_client()
        self.agent = self.client.agents.create_version(
            agent_name=self.agent_name,
            definition=PromptAgentDefinition(
                model=MODEL_DEPLOYMENT_NAME,
                instructions=self.system_prompt,
                tools=self.tools,
            ),
        )
        return self.agent

    def run(self, input_text: str) -> str:
        conversation = self.openai.conversations.create()
        extra_body = {"agent_reference": {"name": self.agent.name, "type": "agent_reference"}}
        response = self.openai.responses.create(
            input=input_text, conversation=conversation.id, extra_body=extra_body)

        while True:
            function_calls = [item for item in response.output if item.type == "function_call"]
            if not function_calls:
                break
            input_list = []
            for item in function_calls:
                if item.name in TOOL_REGISTRY:
                    result = TOOL_REGISTRY[item.name](json.loads(item.arguments))
                else:
                    result = json.dumps({"error": "Unknown tool '" + item.name + "'"})
                input_list.append(FunctionCallOutput(
                    type="function_call_output", call_id=item.call_id, output=result))
            response = self.openai.responses.create(
                input=input_list, conversation=conversation.id, extra_body=extra_body)

        self.openai.conversations.delete(conversation_id=conversation.id)
        return response.output_text

    def cleanup(self):
        if self.agent:
            self.client.agents.delete_version(
                agent_name=self.agent.name, agent_version=self.agent.version)
        if self.client:
            self.client.close()


# =============================================================================
# The five specialised agents
# =============================================================================

class EmailIntakeAgent(BaseAgent):
    agent_name = "email-intake-agent"
    tools = []

    system_prompt = """
    You are an email intake specialist for a car dealership.
    Your job: turn an incoming trade-in request email into a structured JSON object.

    ALWAYS identify TWO vehicles:
    - desired_car: the car the customer wants to BUY from the dealership
    - trade_in_car: the customer's OWN car they want to trade in

    Never invent values. If a field is not stated in the email, set it to null
    and add its name to completeness.missing_fields.

    A request is sufficient for valuation (completeness.is_sufficient_for_valuation)
    only if the trade_in_car has at least:
    - make AND model
    - (year_of_manufacture OR license_plate)
    - mileage_km

    fuel_type and transmission_type are optional fields (an RDW lookup can supply
    them when a license plate is given).

    Respond with ONLY this JSON structure, no other text:
    {
      "sender_name": string | null,
      "sender_email": string | null,
      "desired_car": {
        "make": string | null,
        "model": string | null,
        "trim": string | null,
        "found_in_email": boolean
      },
      "trade_in_car": {
        "license_plate": string | null,
        "make": string | null,
        "model": string | null,
        "year_of_manufacture": integer | null,
        "mileage_km": integer | null,
        "fuel_type": string | null,
        "transmission_type": string | null
      },
      "completeness": {
        "is_sufficient_for_valuation": boolean,
        "missing_fields": [string]
      }
    }
    """


class StockAgent(BaseAgent):
    agent_name = "stock-agent"
    tools = [CHECK_INVENTORY_TOOL, SUGGEST_ALTERNATIVES_TOOL]

    system_prompt = """
    You are a stock agent for a car dealership.
    Given the desired_car from a customer request (make, model, optional trim):
    1. Always call check_inventory first.
    2. If the result is 'not_found' or 'sold', call suggest_alternatives.
    3. Summarise the availability result and any alternatives in a compact
       structured summary. State only facts returned by the tools.
    """


class ValuationAgent(BaseAgent):
    agent_name = "valuation-agent"
    tools = [RDW_LOOKUP_TOOL, LOOKUP_VALUE_RANGE_TOOL]

    system_prompt = """
    You are a vehicle valuation agent for a car dealership.
    Given the trade_in_car data from a customer request:
    1. If a license plate is available, call rdw_lookup and prefer its facts
       over email data (note discrepancies).
    2. Then call lookup_value_range with the best available make, model,
       year_of_manufacture, mileage_km (plus fuel_type/transmission_type if known).
    3. Report the lower and upper bound exactly as returned by the tool.

    STRICT RULES:
    - NEVER state a value outside the tool result. You must not estimate.
    - If lookup_value_range returns 'not_found', say that an on-site valuation
      during the appointment is required.
    - If rdw_lookup fails but email data is complete, proceed with email data.
    - If essential data is missing, report which fields are missing instead of
      guessing.
    """


class SchedulingAgent(BaseAgent):
    agent_name = "scheduling-agent"
    tools = [FIND_AVAILABLE_SLOTS_TOOL]

    system_prompt = """
    You are a scheduling agent for a car dealership.
    Call find_available_slots to retrieve free appointment slots for the coming
    week (Mon-Fri, 09:00-17:00, 60 minutes). Summarise the proposed slots.
    Emphasise that slots are proposals only: the customer must confirm by
    replying or calling the dealership. If no slots are available, say the
    dealership will contact the customer to schedule.
    """


class ReplyAgent(BaseAgent):
    agent_name = "reply-agent"
    tools = []

    system_prompt = """
    You are a customer service reply writer for a car dealership.
    You receive: the structured intake result, the stock result, the valuation
    result and the scheduling result for one customer request.

    Write a friendly, professional reply email in English. Adapt to the path:
    - Car available: confirm availability (with price) and invite a visit/test drive.
    - Car not available or sold: apologise, offer the alternatives from stock.
    - Valuation present: state the indicative trade-in value range (lower/upper
      bound) and ALWAYS add that it is indicative and subject to on-site
      inspection.
    - Valuation missing or insufficient data: kindly ask for the missing fields
      instead of giving a value.
    - Slots present: propose the appointment slots and ask the customer to
      confirm.
    - No slots: say the dealership will contact them to schedule.

    STRICT RULES:
    - Use ONLY facts provided in the input. Never invent prices, dates,
      availability or vehicle details.
    - Keep the email under 200 words. Include a subject line.
    """


# =============================================================================
# Smoke test
# =============================================================================

def main():
    if not PROJECT_CONNECTION_STRING:
        print("PROJECT_CONNECTION_STRING not set. Copy .env.example to .env first.")
        sys.exit(1)

    print("=== Trade-In Agent smoke test ===")
    intake = EmailIntakeAgent()
    intake.create()
    print("Created:", intake.agent.name)
    result = intake.run(
        "Subject: Trade-in question Golf
"
        "Hi, I saw a Volkswagen Golf 1.5 TSI Comfortline on your website. "
        "I would like to trade in my 2018 Opel Astra with 125,000 km. "
        "License plate X-123-AB. Best regards, Jan Dijkstra")
    print(result)
    # Keep agent for inspection in the Foundry portal; uncomment to clean up:
    # intake.cleanup()


if __name__ == "__main__":
    main()
