"""MCP server exposing Kyle Romero's resume from kgromero.com."""

from __future__ import annotations

import argparse
import os
import re
from importlib.metadata import version
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp_types import ImageContent, ResourceLink, TextContent, ToolAnnotations
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from . import data, documents

mcp = MCPServer(
    name="kgromero",
    title="Kyle Romero (kgromero.com)",
    website_url="https://kgromero.com",
    version=version("kgromero-mcp"),
    instructions=(
        "Answers questions about Kyle Romero, a software engineering leader, using "
        "the content published at kgromero.com. Start with get_profile for an overview. "
        "list_experience gives one entry per employer and get_experience the details of "
        "a role; get_career_timeline lists every title including promotions. get_skills, "
        "list_side_projects, list_documents (portfolio and resume downloads; read one with "
        "get_document), "
        "get_interests, get_travel, and get_contact cover the rest. Use search to find "
        "anything by keyword. Only state facts these tools return; do not invent details."
    ),
)

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)


def _job_summary(index: int, job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": index,
        "company": job.get("company"),
        "position": job.get("position"),
        "dates": data.format_range(job.get("startDate"), job.get("endDate")),
        "months": data.duration_months(job.get("startDate"), job.get("endDate")),
        "current": not job.get("endDate"),
        "summary": job.get("summary"),
    }


def _job_detail(index: int, job: dict[str, Any]) -> dict[str, Any]:
    out = _job_summary(index, job)
    out["website"] = job.get("website")
    sections = []
    for h in job.get("highlights", []):
        label, body = data.split_highlight(h)
        if label and label.lower() == "tech stack":
            out["tech_stack"] = data.split_tech_stack(body)
        else:
            sections.append({"label": label, "text": body} if label else {"text": body})
    out["highlights"] = sections
    return out


@mcp.tool(annotations=READ_ONLY)
async def get_profile() -> dict[str, Any]:
    """Overview of Kyle Romero: title, summary, location, current role, and total experience."""
    r = await data.get_resume()
    site = await data.get_site()
    b = r["basics"]
    work = r.get("work", [])
    current = [f"{j['position']} at {j['company']}" for j in work if not j.get("endDate")]
    starts = [j["startDate"] for j in work if j.get("startDate")]
    first = min(starts) if starts else None
    return {
        "name": b.get("name"),
        "title": b.get("label"),
        "summary": b.get("summary"),
        "about": site.get("about"),
        "location": ", ".join(
            x for x in (b.get("location", {}).get("city"), b.get("location", {}).get("region")) if x
        ),
        "current_roles": current,
        "career_start": first,
        "years_of_experience": data.duration_months(first, None) // 12 if first else None,
        "website": b.get("website"),
    }


@mcp.tool(annotations=READ_ONLY)
async def get_contact() -> dict[str, Any]:
    """Contact details and online profiles (LinkedIn, GitHub, Keyoxide), plus a vCard link."""
    b = (await data.get_resume())["basics"]
    return {
        "name": b.get("name"),
        "email": b.get("email"),
        "phone": b.get("phone"),
        "website": b.get("website"),
        "vcard": f"{data.SITE_URL}/kgromero.vcf",
        "profiles": [
            {"network": p.get("network"), "username": p.get("username"), "url": p.get("url")}
            for p in b.get("profiles", [])
        ],
    }


@mcp.tool(annotations=READ_ONLY)
async def list_experience() -> list[dict[str, Any]]:
    """Career timeline, most recent first. Each entry has an id for use with get_experience."""
    work = (await data.get_resume()).get("work", [])
    return [_job_summary(i, j) for i, j in enumerate(work)]


@mcp.tool(annotations=READ_ONLY)
async def get_experience(company_or_id: str) -> list[dict[str, Any]]:
    """Full details for one or more roles: highlights, accomplishments, and tech stack.

    Args:
        company_or_id: An id from list_experience (e.g. "0"), or part of a company or
            position name (e.g. "Tenna", "HP", "Director"). Case-insensitive.
    """
    work = (await data.get_resume()).get("work", [])
    key = company_or_id.strip()
    if key.isdigit() and int(key) < len(work):
        return [_job_detail(int(key), work[int(key)])]
    needle = key.lower()
    matches = [
        _job_detail(i, j)
        for i, j in enumerate(work)
        if needle in j.get("company", "").lower() or needle in j.get("position", "").lower()
    ]
    if not matches:
        names = ", ".join(sorted({j.get("company", "") for j in work}))
        raise ValueError(f"No role matches {company_or_id!r}. Companies: {names}")
    return matches


@mcp.tool(annotations=READ_ONLY)
async def get_education() -> list[dict[str, Any]]:
    """Degrees and schools."""
    edu = (await data.get_resume()).get("education", [])
    return [
        {
            "institution": e.get("institution"),
            "degree": e.get("studyType"),
            "field": e.get("area"),
            "location": e.get("location"),
            "dates": data.format_range(e.get("startDate"), e.get("endDate")),
        }
        for e in edu
    ]


