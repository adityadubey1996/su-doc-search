# backend/search/scraper.py
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup, Tag
from markdownify import markdownify as to_md

logger = logging.getLogger(__name__)

EXCLUDED_PREFIXES = ("/Content/Resources-Mamba20/", "/Content/GeneratedImages/")
SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
HEADING_TAGS = {"h1", "h2", "h3"}

# Elements that are navigation/chrome noise inside the content container
_NOISE_TAGS = ["nav", "script", "style", "footer", "header", "aside", "noscript"]
# MadCap Flare marks sidebar/TOC blocks with data-mc-ignore
_NOISE_CLASSES = {"MCBreadcrumbsBox_0", "main-banner", "hamburger-toolbar", "hamburger-menu"}


@dataclass
class PageDoc:
    url: str
    title: str
    category: str
    breadcrumb: str
    # (anchor_id, heading_text, body_markdown) — one entry per heading section
    sections: list[tuple[str, str, str]] = field(default_factory=list)


def parse_sitemap(xml_text: str) -> list[str]:
    """Return .htm doc URLs from sitemap, excluding image/asset folders."""
    root = ElementTree.fromstring(xml_text)
    urls = []
    for loc in root.iter(f"{{{SITEMAP_NS}}}loc"):
        url = loc.text.strip()
        path = urlparse(url).path
        if not path.endswith(".htm"):
            continue
        if any(path.startswith(p) for p in EXCLUDED_PREFIXES):
            continue
        urls.append(url)
    return urls


def derive_metadata(url: str) -> tuple[str, str]:
    """Return (category, breadcrumb) from URL path segments."""
    path = urlparse(url).path  # /Content/Content-Sources/Jira.htm
    parts = [p for p in path.split("/") if p and p != "Content"]
    if not parts:
        return "General", ""
    category = parts[0].replace("-", " ")
    breadcrumb = " › ".join(p.replace("-", " ").replace(".htm", "") for p in parts)
    return category, breadcrumb


def _find_content(soup: BeautifulSoup) -> Tag | None:
    """Find the main article container. Tries MadCap Flare-specific patterns."""
    # MadCap Flare primary pattern: <div id="mc-main-content" role="main">
    content = soup.find("div", {"id": "mc-main-content"})
    if content:
        return content
    # ARIA role=main is the most semantic fallback
    content = soup.find(attrs={"role": "main"})
    if content:
        return content
    # MadCap body container
    content = soup.find("div", class_="body-container")
    if content:
        return content
    return soup.find("body")


def _strip_noise(content: Tag) -> None:
    """Remove nav, scripts, banners, and MadCap-ignored blocks in-place."""
    for tag_name in _NOISE_TAGS:
        for tag in content.find_all(tag_name):
            tag.decompose()
    for tag in content.find_all(attrs={"data-mc-ignore": True}):
        tag.decompose()
    for css_class in _NOISE_CLASSES:
        for tag in content.find_all(class_=css_class):
            tag.decompose()


def _heading_anchor(tag: Tag) -> str:
    """Return anchor ID for a heading tag.

    MadCap Flare uses id='SomeName' and data-magellan-target='SomeName' on headings.
    These are the real anchor IDs that appear in the published URL's fragment.
    Falls back to slugifying the heading text only when neither is present.
    """
    anchor = tag.get("id", "").strip()
    if not anchor:
        anchor = tag.get("data-magellan-target", "").strip()
    if not anchor:
        anchor = re.sub(r"[^\w]+", "-", tag.get_text(strip=True).lower()).strip("-")[:60]
    return anchor


