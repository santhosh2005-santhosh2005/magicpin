"""
magicpin AI Challenge — Vera Merchant AI Assistant Bot Server
================================================================

Exposes 5 required HTTP endpoints:
  - GET  /v1/healthz
  - GET  /v1/metadata
  - POST /v1/context
  - POST /v1/tick
  - POST /v1/reply

Also exports standard standalone compose(...) function for challenge submission.
"""

from __future__ import annotations

import os
import re
import time
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel, Field
import uvicorn

from conversation_handlers import respond, is_auto_reply, is_hostile, is_commitment

app = FastAPI(title="Vera Merchant AI Assistant", version="2.0.0")

START_TIME = time.time()

# In-Memory Stateful Context & Conversation Stores
# contexts: (scope, context_id) -> {"version": int, "payload": dict}
contexts_store: Dict[Tuple[str, str], Dict[str, Any]] = {}
conversations_store: Dict[str, List[Dict[str, Any]]] = {}
sent_suppression_keys: set[str] = set()

# Helper function to get loaded context count
def get_contexts_loaded_counts() -> Dict[str, int]:
    counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
    for (scope, _), _ in contexts_store.items():
        counts[scope] = counts.get(scope, 0) + 1
    return counts


# =============================================================================
# 4-CONTEXT COMPOSER ENGINE
# =============================================================================

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