@mcp.tool(annotations=READ_ONLY)
async def get_career_timeline() -> list[dict[str, str]]:
    """Every title Kyle has held, most recent first, including promotions within a company
    (e.g. Manager → Senior Manager → Director at MEARS) and education."""
    return (await data.get_site()).get("timeline", [])


def _skill_name(item: str) -> tuple[str, list[str]]:
    """'AWS (EKS, S3)' -> ('AWS', ['EKS', 'S3'])."""
    m = re.match(r"^(.*?)\s*\((.*)\)\s*$", item)
    return (m.group(1), data.split_tech_stack(m.group(2))) if m else (item, [])


@mcp.tool(annotations=READ_ONLY)
async def get_skills(filter: str | None = None) -> dict[str, Any]:
    """Kyle's skills two ways: by category (Cloud, DB, AI, Processes, ...) and as
    technologies used on the job, merged across roles with where and how recently.

    Args:
        filter: Optional case-insensitive substring to narrow results (e.g. "aws", "react",
            "agile"). Matches skill names, sub-components, and category names.
    """
    work = (await data.get_resume()).get("work", [])
    site = await data.get_site()
    f = (filter or "").lower()

    used: dict[str, dict[str, Any]] = {}
    for job in work:
        end = job.get("endDate") or "present"
        for h in job.get("highlights", []):
            label, body = data.split_highlight(h)
            if not label or label.lower() != "tech stack":
                continue
            for item in data.split_tech_stack(body):
                name, parts = _skill_name(item)
                entry = used.setdefault(
                    name.lower(),
                    {"name": name, "components": [], "used_at": [], "most_recent": None},
                )
                entry["components"] += [p for p in parts if p not in entry["components"]]
                if job["company"] not in entry["used_at"]:
                    entry["used_at"].append(job["company"])
                # Work is listed newest first, so the first role seen is the latest.
                entry["most_recent"] = entry["most_recent"] or end

    on_the_job = list(used.values())
    categories = site.get("skillCategories", [])
    if f:
        on_the_job = [
            s for s in on_the_job
            if f in s["name"].lower() or any(f in c.lower() for c in s["components"])
        ]
        categories = [
            {"category": c["category"],
             "skills": c["skills"] if f in c["category"].lower()
             else [s for s in c["skills"] if f in s.lower()]}
            for c in categories
        ]
        categories = [c for c in categories if c["skills"]]
    on_the_job.sort(key=lambda s: (-len(s["used_at"]), s["name"].lower()))
    return {"categories": categories, "used_on_the_job": on_the_job}


@mcp.tool(annotations=READ_ONLY)
async def list_side_projects() -> dict[str, Any]:
    """Personal side projects with language, description, and GitHub link."""
    site = await data.get_site()
    return {"projects": site.get("sideProjects", []), "more": site.get("moreProjects")}


@mcp.tool(annotations=READ_ONLY)
async def list_documents(group: str | None = None) -> list[dict[str, Any]]:
    """Downloadable resume formats plus the portfolio: architecture diagrams, process docs,
    project write-ups, certificates, and college lab reports, grouped by employer/school.
    Use get_document to read one.

    Args:
        group: Optional filter by group name, e.g. "Resume", "MEARS", "HP", "Texas Tech",
            "Other". Case-insensitive substring.
    """
    groups = [documents.resume_group()]
    groups += (await data.get_site()).get("documents", [])
    if group:
        g = group.lower()
        groups = [x for x in groups if g in x["group"].lower()]
    return groups


@mcp.tool(annotations=READ_ONLY, structured_output=False)
async def get_document(
    document: str, pages: str | None = None, as_images: bool = False
) -> list[TextContent | ImageContent | ResourceLink]:
    """Read a document from kgromero.com: PDFs as text, diagrams and photos as images.

    Many PDFs are slide decks or scans; the text result lists pages that are mostly
    visual so you can call again with as_images=true to see them. Results for files on
    the site end with a link to the original file.

    Args:
        document: Name, part of a name, filename, or URL from list_documents
            (e.g. "Electron Architecture", "Transcript", "RedditListener").
        pages: PDF pages to return, e.g. "3", "1-5", or "2,4,7-9". Defaults to all
            pages for text (up to a length limit).
        as_images: Return PDF pages as images instead of text (up to 5 pages per call).
    """
    return await documents.get(document, pages, as_images)


@mcp.tool(annotations=READ_ONLY)
async def get_interests() -> dict[str, Any]:
    """Personal interests and hobbies, and the tech news sites Kyle reads."""
    site = await data.get_site()
    return {"interests": site.get("interests", []), "tech_resources": site.get("techResources", [])}


