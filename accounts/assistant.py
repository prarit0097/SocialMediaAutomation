"""Match visitor questions against maintained Postzyo product answers."""

import re
from collections import Counter
from difflib import get_close_matches
from functools import lru_cache
from types import MappingProxyType

from .assistant_content import TOPICS


QUESTION_FRAMES = (
    "{}", "What should I know about {}?", "Can you explain {}?",
    "Could you tell me about {}?", "I have a question about {}.",
    "Where can I get help with {}?", "How does {} work?",
    "I’d like to learn about {}.", "What does Postzyo say about {}?",
    "Can you help me understand {}?", "What are the details for {}?",
    "I’m looking for information on {}.", "How do I get started with {}?",
    "Do you have a guide for {}?", "What are the basics of {}?",
    "I’m new to Postzyo. Can you explain {}?", "Is there information about {}?",
    "Can I get a quick overview of {}?", "What’s the process for {}?",
    "Where do I read about {}?", "Please help me with {}.",
    "Is there a help article about {}?", "Could you walk me through {}?",
    "Can you answer a question about {}?", "I want to know how {} works.",
    "{} ke baare mein batao", "Mujhe {} samjhao", "{} ke liye help chahiye",
    "{} kaise kaam karta hai", "{} ki details chahiye",
)
DIRECT_QUESTION_FRAMES = (
    "{}", "Quick question: {}", "Hello, {}", "Hi Postzyo, {}", "One more thing: {}",
)
DIRECT_QUESTION = re.compile(
    r"^(are|can|could|does|do|how|is|what|where|which|who|why|will|when)\b|\bkaise\b"
)
FILLER = frozenset(
    "a an and are about can could do does for from how i in is it me my of on or please "
    "much should the to what when where which who why with would you your postzyo "
    "batao bhi chahiye gaya hai ho ka kaa kaise karna kare karu ke ki kitna kitne ko kya mein "
    "mera mere mujhe par sakta samjhao se".split()
)
SYNONYMS = {
    "accounts": "account", "annual": "yearly", "annually": "yearly",
    "costs": "cost", "din": "days", "expire": "expired", "fb": "facebook", "ig": "instagram", "insta": "instagram",
    "payments": "payment", "plans": "plan", "posts": "post",
    "prices": "price", "pricing": "price", "scheduling": "schedule",
    "videos": "video",
}
WORD = re.compile(r"[^\W_]+")


def _normalize(text):
    return " ".join(WORD.findall(text.casefold()))


def _terms(text):
    return frozenset(SYNONYMS.get(word, word) for word in text.split() if word not in FILLER)


@lru_cache(maxsize=1)
def question_index():
    return MappingProxyType({
        _normalize(frame.format(phrase)): topic
        for topic in TOPICS
        for phrase in topic.phrases.split("|")
        for frame in (DIRECT_QUESTION_FRAMES if DIRECT_QUESTION.search(phrase) else QUESTION_FRAMES)
    })


@lru_cache(maxsize=1)
def _phrase_data():
    aliases = [
        (_terms(_normalize(phrase)), topic)
        for topic in TOPICS
        for phrase in topic.phrases.split("|")
    ]
    counts = Counter(term for terms, _ in aliases for term in terms)
    weights = {term: 1 / (count ** 0.35) for term, count in counts.items()}
    return aliases, weights


def answer_question(question):
    normalized = _normalize(question)
    exact = question_index().get(normalized)
    if exact:
        return exact

    aliases, weights = _phrase_data()
    query = _terms(normalized)
    if not query:
        return None
    corrected = set()
    for term in query:
        matches = get_close_matches(term, weights, n=1, cutoff=0.82) if term not in weights and len(term) >= 5 else []
        corrected.add(matches[0] if matches else term)

    query_weight = sum(weights.get(term, 0.65) for term in corrected)
    scores = {}
    for terms, topic in aliases:
        overlap = sum(weights[term] for term in terms & corrected)
        if not overlap:
            continue
        recall = overlap / query_weight
        precision = overlap / sum(weights[term] for term in terms)
        if recall >= 0.58 and precision >= 0.55:
            score = (recall + precision) / 2
            scores[topic] = max(score, scores.get(topic, 0))

    ranked = sorted(scores.items(), key=lambda result: result[1], reverse=True)
    if ranked and (len(ranked) == 1 or ranked[0][1] - ranked[1][1] >= 0.04):
        return ranked[0][0]
    return None
