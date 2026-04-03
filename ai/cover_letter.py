"""
Generates a personalized cover letter. Uses streaming so you see it being written.
Language style: simple, direct, natural — intermediate English speaker level.
"""

import anthropic
from config import AI_CONFIG
from database import Job

_client = anthropic.AsyncAnthropic()


async def generate_cover_letter(
    job: Job,
    adapted_resume: dict,
    stream_callback=None,
) -> str:
    """
    Returns the full cover letter as a string.
    If stream_callback is provided, calls it with each text chunk.
    """
    skills_flat = []
    for items in adapted_resume.get("skills", {}).values():
        if isinstance(items, list):
            skills_flat.extend(items[:5])

    # Pick the 2 most relevant experience bullets from the most recent job
    recent_job = adapted_resume.get("experience", [{}])[0]
    top_bullets = recent_job.get("bullets", [])[:3]
    bullets_text = "\n".join(f"- {b}" for b in top_bullets)

    name = adapted_resume["personal"]["name"]
    email = adapted_resume["personal"]["email"]
    current_title = adapted_resume["personal"]["title"]

    prompt = f"""Write a cover letter for this job application. The candidate is a non-native English speaker writing at an intermediate level, so keep the language simple, natural, and direct.

## Job
Title: {job.title}
Company: {job.company}
Description (excerpt):
{job.description[:3000]}

## Candidate
Name: {name}
Title: {current_title}
Adapted Summary: {adapted_resume['summary']}
Key Skills (most relevant): {', '.join(skills_flat[:12])}
Recent Work Highlights:
{bullets_text}

## Rules for the cover letter:
- Length: 3 paragraphs, around 250 words total
- Paragraph 1: Why this company/role is interesting. Be specific — mention something real from the job description. Don't be generic.
- Paragraph 2: 2-3 concrete things the candidate did that are relevant to this role. Use simple past tense. Real results if available.
- Paragraph 3: Short closing. Mention availability and interest in talking. Keep it brief.

## Language rules (very important):
- Write like a real person, not like an AI
- Simple sentences. Short paragraphs.
- Avoid these words completely: leveraged, spearheaded, synergies, cutting-edge, innovative, passionate, driven, dynamic, robust, seamlessly, orchestrated
- Use these instead: built, set up, managed, worked on, helped, improved, reduced, wrote, deployed, handled, made sure
- Do NOT use em-dashes (—) anywhere. Use commas or colons instead.
- It's OK to say "I" a lot — cover letters are personal
- It's OK to say "I have experience with X" instead of fancy phrases
- First sentence should NOT be "I am writing to express my interest" — be more direct

Start with "Dear Hiring Manager," and end with:
"Best regards,
{name}
{email}"

Write ONLY the letter. No commentary before or after."""

    full_text = ""

    async with _client.messages.stream(
        model=AI_CONFIG["model"],
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        async for chunk in stream.text_stream:
            full_text += chunk
            if stream_callback:
                stream_callback(chunk)

    return full_text