@mcp.tool(annotations=READ_ONLY)
async def get_travel(lived_only: bool = False) -> dict[str, Any]:
    """Places Kyle has lived and traveled to (from the travel globe on kgromero.com).

    Args:
        lived_only: Only return places Kyle has lived.
    """
    places = (await data.get_site()).get("travel", [])
    lived = [p["place"] for p in places if p.get("lived")]
    visited = [p for p in places if not p.get("lived")]
    out: dict[str, Any] = {"lived": lived}
    if not lived_only:
        out["visited"] = visited
        out["visited_count"] = len(visited)
    return out


@mcp.tool(annotations=READ_ONLY)
async def search(query: str) -> list[dict[str, Any]]:
    """Search everything on kgromero.com (resume, skills, projects, documents, timeline,
    interests, travel) for a keyword or phrase, e.g. "cost", "Kubernetes", "architecture".

    Every word in the query must appear for a result to match. Case-insensitive.
    """
    r = await data.get_resume()
    site = await data.get_site()
    words = [w for w in query.lower().split() if w]
    if not words:
        raise ValueError("query must not be empty")

    def hit(*texts: str | None) -> bool:
        t = " ".join(x or "" for x in texts).lower()
        return all(w in t for w in words)

    results: list[dict[str, Any]] = []
    add = lambda section, text, **extra: results.append({"section": section, **extra, "text": text})  # noqa: E731

    if hit(r["basics"].get("summary")):
        add("summary", r["basics"]["summary"])
    if hit(site.get("about")):
        add("about", site["about"])
    for i, job in enumerate(r.get("work", [])):
        for text in [job.get("summary"), *job.get("highlights", [])]:
            if text and hit(job.get("company"), job.get("position"), text):
                add("experience", text, id=i, company=job.get("company"), position=job.get("position"))
    for e in r.get("education", []):
        line = f"{e.get('studyType')} in {e.get('area')}, {e.get('institution')}"
        if hit(line):
            add("education", line)
    for t in site.get("timeline", []):
        if hit(t.get("title"), t.get("date")):
            add("timeline", f"{t['date']}: {t['title']}")
    for c in site.get("skillCategories", []):
        matched = [s for s in c["skills"] if hit(c["category"], s)]
        if matched:
            add("skills", ", ".join(matched), category=c["category"])
    for p in site.get("sideProjects", []):
        if hit(p.get("name"), p.get("language"), p.get("description")):
            add("side_project", f"{p['name']} ({p.get('language')}): {p['description']}", url=p.get("link"))
    for g in site.get("documents", []):
        for d in g.get("items", []):
            if hit(g.get("group"), d.get("name")):
                add("document", d["name"], group=g["group"], url=d["url"])
    for i in site.get("interests", []):
        if hit(i):
            add("interest", i)
    for p in site.get("travel", []):
        if hit(p.get("place")):
            add("travel", p["place"], lived=p.get("lived"))
    return results


@mcp.resource(
    "resume://kgromero/json",
    name="resume-json",
    title="Resume (JSON Resume format)",
    mime_type="application/json",
)
async def resume_json() -> dict[str, Any]:
    return await data.get_resume()


@mcp.resource(
    "resume://kgromero/markdown",
    name="resume-markdown",
    title="Resume (Markdown)",
    mime_type="text/markdown",
)
async def resume_markdown() -> str:
    return await data.get_resume_markdown()


@mcp.resource(
    "resume://kgromero/site",
    name="site-content",
    title="kgromero.com site content (projects, skills, docs, travel)",
    mime_type="application/json",
)
async def site_content() -> dict[str, Any]:
    return await data.get_site()


@mcp.prompt(title="Assess fit for a job")
async def assess_job_fit(job_description: str) -> str:
    """Compare Kyle Romero's resume against a job description."""
    md = await data.get_resume_markdown()
    return (
        "Here is Kyle Romero's resume:\n\n"
        f"{md}\n\n---\n\nHere is a job description:\n\n{job_description}\n\n---\n\n"
        "Assess how well Kyle fits this role. List the strongest matches with evidence "
        "from the resume, any gaps, and suggested talking points. Only cite facts that "
        "appear in the resume."
    )


@mcp.custom_route("/", methods=["GET"], include_in_schema=False)
async def home(request: Request) -> Response:
    """Health check, and a pointer for people who open the URL in a browser."""
    return PlainTextResponse(
        "kgromero.com MCP server. Connect an MCP client to /mcp. "
        "Docs: https://github.com/romero927/kgromero-mcp\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="kgromero.com MCP server")
    parser.add_argument("--http", action="store_true", help="serve Streamable HTTP instead of stdio")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args()
    if args.http:
        # Stateless with plain JSON responses: no sessions to keep, and it works
        # cleanly behind the Netlify proxy at kgromero.com/mcp.
        mcp.run(
            "streamable-http",
            host=args.host,
            port=args.port,
            stateless_http=True,
            json_response=True,
        )
    else:
        mcp.run()


if __name__ == "__main__":
    main()