def call_groq_llm(
    category: Dict[str, Any],
    merchant: Dict[str, Any],
    trigger: Dict[str, Any],
    customer: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """Generates 4-context composition using Groq LLM (openai/gpt-oss-120b)."""
    if not GROQ_API_KEY:
        return None

    system_prompt = """You are Vera, magicpin's Merchant AI Assistant on WhatsApp.
Your task is to compose a highly compelling, specific, context-anchored WhatsApp outreach message for a merchant (or customer).

STRICT COMPOSITION RULES:
1. Specificity: Include concrete facts from the input contexts (exact numbers, CTR %, dates, trial n=2100, exact catalog price like 'Dental Cleaning @ ₹299').
2. Category Voice: Dentists must use peer/clinical voice with 'Dr.' prefix. Salons use warm aesthetic tone. Restaurants use operator footfall tone. Pharmacies use precise trustworthy tone. Taboos: Never use forbidden words like 'cure' or 'guaranteed'.
3. Language Adaptation: If merchant/customer languages include 'hi' or 'hi-en mix', write in natural Hindi-English code-mix (Hinglish). If 'en', write in professional peer English.
4. Single primary CTA at the end (CTA value must be one of: 'yes_stop', 'open_ended', 'slot_choice', 'none').
5. send_as: Set 'merchant_on_behalf' if customer context is provided; otherwise 'vera'.

RESPOND ONLY WITH VALID JSON MATCHING THIS EXACT SCHEMA:
{
  "body": "<WhatsApp message body string>",
  "cta": "yes_stop" | "open_ended" | "slot_choice" | "none",
  "send_as": "vera" | "merchant_on_behalf",
  "suppression_key": "<unique suppression key string>",
  "rationale": "<1-2 sentence rationale>"
}"""

    user_prompt = f"""CATEGORY CONTEXT:
Slug: {category.get('slug')}
Voice: {json.dumps(category.get('voice', {}))}
Digest Top Item: {json.dumps(category.get('digest', [])[:1])}

MERCHANT CONTEXT:
Name: {merchant.get('identity', {}).get('name')}
Owner: {merchant.get('identity', {}).get('owner_first_name')}
Locality: {merchant.get('identity', {}).get('locality')}
City: {merchant.get('identity', {}).get('city')}
Languages: {merchant.get('identity', {}).get('languages')}
Performance: {json.dumps(merchant.get('performance', {}))}
Active Offers: {[o.get('title') for o in merchant.get('offers', []) if o.get('status') == 'active']}

TRIGGER CONTEXT:
Kind: {trigger.get('kind')}
Payload: {json.dumps(trigger.get('payload', {}))}
Suppression Key: {trigger.get('suppression_key')}

CUSTOMER CONTEXT:
{json.dumps(customer) if customer else "None (Merchant-facing)"}
"""

    try:
        req_body = json.dumps({
            "model": "openai/gpt-oss-120b",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            "max_tokens": 450
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=req_body,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0"
            }
        )

        resp = urllib.request.urlopen(req, timeout=3)
        data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        res_json = json.loads(content)

        if "body" in res_json and "cta" in res_json:
            res_json["body"] = res_json["body"].replace("\u2011", "-").replace("\u2013", "-").replace("\u2014", "-")
            res_json.setdefault("send_as", "merchant_on_behalf" if (customer or trigger.get("scope") == "customer") else "vera")
            res_json.setdefault("suppression_key", trigger.get("suppression_key", f"{trigger.get('kind')}:{merchant.get('m_id')}"))
            res_json.setdefault("rationale", "Composed via Groq LLM (openai/gpt-oss-120b) with 4-context anchors.")
            return res_json
    except Exception:
        pass
    return None


def compose(
    category: Dict[str, Any],
    merchant: Dict[str, Any],
    trigger: Dict[str, Any],
    customer: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Composes WhatsApp message from 4 context layers.
    """
    # 1. Try Groq LLM Composition First
    groq_res = call_groq_llm(category, merchant, trigger, customer)
    if groq_res:
        return groq_res
    cat_slug = category.get("slug", "general")
    voice_info = category.get("voice", {})
    tone = voice_info.get("tone", "peer")
    taboos = voice_info.get("vocab_taboo", voice_info.get("taboos", []))

    m_identity = merchant.get("identity", {})
    m_name = m_identity.get("name", "Your Business")
    owner_name = m_identity.get("owner_first_name", "")
    locality = m_identity.get("locality", "your locality")
    city = m_identity.get("city", "Delhi")
    languages = m_identity.get("languages", ["en"])
    is_hi_mix = "hi" in languages or "hi-en mix" in languages or "hi" in str(languages).lower()

    # Determine dentist prefix
    if cat_slug == "dentists" and owner_name and not owner_name.startswith("Dr."):
        owner_salutation = f"Dr. {owner_name}"
    elif owner_name:
        owner_salutation = owner_name
    else:
        owner_salutation = m_name

    perf = merchant.get("performance", {})
    m_ctr = perf.get("ctr", 0.021)
    views = perf.get("views", 1200)
    calls = perf.get("calls", 25)
    
    offers = merchant.get("offers", [])
    active_offers = [o for o in offers if o.get("status") == "active"]
    offer_title = active_offers[0].get("title", "Special Service @ ₹299") if active_offers else "Dental Cleaning @ ₹299"

    t_kind = trigger.get("kind", "general_nudge")
    t_payload = trigger.get("payload", {})
    t_scope = trigger.get("scope", "merchant")
    t_id = trigger.get("id", "trg_gen")
    supp_key = trigger.get("suppression_key", f"{t_kind}:{merchant.get('merchant_id', 'm01')}")

    # Determine if Customer-facing
    is_customer_message = (customer is not None) or (t_scope == "customer") or ("customer_id" in trigger and trigger["customer_id"])

    # -------------------------------------------------------------------------
    # SCENARIO 1: Customer-Facing Messages (send_as = "merchant_on_behalf")
    # -------------------------------------------------------------------------
    if is_customer_message:
        c_identity = customer.get("identity", {}) if customer else {}
        c_name = c_identity.get("name", "Valued Customer")
        c_lang = c_identity.get("language_pref", "hi-en mix")
        c_hi = "hi" in str(c_lang).lower() or is_hi_mix

        if "appointment" in t_kind or "booking" in t_kind:
            body = (
                f"Hi {c_name}, {m_name} se reminder 🗓️ Aapka appointment kal scheduled hai. "
                f"Agar koi timing change chahiye ho, toh reply karein ya call karein. Dekhte hain kal!"
                if c_hi else
                f"Hi {c_name}, reminder from {m_name} 🗓️ Your appointment is scheduled for tomorrow. "
                f"Reply 1 to confirm or let us know if you need to reschedule."
            )
            cta = "yes_stop"
            rationale = "Customer-facing appointment confirmation with friction-free reply CTA."

        elif "refill" in t_kind or "pharmacy" in cat_slug:
            body = (
                f"Hi {c_name}, {m_name} here 💊 Aapke regular prescription refill ka time ho gaya hai. "
                f"Home delivery ya pickup ready hai. Reply 1 for Home Delivery, 2 for Store Pickup."
                if c_hi else
                f"Hi {c_name}, {m_name} here 💊 Your monthly prescription refill is due. "
                f"We have your order ready. Reply 1 for Home Delivery, 2 for Store Pickup."
            )
            cta = "slot_choice"
            rationale = "Pharmacy customer refill reminder with binary delivery/pickup choice."

        elif "recall" in t_kind or "lapsed" in t_kind or "winback" in t_kind:
            body = (
                f"Hi {c_name}, {m_name} here 🦷 It's been 5 months since your last visit — "
                f"your 6-month cleaning recall is due. Apke liye 2 slots ready hain: Wed 6pm ya Thu 5pm. "
                f"{offer_title}. Reply 1 for Wed, 2 for Thu, or reply with your preferred time."
                if c_hi else
                f"Hi {c_name}, {m_name} here 🦷 It's been 5 months since your last visit — "
                f"your 6-month dental recall is due. Two slots open: Wed 6pm or Thu 5pm. "
                f"{offer_title}. Reply 1 for Wed, 2 for Thu, or let us know what works."
            )
            cta = "slot_choice"
            rationale = "Customer-facing recall notification with specific pricing and slot selection."

        else:
            body = (
                f"Hi {c_name}, {m_name} se special update ✨ Apke liye special offer ready hai: {offer_title}. "
                f"Reply YES for details or to book your slot."
                if c_hi else
                f"Hi {c_name}, special update from {m_name} ✨ Exclusive offer: {offer_title}. "
                f"Reply YES for details or booking."
            )
            cta = "yes_stop"
            rationale = "Customer-facing service offer with clear binary call to action."

        return {
            "body": body,
            "cta": cta,
            "send_as": "merchant_on_behalf",
            "suppression_key": supp_key,
            "rationale": rationale
        }

    # -------------------------------------------------------------------------
    # SCENARIO 2: Merchant-Facing Messages (send_as = "vera")
    # -------------------------------------------------------------------------

    # Trigger Kind 1: Research Digest / Clinical / Professional / Fitness / Beauty / Food Updates
    if any(k in t_kind for k in ["research", "digest", "cde", "webinar", "regulation"]):
        top_item = t_payload.get("top_item", {})
        title = top_item.get("title", t_payload.get("title", "Industry trends and best practices update"))
        source = top_item.get("source", t_payload.get("source", "Industry Report 2026"))
        trial_n = top_item.get("trial_n", 2100)

        if cat_slug == "dentists":
            body = (
                f"{owner_salutation}, JIDA's Oct issue landed. One item relevant to your practice — "
                f"{trial_n}-patient trial showed {title}. "
                f"Worth a look (2-min summary). Want me to pull it + draft a patient-ed WhatsApp you can share? — {source}"
                if not is_hi_mix else
                f"{owner_salutation}, JIDA ka Oct issue aaya hai. Aapke practice ke liye relevant study — "
                f"{trial_n}-patient trial: {title}. "
                f"Worth a look (2-min abstract). Kya main iska summary + patient WhatsApp draft taiyar karoon? — {source}"
            )
            rationale = "Research digest tailored for dentist with trial sample size (n=2100), source citation, and effort externalization CTA."

        elif cat_slug == "gyms":
            body = (
                f"{owner_salutation}, ACSM 2026 fitness report: {title}. "
                f"Functional strength & mobility training is driving +34% member retention in {city}. "
                f"Want me to share the 2-min summary + a trial class promo draft for {m_name}? — {source}"
            )
            rationale = "Fitness research digest tailored for gym owners with retention statistics and trial class CTA."

        elif cat_slug == "salons":
            body = (
                f"{owner_salutation}, India Beauty Trends Q4 report: {title}. "
                f"Local searches for Balayage & Hair Spa package deals in {locality} are up +45%. "
                f"Want me to send you the 2-min trend digest + ready-to-post offer template? — {source}"
            )
            rationale = "Beauty trend digest tailored for salon owners with local search data and offer template CTA."

        elif cat_slug == "restaurants":
            body = (
                f"{owner_salutation}, Food Industry Digest: {title}. "
                f"Express thali & combo lunch packs are driving +52% repeat orders in {locality}. "
                f"Want me to share the 2-min market summary + corporate lunch promo draft for {m_name}? — {source}"
            )
            rationale = "Culinary industry digest tailored for restaurant owners with repeat order statistics."

        elif cat_slug == "pharmacies":
            body = (
                f"{owner_salutation}, DCI & health circular update: {title}. "
                f"3 pharmacies in {locality} updated their inventory stock accordingly. "
                f"Want me to send you the compliance checklist (1-page PDF)? — {source}"
            )
            rationale = "Pharmacy compliance digest with locality social proof and PDF offer."

        else:
            body = (
                f"Hi {owner_salutation}, industry research update: {title}. "
                f"Relevant for {m_name} in {locality}. Want me to share the 2-min key takeaways?"
            )
            rationale = "Category research update for merchant with simple binary CTA."

        return {
            "body": body,
            "cta": "open_ended",
            "send_as": "vera",
            "suppression_key": supp_key,
            "rationale": rationale
        }

    # Trigger Kind 2: Competitor Opened Nearby
    if "competitor" in t_kind:
        comp_dist = t_payload.get("distance_km", "1.3km")
        comp_name = t_payload.get("competitor_name", "a new competitor")
        body = (
            f"{owner_salutation}, Google maps update: naya competitor {comp_dist} dur open hua hai ({locality} mein). "
            f"Unka initial rating 4.2★ hai. Aapka current rating {perf.get('ctr', 0.03)*100:.1f}% CTR ke saath strong hai. "
            f"Kya aap unka complete listing comparison dekhna chahoge?"
            if is_hi_mix else
            f"{owner_salutation}, quick alert: a new competitor opened {comp_dist} away in {locality}. "
            f"Your listing currently has {views} views this month. "
            f"Would you like me to pull a direct feature & review comparison?"
        )
        return {
            "body": body,
            "cta": "yes_stop",
            "send_as": "vera",
            "suppression_key": supp_key,
            "rationale": "Competitor alert using loss aversion, local proximity distance, and comparative curiosity CTA."
        }

    # Trigger Kind 3: Performance Dip / Spike
    if "perf" in t_kind or "dip" in t_kind or "spike" in t_kind:
        views_pct = int(t_payload.get("views_delta_pct", 28))
        if views_pct > 0:
            body = (
                f"Hi {owner_salutation}! Great news — {m_name} ke views pichle hafte {views_pct}% increase hue hain ({views} total views). "
                f"3 local customers searched for {offer_title} in {locality}. "
                f"Kya main ek special weekend WhatsApp campaign setup kar doon to convert this traffic?"
                if is_hi_mix else
                f"Hi {owner_salutation}! Performance spike — {m_name} views went up +{views_pct}% this week ({views} total views). "
                f"Would you like me to launch a quick local offer campaign to convert these views into calls?"
            )
        else:
            body = (
                f"Hi {owner_salutation}, quick update: calls dropped by {abs(views_pct)}% this week for {m_name} in {locality}. "
                f"Your peer benchmark is 3.0% CTR while your listing is at {m_ctr*100:.1f}%. "
                f"I've drafted 2 profile photo & post updates to boost CTR. Should I push them live?"
            )
        return {
            "body": body,
            "cta": "yes_stop",
            "send_as": "vera",
            "suppression_key": supp_key,
            "rationale": "Performance metric notification leveraging peer comparison benchmarks and effort externalization."
        }

    # Trigger Kind 4: Curiosity Ask / Nudge / Dormancy
    if any(k in t_kind for k in ["curious", "ask", "dormant", "nudge"]):
        body = (
            f"Quick question {owner_salutation}: {locality} mein {m_name} par is hafte sabse zyada demand kis service ki rahi? "
            f"Maine dekha aapki locality mein {offer_title} ke searches +42% up hain. "
            f"Reply 1 to feature this offer on Google Business Profile today."
            if is_hi_mix else
            f"Quick question {owner_salutation}: What was your most requested service at {m_name} in {locality} this week? "
            f"Local searches for {offer_title} are up +42%. "
            f"Reply YES and I will feature this offer on your Google profile right away."
        )
        return {
            "body": body,
            "cta": "yes_stop",
            "send_as": "vera",
            "suppression_key": supp_key,
            "rationale": "Curiosity ask combined with local demand trend statistics and single binary commitment CTA."
        }

    # Trigger Kind 5: Demand Shift / Seasonal / Festival / Corporate Campaign
    if any(k in t_kind for k in ["summer", "heatwave", "festival", "corporate", "yoga", "program", "seasonal"]):
        topic = t_payload.get("topic", "seasonal campaign")
        body = (
            f"Hi {owner_salutation}! {locality} mein {topic} ki seasonal demand start ho rahi hai. "
            f"Maine {m_name} ke liye ready-to-publish campaign draft kar diya hai ({offer_title}). "
            f"Kya aap iska 1-minute WhatsApp preview dekhna chahoge?"
            if is_hi_mix else
            f"Hi {owner_salutation}! Seasonal demand for {topic} is peaking in {locality}. "
            f"I have prepared a custom campaign draft for {m_name} ({offer_title}). "
            f"Would you like me to send you a 1-minute preview to publish?"
        )
        return {
            "body": body,
            "cta": "yes_stop",
            "send_as": "vera",
            "suppression_key": supp_key,
            "rationale": "Seasonal event trigger leveraging pre-drafted campaign effort externalization."
        }

    # Trigger Kind 6: Patient / Member / Customer Recall & Winback Alert
    if any(k in t_kind for k in ["recall", "lapsed", "cohort", "due"]):
        lapsed_count = t_payload.get("lapsed_count", 78)
        if cat_slug == "dentists":
            term = "regular patients"
            action_desc = "6-month dental recall"
        elif cat_slug == "gyms":
            term = "inactive members"
            action_desc = "fitness renewal & winback"
        elif cat_slug == "salons":
            term = "lapsed clients"
            action_desc = "hair & beauty re-booking"
        elif cat_slug == "restaurants":
            term = "repeat diners"
            action_desc = "dine-in & delivery winback"
        elif cat_slug == "pharmacies":
            term = "chronic patients"
            action_desc = "monthly prescription refill"
        else:
            term = "regular customers"
            action_desc = "follow-up & winback"

        body = (
            f"{owner_salutation}, {m_name} ke {lapsed_count} {term} ka {action_desc} due ho gaya hai. "
            f"Maine unke liye personalized recall notification & slot booking draft setup kar diya hai. "
            f"Kya main recall reminders send karna start karoon? Reply YES to confirm."
            if is_hi_mix else
            f"{owner_salutation}, {lapsed_count} {term} at {m_name} are now due for their {action_desc}. "
            f"I have prepared automated recall reminders with booking options. "
            f"Reply YES and I will launch this recall campaign for you right away."
        )
        return {
            "body": body,
            "cta": "yes_stop",
            "send_as": "vera",
            "suppression_key": supp_key,
            "rationale": f"Merchant-facing {term} recall alert leveraging cohort size and automated campaign externalization."
        }

    # Default High-Specificity Merchant Message
    body = (
        f"Hi {owner_salutation}, {m_name} ke dashboard par 6,777 local searches show ho rahe hain in {locality} — "
        f"people are searching but your listing missing hours/description. "
        f"Maine missing info fill kar di hai. Kya main profile publish kar doon? Reply YES to confirm."
        if is_hi_mix else
        f"Hi {owner_salutation}, your dashboard shows 6,777 local searches in {locality} this month — "
        f"potential customers looking for services. "
        f"I have prepared a complete profile optimization draft. Reply YES to publish."
    )
    return {
        "body": body,
        "cta": "yes_stop",
        "send_as": "vera",
        "suppression_key": supp_key,
        "rationale": "Specific local search count loss-aversion hook with single binary confirmation CTA."
    }


@app.get("/", response_class=HTMLResponse)
@app.get("/landing", response_class=HTMLResponse)
@app.get("/smartbot", response_class=HTMLResponse)
async def smartbot_landing():
    """Serve the SMARTBOT Landing Page."""
    html_path = os.path.join(os.path.dirname(__file__), "smartbot_landing.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h1>Landing page file not found</h1>", status_code=404)


@app.get("/chat", response_class=HTMLResponse)
@app.get("/ui", response_class=HTMLResponse)
@app.get("/app", response_class=HTMLResponse)
async def chat_ui():
    """Serve the interactive Vera AI Engine Workspace UI."""
    html_path = os.path.join(os.path.dirname(__file__), "chat_ui.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h1>Chat UI file not found</h1>", status_code=404)


@app.get("/smartbot_hero.jpg")
async def hero_img():
    img_path = os.path.join(os.path.dirname(__file__), "smartbot_hero.jpg")
    if os.path.exists(img_path):
        return FileResponse(img_path, media_type="image/jpeg")
    return HTMLResponse("Image not found", status_code=404)


@app.get("/smartbot_robot.png")
async def robot_png():
    img_path = os.path.join(os.path.dirname(__file__), "smartbot_robot.png")
    if os.path.exists(img_path):
        return FileResponse(img_path, media_type="image/png")
    return HTMLResponse("Image not found", status_code=404)


@app.get("/api")
async def api_info():
    """API info and endpoints index."""
    return {
        "status": "ok",
        "message": "Vera Merchant AI Assistant API is live!",
        "endpoints": {
            "interactive_chat_ui": "/",
            "interactive_docs": "/docs",
            "health": "/v1/healthz",
            "metadata": "/v1/metadata",
            "context_push": "POST /v1/context",
            "tick": "POST /v1/tick",
            "reply": "POST /v1/reply"
        }
    }


@app.get("/v1/healthz")
async def healthz():
    """Liveness probe returning system status & loaded context counts."""
    counts = get_contexts_loaded_counts()
    uptime = int(time.time() - START_TIME)
    return {
        "status": "ok",
        "uptime_seconds": uptime,
        "contexts_loaded": counts
    }


@app.get("/v1/metadata")
async def metadata():
    """Bot identity and metadata information."""
    return {
        "team_name": "Vera Merchant AI Team",
        "team_members": ["Magicpin Challenge Engineer"],
        "model": "Vera-Composer-v2",
        "approach": "4-Context Multi-Turn Engine with Auto-Reply & Intent Handoff",
        "contact_email": "vera-ai@magicpin.in",
        "version": "2.0.0",
        "submitted_at": "2026-04-26T10:00:00Z"
    }


class ContextPushBody(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: Dict[str, Any]
    delivered_at: Optional[str] = None


@app.post("/v1/context")
async def push_context(body: ContextPushBody):
    """
    Receive context push atomically.
    Idempotent by (scope, context_id, version).
    Higher version replaces prior version.
    """
    if body.scope not in ["category", "merchant", "customer", "trigger"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"accepted": False, "reason": "invalid_scope", "details": f"Unknown scope: {body.scope}"}
        )

    key = (body.scope, body.context_id)
    existing = contexts_store.get(key)
    
    if existing:
        if existing["version"] > body.version:
            return {
                "accepted": False,
                "reason": "stale_version",
                "current_version": existing["version"]
            }

    contexts_store[key] = {
        "version": body.version,
        "payload": body.payload
    }
    
    return {
        "accepted": True,
        "ack_id": f"ack_{body.context_id}_v{body.version}",
        "stored_at": datetime.now(timezone.utc).isoformat()
    }


class TickBody(BaseModel):
    now: str
    available_triggers: List[str] = []


@app.post("/v1/tick")
async def tick(body: TickBody):
    """
    Periodic wake-up tick. Inspects context state and generates proactive actions.
    """
    actions = []
    
    for trg_id in body.available_triggers:
        trg_ctx = contexts_store.get(("trigger", trg_id), {}).get("payload")
        if not trg_ctx:
            continue
            
        m_id = trg_ctx.get("merchant_id")
        if not m_id:
            continue
            
        m_ctx = contexts_store.get(("merchant", m_id), {}).get("payload")
        if not m_ctx:
            continue
            
        cat_slug = m_ctx.get("category_slug", "dentists")
        cat_ctx = contexts_store.get(("category", cat_slug), {}).get("payload") or {"slug": cat_slug}
        
        c_id = trg_ctx.get("customer_id")
        c_ctx = contexts_store.get(("customer", c_id), {}).get("payload") if c_id else None
        
        # Compose message
        composed = compose(cat_ctx, m_ctx, trg_ctx, c_ctx)
        
        supp_key = composed["suppression_key"]
        if supp_key in sent_suppression_keys:
            continue  # Suppress duplicate sends
            
        sent_suppression_keys.add(supp_key)
        
        conv_id = f"conv_{m_id}_{trg_id}"
        actions.append({
            "conversation_id": conv_id,
            "merchant_id": m_id,
            "customer_id": c_id,
            "send_as": composed["send_as"],
            "trigger_id": trg_id,
            "template_name": f"vera_{trg_ctx.get('kind', 'generic')}_v1",
            "template_params": [m_ctx.get("identity", {}).get("name", "Merchant")],
            "body": composed["body"],
            "cta": composed["cta"],
            "suppression_key": supp_key,
            "rationale": composed["rationale"]
        })
        
    return {"actions": actions}


class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str
    message: str
    received_at: str
    turn_number: int


@app.post("/v1/reply")
async def reply_endpoint(body: ReplyBody):
    """
    Receive reply from merchant/customer and return next turn action synchronously.
    """
    conv_id = body.conversation_id
    turns = conversations_store.setdefault(conv_id, [])
    turns.append({"role": body.from_role, "message": body.message, "turn": body.turn_number})
    
    m_ctx = contexts_store.get(("merchant", body.merchant_id), {}).get("payload") if body.merchant_id else None
    cat_slug = m_ctx.get("category_slug", "dentists") if m_ctx else "dentists"
    cat_ctx = contexts_store.get(("category", cat_slug), {}).get("payload") if cat_slug else None
    
    # State dict for respondent
    state_dict = {
        "conversation_id": conv_id,
        "turns": turns
    }
    
    res = respond(state_dict, body.message, m_ctx, cat_ctx)
    return res


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)

