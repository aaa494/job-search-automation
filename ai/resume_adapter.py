"""
Adapts the base resume for a specific job posting.

Rules:
  - Rewrite summary to directly address the job
  - Reorder skills so the most relevant ones come first
  - Reorder bullets within each job — most relevant to THIS job goes first
  - Can strengthen and expand bullet points based on what the skill IMPLIES
    (e.g. "used Kubernetes in production" → can mention pods, deployments, namespaces,
    scaling, health checks — these are all standard parts of K8s work)
  - NEVER invent jobs, companies, certifications, or years of experience
  - NEVER claim tools the candidate has never mentioned
  - Language: simple, direct, natural — like a real person wrote it, not an AI
    Avoid: "leveraged", "spearheaded", "orchestrated synergies", "cutting-edge"
    Use: "built", "set up", "managed", "improved", "helped", "reduced", "wrote"
"""

import copy
import json
import anthropic
from config import AI_CONFIG
from database import Job

_client = anthropic.AsyncAnthropic()

# Skills that strongly imply related knowledge — used to help Claude infer details
SKILL_IMPLICATIONS = {
    "Kubernetes": "knows pods, deployments, services, ingress, namespaces, RBAC, resource limits, health checks, horizontal scaling, kubectl",
    "Helm": "knows chart templating, values files, release management, chart repositories",
    "ArgoCD": "knows GitOps workflow, sync policies, app-of-apps pattern, rollback",
    "Terraform": "knows HCL, state management, modules, workspaces, plan/apply workflow, remote backends",
    "Ansible": "knows playbooks, roles, inventory, idempotency, vault for secrets",
    "Prometheus": "knows PromQL, alerting rules, recording rules, scrape configs, exporters",
    "Grafana": "knows dashboards, data sources, alert management, panels",
    "Datadog": "knows APM, log management, metrics, monitors, dashboards, integrations",
    "GitHub Actions": "knows workflows, jobs, steps, matrix builds, secrets, reusable workflows, self-hosted runners",
    "Docker": "knows Dockerfile, multi-stage builds, layer caching, container networking, volumes",
    "AWS": "knows EC2, S3, IAM, VPC, EKS, RDS, Lambda, CloudWatch, Route53, Load Balancers",
    "GCP": "knows GKE, Cloud Storage, Cloud Run, IAM, VPC, Cloud SQL, Pub/Sub",
    "Azure": "knows AKS, Azure DevOps, Azure SQL, Key Vault, Azure Monitor, App Service",
    "HashiCorp Vault": "knows secrets engines, auth methods, policies, dynamic secrets, secret rotation",
    "PostgreSQL": "knows schema management, query optimization, indexes, replication, backups, pg_dump, connection pooling",
    "MongoDB": "knows aggregation pipeline, indexes, replica sets, sharding, MongoDB Atlas management",
    "Liquibase": "knows changesets, changelogs, rollback, diff commands, database version control",
    "Azure SQL": "knows Azure SQL Database, elastic pools, DTU/vCore pricing, backups, geo-replication",
}


async def adapt_resume(base_resume: dict, job: Job) -> dict:
    """Returns a new resume dict adapted for the given job. Base is never mutated."""
    resume = copy.deepcopy(base_resume)

    skills_flat = []
    for items in base_resume.get("skills", {}).values():
        if isinstance(items, list):
            skills_flat.extend(items)

    # Build skill implications for skills the candidate has
    implications_text = ""
    for skill in skills_flat:
        if skill in SKILL_IMPLICATIONS:
            implications_text += f"\n- {skill}: {SKILL_IMPLICATIONS[skill]}"

    experience_json = json.dumps(base_resume.get("experience", []), indent=2)

    prompt = f"""You are helping a DevOps engineer tailor his resume for a job application.

## Target Job
Title: {job.title}
Company: {job.company}
Description:
{job.description[:4500]}

## Candidate's Current Summary
{base_resume['summary']}

## Candidate's Skills
{', '.join(skills_flat)}

## What these skills imply (use this to add relevant details to bullets, but ONLY for skills already listed):
{implications_text}

## Candidate's Experience
{experience_json}

---

## Your job — 3 tasks:

### Task 1: Write a new "summary" (3-4 sentences)
- Start with something like "DevOps Engineer with X years of experience in..."
- Mention 2-3 specific tools or technologies from the job description that the candidate actually has
- Keep it simple and direct — like a real person wrote it
- No buzzwords like "leveraged", "spearheaded", "synergies"
- Use words like: built, managed, worked on, helped, improved, set up, maintained

### Task 2: "highlighted_skills" — reorder the candidate's skills by relevance to THIS job
- Most relevant tools (mentioned in job description) go first
- Use EXACT same names as in the skills list above
- Include ALL skills, just in different order

### Task 3: "adapted_experience" — same bullets, better order + slight improvements
- Move the most relevant bullets to the top for each job
- You can expand a bullet slightly if the skill clearly implies more detail
  Example: "Managed MongoDB Atlas clusters" → "Managed MongoDB Atlas clusters, including replica sets, index optimization, and automated backups"
  This is OK because MongoDB expertise clearly includes these things.
- Keep the same total number of bullets per job
- Do NOT add bullets about tools not mentioned in original experience
- Simple language — no corporate buzzwords
- Real numbers/metrics only if they were in the original bullets
- Do NOT use em-dashes (—) anywhere in bullets or summary. Use commas or colons instead.

Return ONLY valid JSON, no markdown:
{{
  "summary": "...",
  "highlighted_skills": ["skill1", "skill2", ...],
  "adapted_experience": [
    {{
      "title": "...",
      "company": "...",
      "location": "...",
      "start": "...",
      "end": "...",
      "bullets": ["...", ...]
    }}
  ]
}}"""

    kwargs: dict = {
        "model": AI_CONFIG["model"],
        "max_tokens": 16000,
        "messages": [{"role": "user", "content": prompt}],
    }
    if AI_CONFIG.get("use_thinking"):
        kwargs["thinking"] = {"type": "adaptive"}

    response = await _client.messages.create(**kwargs)
    text = next(b.text for b in response.content if b.type == "text")

    text = text.strip()
    # Strip markdown fences
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
    # Trim any trailing text after the closing brace
    last_brace = text.rfind("}")
    if last_brace != -1:
        text = text[:last_brace + 1]

    data = json.loads(text)

    # Apply summary
    resume["summary"] = data["summary"]

    # Apply skill reordering
    priority_skills = data.get("highlighted_skills", [])
    top_skills = [s for s in priority_skills if s in skills_flat][:12]
    priority_lower = {s.lower() for s in top_skills}

    new_skills = {}
    if top_skills:
        new_skills["highlighted"] = top_skills
    for cat, items in base_resume.get("skills", {}).items():
        if isinstance(items, list):
            leftover = [s for s in items if s.lower() not in priority_lower]
            if leftover:
                new_skills[cat] = leftover
    resume["skills"] = new_skills

    # Apply experience
    adapted_exp = data.get("adapted_experience", [])
    if adapted_exp and len(adapted_exp) == len(resume.get("experience", [])):
        resume["experience"] = adapted_exp

    return resume
