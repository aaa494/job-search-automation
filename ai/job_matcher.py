"""
Score job relevance using Claude.
Returns a 0-100 score + reasoning so main.py can filter low-quality matches.
"""

import asyncio
import json
import logging
import anthropic
from config import AI_CONFIG
from database import Job

log = logging.getLogger("jobsearch")
_client = anthropic.AsyncAnthropic()


async def score_job(job: Job, user_profile: dict) -> tuple[float, str]:
    """
    Returns (score: 0-100, reason: str).
    Uses adaptive thinking to reason about the match carefully.
    """
    skills_flat = []
    for items in user_profile.get("skills", {}).values():
        skills_flat.extend(items)

    exp_summary = "\n".join(
        f"- {e['title']} at {e['company']} ({e['start']}–{e['end']})"
        for e in user_profile.get("experience", [])
    )

    prompt = f"""Score how well this candidate fits the job. Be realistic and practical.

## Candidate
Name: {user_profile['personal']['name']}
Title: {user_profile['personal']['title']}
Summary: {user_profile['summary']}
Skills: {', '.join(skills_flat[:40])}
Experience:
{exp_summary}
Certifications: {', '.join(user_profile.get('certifications', []))}

## Job
Title: {job.title}
Company: {job.company}
Location: {job.location}
Salary: {job.salary or 'Not mentioned'}
Description:
{job.description[:3000]}

## Scoring guide:
- 85-100: Great fit. Most requirements match. Would likely get an interview.
- 70-84: Good fit. Candidate can do the job. A few gaps but nothing major.
- 50-69: Partial fit. Missing some key requirements or wrong seniority level.
- 0-49: Bad fit. Wrong tech stack, wrong level, not remote, or not DevOps related.

Also check: is this job actually remote? Does it require US work authorization? (candidate has Green Card, so that's fine)

Write the reason in simple, short sentences. No buzzwords.

Respond ONLY with valid JSON, no markdown:
{{
  "score": <integer 0-100>,
  "reason": "<2 short sentences why this score>",
  "key_matches": ["<what matches>"],
  "key_gaps": ["<what's missing>"]
}}"""

    kwargs: dict = {
        "model": AI_CONFIG["model"],
        "max_tokens": 8000,
        "messages": [{"role": "user", "content": prompt}],
    }
    if AI_CONFIG.get("use_thinking"):
        kwargs["thinking"] = {"type": "adaptive"}

    # Retry up to 3 times on transient network errors
    response = None
    for attempt in range(3):
        try:
            response = await _client.messages.create(**kwargs)
            break
        except Exception as e:
            if attempt == 2:
                raise
            log.warning("Claude API attempt %d/3 failed (%s) — retrying in %ds", attempt + 1, type(e).__name__, 3 * (attempt + 1))
            await asyncio.sleep(3 * (attempt + 1))

    # Extract the text block (thinking blocks may precede it)
    text = next(b.text for b in response.content if b.type == "text")

    # Strip possible markdown fences
    text = text.strip()
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                text = part
                break
    text = text.strip()
    last_brace = text.rfind("}")
    if last_brace != -1:
        text = text[:last_brace + 1]

    data = json.loads(text)
    score = float(data.get("score", 0))
    reason = data.get("reason", "")
    return score, reason