def _nodes_to_text(nodes: list[Tag]) -> str:
    """Convert a list of BeautifulSoup nodes to clean prose using markdownify.

    Why markdownify instead of get_text():
    - Tables → Markdown pipe tables (all columns visible in search index)
    - Code blocks → fenced blocks (preserves code without running words together)
    - Images → ![alt](url) which we convert to [Image: alt] inline

    strip=['a'] drops hyperlink URLs while keeping link text.
    """
    if not nodes:
        return ""
    combined_html = "".join(str(n) for n in nodes)
    raw = to_md(combined_html, strip=["a"], heading_style="ATX", bullets="*")
    # Convert markdown image syntax → [Image: alt] inline text
    raw = re.sub(
        r"!\[([^\]]*)\]\([^\)]*\)",
        lambda m: f"[Image: {m.group(1)}]" if m.group(1) else "",
        raw,
    )
    # Normalize non-breaking spaces and collapse excessive blank lines
    raw = raw.replace("\xa0", " ")
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def _walk_into(container: Tag, sections: list, state: dict) -> None:
    """Walk direct children of a container, splitting on heading tags."""
    for child in container.children:
        if not hasattr(child, "name") or not child.name:
            continue
        if child.name in HEADING_TAGS:
            _flush(sections, state)
            state["anchor"] = _heading_anchor(child)
            state["heading"] = f"{'#' * int(child.name[1])} {child.get_text(strip=True)}"
            state["nodes"] = []
        else:
            # If this non-heading child contains nested headings, recurse into it
            if child.find(HEADING_TAGS):
                _walk_into(child, sections, state)
            else:
                state["nodes"].append(child)


def _flush(sections: list, state: dict) -> None:
    """Flush current state into sections list if there's anything to save."""
    body = _nodes_to_text(state["nodes"])
    if body or state["heading"]:
        sections.append((state["anchor"], state["heading"], body))


def extract_sections(html: str) -> tuple[str, list[tuple[str, str, str]]]:
    """Return (page_title, [(anchor_id, heading_text, body_text), ...]).

    Splits content at every H1/H2/H3 boundary. Body text is clean Markdown
    produced by markdownify — images become [Image: alt], tables become
    Markdown pipe tables, code blocks get backtick fences.

    anchor_id comes from the heading's real HTML id attribute (MadCap Flare
    publishes these as URL fragments), enabling section-level deep-linking.
    """
    soup = BeautifulSoup(html, "lxml")

    title_tag = soup.find("h1") or soup.find("title")
    page_title = title_tag.get_text(strip=True) if title_tag else "Untitled"

    content = _find_content(soup)
    if not content:
        return page_title, []

    _strip_noise(content)

    sections: list[tuple[str, str, str]] = []
    state: dict = {"anchor": "", "heading": "", "nodes": []}

    _walk_into(content, sections, state)
    _flush(sections, state)

    return page_title, sections


async def fetch_page(client: httpx.AsyncClient, url: str) -> PageDoc | None:
    """Fetch a single page and extract sections. Returns None on failure."""
    try:
        resp = await client.get(url, timeout=15, follow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("fetch failed url=%s err=%s", url, exc)
        return None
    title, sections = extract_sections(resp.text)
    category, breadcrumb = derive_metadata(url)
    total_words = sum(len(body.split()) for _, _, body in sections)
    logger.debug("fetched url=%s sections=%d words=%d", url, len(sections), total_words)
    return PageDoc(url=url, title=title, category=category, breadcrumb=breadcrumb, sections=sections)


async def scrape_sitemap(sitemap_url: str, limit: int | None = None) -> list[PageDoc]:
    """Fetch sitemap then crawl all doc pages concurrently in batches of 10."""
    import asyncio

    async with httpx.AsyncClient(headers={"User-Agent": "su-docs-search/0.1"}) as client:
        resp = await client.get(sitemap_url, timeout=30)
        resp.raise_for_status()
        urls = parse_sitemap(resp.text)

    if limit:
        urls = urls[:limit]

    logger.info("scraping %d pages", len(urls))
    docs: list[PageDoc] = []

    async with httpx.AsyncClient(headers={"User-Agent": "su-docs-search/0.1"}) as client:
        for i in range(0, len(urls), 10):
            batch = urls[i : i + 10]
            results = await asyncio.gather(*[fetch_page(client, u) for u in batch])
            docs.extend(r for r in results if r is not None)
            logger.info("scraped %d/%d pages", len(docs), len(urls))

    return docs
