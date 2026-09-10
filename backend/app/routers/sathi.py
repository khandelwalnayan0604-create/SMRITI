import re
from datetime import datetime, timezone
import httpx
from fastapi import APIRouter, HTTPException, Depends
from app.database import get_db
from app.auth import get_current_user
from app.config import settings
from app.schemas import SathiChatRequest
from bson import ObjectId

router = APIRouter(prefix="/sathi", tags=["sathi"])

DISTRESS_PATTERNS = [
    r"where am i", r"who are you", r"lost", r"scared", r"fear", r"help me", r"die",
    r"want to go home", r"take me home", r"don't know", r"confused", r"stolen",
    r"ক'ত আছো", r"মই ক'ত", r"কোন তুমি", r"ডৰ লাগিছে", r"ভয় লাগিছে", r"পাহৰি গ'লো", r"ঘৰলৈ যাম",
    r"कहाँ हूँ", r"डर लग रहा", r"घर जाना", r"भूल गया", r"मदद करो", r"बचाओ"
]

FALLBACK_CALM_RESPONSES = {
    "as": (
        "মই আপোনাৰ লগত আছো। আপুনি আপোনাৰ নিজৰ ঘৰতে সুৰক্ষিত আছে। "
        "চিন্তা নকৰিব, আহক আমি অলপ জিৰণি লওঁ। আপুনি এতিয়া একাপ গৰম চাহ খাবনে?"
    ),
    "hi": (
        "मैं आपके साथ हूँ। आप अपने घर पर पूरी तरह सुरक्षित हैं। "
        "बिल्कुल चिंता न करें। चलिए थोड़ा विश्राम करते हैं। क्या आप एक कप गर्म चाय पिएंगे?"
    ),
    "en": (
        "I am right here with you. You are completely safe at home. "
        "Take a gentle breath. Let us relax and look at your family pictures together."
    )
}

def detect_distress(text: str) -> bool:
    clean = text.lower()
    for pattern in DISTRESS_PATTERNS:
        if re.search(pattern, clean, re.IGNORECASE):
            return True
    return False

@router.post("/chat")
async def chat_with_sathi(payload: SathiChatRequest, current_user: dict = Depends(get_current_user)):
    db = get_db()
    patient_id = payload.patient_id
    patient = await db.patients.find_one({"_id": ObjectId(patient_id)})
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    patient_name = patient.get("name", "Elder")
    lang = payload.language or patient.get("language", "as")

    # Fetch personalization context from survey and facts
    survey = await db.patient_surveys.find_one({"patient_id": patient_id})
    facts_cursor = db.facts.find({"patient_id": patient_id}).limit(5)
    facts_list = [f.get("content") async for f in facts_cursor]

    family_members = survey.get("family_context", []) if survey else []
    family_names = ", ".join([f"{m.get('name')} ({m.get('relationship')})" for m in family_members]) if family_members else "family members"
    interests = survey.get("interests_history", {}) if survey else {}
    fav_food = ", ".join(interests.get("favorite_foods", [])) or "Assam tea"
    fav_places = ", ".join(interests.get("favorite_places", [])) or "Jorhat"

    # Guardrail check for acute distress or disorientation
    is_distressed = detect_distress(payload.message)
    if is_distressed:
        now_iso = datetime.now(timezone.utc).isoformat()
        # Log flagged alert for caregiver immediately
        alert_doc = {
            "patient_id": patient_id,
            "type": "decline",
            "severity": "high",
            "read": False,
            "dismissed": False,
            "timestamp": now_iso,
            "resolved_by": None,
            "details": {
                "reason": "Sathi guardrail detected acute distress or confusion in patient conversation",
                "patient_utterance": payload.message,
                "action_taken": "Flagged alert created; soothing response presented",
                "simulated_sms": f"CAREGIVER NOTICE: Patient {patient_name} voiced confusion/distress in Sathi companion: '{payload.message[:50]}...'. Please offer reassurance."
            }
        }
        await db.alerts.insert_one(alert_doc)

    # If distress detected, return high-priority reassuring response
    if is_distressed:
        reassurance = {
            "as": f"নমস্কাৰ {patient_name} ডাঙৰীয়া। আপুনি আপোনাৰ চিনাকি ঘৰতে সুৰক্ষিত আছে। আপোনাৰ পৰিয়াল {family_names} আপোনাৰ কাষতেই আছে। একো চিন্তা নকৰিব, আহক অলপ বহক।",
            "hi": f"नमस्ते {patient_name} जी। आप बिल्कुल सुरक्षित हैं। आपका परिवार {family_names} आपके साथ है। चिंता मत कीजिए, शांत रहिए।",
            "en": f"Namaskar {patient_name}. You are completely safe in your home in {fav_places}. Your family {family_names} is close by. Take a gentle breath, you are safe."
        }
        reply_text = reassurance.get(lang, reassurance["en"])
        return {
            "reply": reply_text,
            "distress_flagged": True,
            "language": lang
        }

    # Attempt LLM call if OpenAI key exists
    system_prompt = (
        f"You are Sathi (সাথী), an empathetic, polite, voice-first AI companion for {patient_name}, "
        f"an elderly person in North East India with mild cognitive impairment. "
        f"Family context: {family_names}. "
        f"Favorite places: {fav_places}. Favorite foods: {fav_food}. "
        f"Facts about their life: {'; '.join(facts_list)}. "
        f"Respond in {lang} language (or English if requested), with extreme gentleness, warmth, short sentences (1-2 sentences maximum), "
        f"using positive grounding memories. NEVER show technical or medical jargon."
    )

    if settings.OPENAI_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": payload.message}
                        ],
                        "max_tokens": 80,
                        "temperature": 0.6
                    }
                )
                if res.status_code == 200:
                    data = res.json()
                    bot_text = data["choices"][0]["message"]["content"].strip()
                    return {"reply": bot_text, "distress_flagged": False, "language": lang}
        except Exception:
            # Fall through silently to calm fallback on timeout or error
            pass

    # Contextual compassionate fallback
    grounded_replies = {
        "as": [
            f"নমস্কাৰ {patient_name} ডাঙৰীয়া। আজিৰ বতৰটো বৰ শান্ত। আপুনি এতিয়া একাপ সুবাসিত অসম চাহ খাবনে?",
            f"আপোনাৰ কথা শুনি বৰ ভাল লাগিল। আপুনি জুৰহাটৰ বাগানৰ কথা কিবা মনত পেলাইছেনে?",
            f"মই আপোনাৰ লগত আছো। আপোনাৰ পৰিয়াল {family_names} সদায় আপোনাৰ শুভাকাংক্ষী।"
        ],
        "hi": [
            f"नमस्ते {patient_name} जी। आज का दिन बहुत शांत है। क्या आप एक कप गर्म असम चाय लेना पसंद करेंगे?",
            f"आपकी बात सुनकर बहुत अच्छा लगा। आपके परिवार के सभी सदस्य आपको बहुत स्नेह करते हैं।"
        ],
        "en": [
            f"Namaskar {patient_name}. It is a peaceful day today. Would you like a warm cup of Assam tea?",
            f"It is wonderful talking with you. Your family {family_names} loves you very dearly."
        ]
    }
    options = grounded_replies.get(lang, grounded_replies["en"])
    import random
    reply = random.choice(options)

    return {
        "reply": reply,
        "distress_flagged": False,
        "language": lang
    }
