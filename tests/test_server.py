"""End-to-end tests over the MCP protocol, using the bundled snapshot data."""

import json

import pytest
from mcp import Client

from kgromero_mcp import data
from kgromero_mcp.server import mcp


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(data, "OFFLINE", True)
    data._cache.clear()


async def call(name, args=None):
    async with Client(mcp) as client:
        result = await client.call_tool(name, args or {})
        assert not result.is_error, result.content
        return result.structured_content.get("result", result.structured_content)


def test_split_tech_stack_respects_parentheses():
    assert data.split_tech_stack("AWS (EKS, S3), Docker, Claude.") == ["AWS (EKS, S3)", "Docker", "Claude"]


def test_split_highlight():
    assert data.split_highlight("Tech Stack: React, Redux") == ("Tech Stack", "React, Redux")
    assert data.split_highlight("No label here") == (None, "No label here")


async def test_lists_all_tools():
    async with Client(mcp) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert names == {
        "get_profile", "get_contact", "list_experience", "get_experience", "get_education",
        "get_career_timeline", "get_skills", "list_side_projects", "list_documents",
        "get_interests", "get_travel", "search",
    }


async def test_profile():
    p = await call("get_profile")
    assert p["name"] == "Kyle Romero"
    assert p["about"]
    assert p["years_of_experience"] >= 19


async def test_experience_lookup_by_name_and_id():
    jobs = await call("list_experience")
    assert jobs[0]["current"] is True
    by_name = await call("get_experience", {"company_or_id": "mears"})
    assert by_name[0]["position"] == "Director of Operations Technology"
    assert "tech_stack" in by_name[0]
    by_id = await call("get_experience", {"company_or_id": "0"})
    assert by_id[0]["company"] == jobs[0]["company"]


async def test_experience_no_match_is_error():
    async with Client(mcp) as client:
        result = await client.call_tool("get_experience", {"company_or_id": "Nonexistent Corp"})
    assert result.is_error


async def test_timeline_includes_promotions():
    titles = [t["title"] for t in await call("get_career_timeline")]
    assert sum("MEARS" in t for t in titles) == 3


async def test_skills_filter():
    s = await call("get_skills", {"filter": "aws"})
    assert any(x["name"] == "AWS" and "EKS" in x["components"] for x in s["used_on_the_job"])
    assert any(c["category"] == "Cloud" for c in s["categories"])
    aws = next(x for x in s["used_on_the_job"] if x["name"] == "AWS")
    assert aws["most_recent"] == "present"


async def test_documents_and_projects():
    docs = await call("list_documents", {"group": "hp"})
    assert docs and all("HP" in g["group"] for g in docs)
    assert all(i["url"].startswith("http") for g in await call("list_documents") for i in g["items"])
    projects = await call("list_side_projects")
    assert any(p["name"] == "Scordle" for p in projects["projects"])


async def test_travel():
    t = await call("get_travel", {"lived_only": True})
    assert "Jersey City" in t["lived"] and "visited" not in t


async def test_search_spans_sections():
    sections = {r["section"] for r in await call("search", {"query": "architecture"})}
    assert {"skills", "document"} <= sections


async def test_resources_and_prompt():
    async with Client(mcp) as client:
        uris = {str(r.uri) for r in (await client.list_resources()).resources}
        assert uris == {"resume://kgromero/json", "resume://kgromero/markdown", "resume://kgromero/site"}
        res = await client.read_resource("resume://kgromero/json")
        assert json.loads(res.contents[0].text)["basics"]["name"] == "Kyle Romero"
        prompt = await client.get_prompt("assess_job_fit", {"job_description": "Staff engineer"})
        assert "Staff engineer" in prompt.messages[0].content.text
