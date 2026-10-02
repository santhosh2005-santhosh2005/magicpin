from __future__ import annotations
import os
import re
import json
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional, Tuple

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

def call_groq_reply(
    previous_turns: List[Dict[str, Any]],
    merchant_message: str,
    merchant_context: Optional[Dict[str, Any]],
    category_context: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Generates multi-turn reply using Groq LLM (openai/gpt-oss-120b)."""
    if not GROQ_API_KEY:
        return None

    m_name = merchant_context.get("identity", {}).get("name", "Merchant") if merchant_context else "Merchant"
    owner_name = merchant_context.get("identity", {}).get("owner_first_name", "") if merchant_context else ""

    system_prompt = f"""You are Vera, magicpin's Merchant AI Assistant on WhatsApp.
You are talking to merchant owner '{owner_name or m_name}' ({m_name}).

STRICT CONVERSATIONAL RULES:
1. If merchant expresses commitment/agreement ('ok lets do it', 'yes', 'send details'), switch IMMEDIATELY to ACTION execution mode ('action': 'send') and confirm action initiation. DO NOT re-ask qualifying questions.
2. If merchant asks a question (timeframe, cost, details), answer directly with concrete numbers (24-48 hours on Google, ₹299 catalog price).
3. If merchant asks to stop or shows hostility, set 'action': 'end' and apologize politely.
4. Match natural Hinglish code-mix if merchant uses Hindi/English.

RESPOND ONLY WITH VALID JSON:
{{
  "action": "send" | "wait" | "end",
  "body": "<message string>",
  "cta": "yes_stop" | "open_ended" | "none",
  "rationale": "<1-2 sentence rationale>"
}}"""

    try:
        messages = [{"role": "system", "content": system_prompt}]
        for turn in previous_turns[-6:]:
            role = "assistant" if turn.get("role") == "vera" else "user"
            messages.append({"role": role, "content": turn.get("message", "")})
        messages.append({"role": "user", "content": merchant_message})

        req_body = json.dumps({
            "model": "openai/gpt-oss-120b",
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            "max_tokens": 400
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

        if "action" in res_json and "body" in res_json:
            res_json["body"] = res_json["body"].replace("\u2011", "-").replace("\u2013", "-").replace("\u2014", "-")
            res_json.setdefault("cta", "open_ended")
            res_json.setdefault("rationale", "Composed via Groq LLM (openai/gpt-oss-120b)")
            return res_json
    except Exception:
        pass
    return None

AUTO_REPLY_PATTERNS = [
    r"thank you for contacting",
    r"our team will respond",
    r"aapki jaankari ke liye",
    r"sujhaav hamari team tak",
    r"automated assistant",
    r"auto-reply",
    r"automated message",
    r"thank you for reaching out",
    r"we have received your message",
    r"out of office",
    r"will get back to you",
    r"auto-generated",
    r"automated response"
]

HOSTILE_PATTERNS = [
    r"\bstop\b",
    r"\bspam\b",
    r"don't message",
    r"dont message",
    r"not interested",
    r"unsubscribe",
    r"remove my number",
    r"leave me alone",
    r"block",
    r"shut up",
    r"useless",
    r"fake",
    r"do not contact"
]

COMMITMENT_PATTERNS = [
    r"\b(yes|yeah|yep|sure|ok|okay)\b",
    r"let'?s do it",
    r"what'?s next",
    r"send me",
    r"go ahead",
    r"update my profile",
    r"judrna hai",
    r"\bjoin\b",
    r"proceed",
    r"draft it",
    r"\bdo it\b",
    r"show me",
    r"please check",
    r"sounds good",
    r"interested"
]

class ConversationState:
    """Tracks state for an ongoing conversation."""
    def __init__(self, conversation_id: str, merchant_id: Optional[str] = None, customer_id: Optional[str] = None):
        self.conversation_id = conversation_id
        self.merchant_id = merchant_id
        self.customer_id = customer_id
        self.turns: List[Dict[str, Any]] = []
        self.auto_reply_count: int = 0
        self.last_merchant_msg: str = ""
        self.current_mode: str = "pitch"  # "pitch", "qualifying", "action", "ended"

    def add_turn(self, role: str, message: str):
        self.turns.append({"role": role, "message": message})

def is_auto_reply(message: str, previous_messages: List[str]) -> Tuple[bool, bool]:
    """Check if message matches known auto-reply patterns or is duplicate of a previous turn."""
    msg_lower = message.lower().strip()
    for pattern in AUTO_REPLY_PATTERNS:
        if re.search(pattern, msg_lower):
            return True, False
    
    # Check for repeated exact duplicate in previous merchant messages (3+ occurrences)
    duplicate_count = sum(1 for m in previous_messages if m.lower().strip() == msg_lower)
    if duplicate_count >= 2:
        return True, True
        
    return False, False

def is_hostile(message: str) -> bool:
    """Check if message expresses hostility or opt-out desire."""
    msg_lower = message.lower().strip()
    for pattern in HOSTILE_PATTERNS:
        if re.search(pattern, msg_lower):
            return True
    return False

def is_commitment(message: str) -> bool:
    """Check if message indicates agreement/intent commitment."""
    msg_lower = message.lower().strip()
    for pattern in COMMITMENT_PATTERNS:
        if re.search(pattern, msg_lower):
            return True
    return False

def respond(
    state_or_dict: Dict[str, Any] | ConversationState,
    merchant_message: str,
    merchant_context: Optional[Dict[str, Any]] = None,
    category_context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Given conversation state & merchant's latest message, produce the next action:
    Returns dict: {"action": "send"|"wait"|"end", "body": ..., "cta": ..., "rationale": ...}
    """
    if isinstance(state_or_dict, dict):
        conv_id = state_or_dict.get("conversation_id", "conv_default")
        previous_turns = state_or_dict.get("turns", [])
        # Exclude the very last turn (which is current merchant_message)
        prev_merchant_msgs = [
            t.get("message", "") for t in previous_turns[:-1] 
            if t.get("role") == "merchant"
        ]
    else:
        conv_id = state_or_dict.conversation_id
        prev_merchant_msgs = [
            t["message"] for t in state_or_dict.turns[:-1] 
            if t["role"] == "merchant"
        ]

    msg_lower = merchant_message.lower().strip()

    # 1. Hostile / Opt-out Detection
    if is_hostile(merchant_message):
        return {
            "action": "end",
            "body": "Samajh gayi. Aapki preference update kar di hai aur aage se koi message nahi aayega. Business ke liye best wishes!",
            "cta": "none",
            "rationale": "Merchant requested opt-out/stop. Respecting preference and ending conversation gracefully."
        }

    # 2. Auto-reply Detection
    is_auto, is_repeat = is_auto_reply(merchant_message, prev_merchant_msgs)
    if is_auto:
        return {
            "action": "end",
            "body": "Samajh gayi. Automated response detected — main owner/manager se directly connect kar lungi. Best wishes!",
            "cta": "none",
            "rationale": "Detected automated WhatsApp Business reply. Gracefully ending conversation to prevent turn pollution."
        }

    # Try Groq LLM Multi-Turn Reply
    groq_reply = call_groq_reply(previous_turns, merchant_message, merchant_context, category_context)
    if groq_reply:
        return groq_reply

    # 3. Intent Commitment Transition (Pitch/Qualifying -> Action)
    if is_commitment(merchant_message):
        m_name = merchant_context.get("identity", {}).get("name", "Aapka business") if merchant_context else "Aapka business"
        owner_name = merchant_context.get("identity", {}).get("owner_first_name", "") if merchant_context else ""
        greeting = f"Hi {owner_name}, " if owner_name else "Hi, "
        
        return {
            "action": "send",
            "body": f"{greeting}Done! Maine action initiate kar diya hai. {m_name} ke liye initial updates & optimization publish ho rahe hain (takes 24-48 hours on Google). Main aapko dashboard link & instant preview WhatsApp par bhej rahi hoon.",
            "cta": "open_ended",
            "rationale": "Merchant expressed explicit commitment. Immediately transitioned from pitch/qualifying mode to ACTION execution mode."
        }

    # 4. Standard Responsive Turn / Information Request
    if "?" in merchant_message or any(w in msg_lower for w in ["how", "when", "what", "where", "price", "cost", "kab", "detail", "details", "info"]):
        return {
            "action": "send",
            "body": "Zaroor! Main aapke business profile aur active campaign ki complete breakdown share kar rahi hoon: Hamara automated WhatsApp outreach active recall patients ko targeted slots offer karega with zero manual effort from your staff. Kya main ye flow activate kar doon? Reply YES to confirm.",
            "cta": "yes_stop",
            "rationale": "Directly answered information request with concrete workflow explanation and actionable next CTA."
        }

    # Default polite continuation
    return {
        "action": "send",
        "body": "Bilkul! Main aapke profile data ke according sari details ready kar ke update kar deti hoon. Agar koi specific offer change karna ho to mujhe batayein.",
        "cta": "open_ended",
        "rationale": "Acknowledged merchant input and proposed proactive offer management."
    }
