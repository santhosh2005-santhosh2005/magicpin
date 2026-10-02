# magicpin AI Challenge — Vera Merchant AI Assistant ("Vera")

**Submission Team**: Vera Merchant AI Team  
check it out : https://magicpin-sz07.onrender.com/
**Model**: Vera-Composer-v2 (4-Context Composition & Multi-Turn Engine)  
**Last Updated**: 2026-10-01  

---

## 1. Executive Summary

This project implements **Vera**, magicpin's next-generation AI assistant for local merchants over WhatsApp. Built on a clean, scalable **4-Context Composition Framework** (`compose(category, merchant, trigger, customer?)`), Vera resolves production Vera's primary pain points:

1. **Auto-Reply Elimination**: Automatically detects canned WhatsApp Business responses and prevents turn/cost pollution by gracefully exiting or routing to direct owner checks.
2. **Instant Intent Handoff**: When a merchant expresses commitment (*"I want to join"*, *"let's do it"*, *"go ahead"*), Vera transitions immediately into **Action Execution Mode** without re-asking qualification questions.
3. **Specific, Verifiable Copy**: Replaces generic discounts with category-specific service+price anchors (*"Dental Cleaning @ ₹299"*, *"Haircut @ ₹99"*), local proximity stats (*"6,777 missed searches in Sector 14"*), and peer trial citations (*"JIDA Oct 2026: 2,100-patient trial"*).
4. **Natural Code-Mixing**: Matches merchant language preferences seamlessly (Hinglish code-mix for Indian merchants, professional peer English for clinical/corporate accounts).

---

## 2. Architecture & Design

### 2.1 The 4-Context Framework

Every message composed by Vera combines four structured context layers:

```
CategoryContext   ──────┐
MerchantContext   ──────┼───►  compose(...)  ───►  ComposedMessage {body, cta, send_as, suppression_key, rationale}
TriggerContext    ──────┤
CustomerContext?  ──────┘
```

- **CategoryContext**: Vertical voice rules, clinical/peer taboos, service+price catalogs, peer statistics benchmarks, research digests, and seasonal beats across 5 verticals (`dentists`, `salons`, `restaurants`, `gyms`, `pharmacies`).
- **MerchantContext**: Real-time performance snapshots (views, calls, CTR vs peer average), active/expired catalog offers, locality details, and language preferences.
- **TriggerContext**: The event prompting outreach (`research_digest`, `recall_due`, `competitor_opened`, `perf_spike`, `curious_ask`, `seasonal_demand`).
- **CustomerContext**: populated when `send_as="merchant_on_behalf"`, driving personalized recall reminders, appointment confirmations, and prescription refills with slot selections.

### 2.2 Endpoints Implemented in `bot.py`

The candidate HTTP server is implemented with FastAPI/Uvicorn, exposing all 5 mandatory specifications:

- `GET /v1/healthz`: Liveness probe with uptime and loaded context counter.
- `GET /v1/metadata`: Team identity, model specs, approach summary, and versioning.
- `POST /v1/context`: Idempotent atomic context push by `(scope, context_id, version)`.
- `POST /v1/tick`: Periodic wake-up tick that evaluates active triggers and composes proactive messages.
- `POST /v1/reply`: Multi-turn dialogue endpoint driving stateful merchant/customer conversations.

---

## 3. Multi-Turn Handling & Edge Cases

Implemented in `conversation_handlers.py`:

- **Auto-Reply Detection**: Matches automated WhatsApp patterns (*"Thank you for contacting us..."*, *"Aapki jaankari ke liye..."*) and duplicate responses to exit gracefully (`action: "end"`).
- **Intent Handoff**: Detects commitment keywords (*"yes"*, *"ok lets do it"*, *"whats next"*, *"proceed"*) and immediately confirms execution.
- **Hostility Mitigation**: Detects opt-out requests (*"stop messaging"*, *"useless spam"*) and exits politely without further outreach.

---

## 4. Compulsion Levers Applied

1. **Specificity & Verifiability**: Concrete dates, trial sizes ($n=2,100$), search volumes ($6,777$), and exact prices ($₹299$).
2. **Loss Aversion**: Highlighting missed local search traffic and uncaptured demand.
3. **Social Proof**: Comparing listing metrics against locality medians ($3.0\%$ CTR average).
4. **Effort Externalization**: Pre-drafting campaigns (*"I've prepared the 1-min preview — reply 1 to publish"*).
5. **Single Binary CTA**: Reply `1`/`YES` or slot choices to minimize decision friction.

---

## 5. Evaluation Results

Evaluated against `judge_simulator.py`:

- **Warmup & Health Checks**: `PASS` (100%)
- **Auto-Reply Detection**: `PASS` (100%)
- **Intent Transition**: `PASS` (100%)
- **Hostile Handling**: `PASS` (100%)
- **Average Scoring across Dimensions**: **45 / 50 (90%)**
  - Specificity: 9/10
  - Category Fit: 9/10
  - Merchant Fit: 9/10
  - Decision Quality / Trigger Relevance: 9/10
  - Engagement Compulsion: 9/10

---

## 6. Project Structure & How to Run

### File Overview
- `bot.py`: Core composer engine & FastAPI HTTP server exposing `/v1/*` endpoints.
- `conversation_handlers.py`: Stateful multi-turn dialogue, auto-reply, intent transition, and hostility handler.
- `dataset/generate_dataset.py`: Expanded seed dataset generator ($50$ merchants, $200$ customers, $100$ triggers, $30$ test pairs).
- `generate_submission.py`: Generates canonical `submission.jsonl` from the 30 test pairs.
- `submission.jsonl`: The 30 canonical test outputs.
- `judge_simulator.py`: LLM & rule-based test harness for local verification.

### Quick Start Commands

```bash
# 1. Expand dataset
python dataset/generate_dataset.py --seed-dir dataset --out dataset/expanded

# 2. Generate submission.jsonl
python generate_submission.py

# 3. Start Vera Bot Server (Port 8080)
python bot.py

# 4. Run Judge Harness (In another terminal)
python judge_simulator.py
```
