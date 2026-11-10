"""Private writing observations. Message contents never become cross-case prompts."""
import re
from collections import Counter
from statistics import median

AUDIENCES = ("customer", "engineering", "internal")


def features(body):
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    words = re.findall(r"\b[\w']+\b", body)
    paragraphs = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    greeting = re.match(r"^(Hi|Hello|Dear|Good morning|Good afternoon)\b", lines[0] if lines else "", re.I)
    closings = {"best regards", "kind regards", "regards", "thanks", "thank you", "best", "sincerely"}
    closing = next((line.rstrip(",.! ").lower() for line in reversed(lines[-5:]) if line.rstrip(",.! ").lower() in closings), None)
    return dict(words=len(words), paragraphs=len(paragraphs), questions=body.count("?"),
                bullets=bool(re.search(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+", body)),
                greeting=greeting.group(1).lower() if greeting else None, closing=closing,
                good_day=bool(re.search(r"\bgood day\b", " ".join(lines[:3]), re.I)),
                acknowledges_early=bool(re.search(r"\b(thank you|thanks|understand|appreciate)\b", " ".join(paragraphs[:2])[:500], re.I)))


def describe(examples):
    # Recent confirmed examples allow preferences to evolve without amplifying
    # an AI suggestion simply because it was displayed, copied or reviewed.
    recent = examples[:20]
    if not recent:
        return []
    n = len(recent)
    observations = [f"Typical length: {round(median(e['features']['words'] for e in recent))} words (recent {n} examples)."]
    for key, label in (("greeting", "Opening"), ("closing", "Sign-off")):
        counts = Counter(e['features'][key] for e in recent if e['features'].get(key))
        if counts:
            value, count = counts.most_common(1)[0]
            observations.append(f"{label}: {value} ({count}/{n} examples).")
    for key, label in (("bullets", "Uses bullets or numbered points"), ("acknowledges_early", "Acknowledges or thanks early")):
        count = sum(bool(e['features'][key]) for e in recent)
        observations.append(f"{label}: {count}/{n} examples.")
    observations.append(f"Typical paragraph count: {round(median(e['features']['paragraphs'] for e in recent))}.")
    return observations


def historical_descriptions(records):
    """Only allowlisted structural choices can cross case boundaries."""
    labels = dict(numbered_issues="Numbered issues with specific details", observations_recommendations="Observations followed by recommended changes", direct_reference="Direct reference with short context", short_specific_ask="Brief follow-up with a specific question")
    observations = set()
    for record in records[:20]:
        f = record['features']
        if f.get('greeting') in {'hi', 'hello', 'dear'}:
            observations.add('Opening: ' + f['greeting'])
        if f.get('good_day'):
            observations.add('Often adds “Good day!” after the greeting')
        if f.get('closing') in {'best regards', 'kind regards', 'regards', 'thanks', 'thank you'}:
            observations.add('Sign-off: ' + f['closing'])
        if f.get('acknowledges_early'):
            observations.add('Acknowledges customer effort early')
        observations.update(labels[s] for s in f.get('structure', []) if s in labels)
    return sorted(observations)
