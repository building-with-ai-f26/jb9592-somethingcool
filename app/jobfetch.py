"""Fetch a job posting URL and turn it into plain readable text.

Order of preference:
  1. JSON-LD `JobPosting` data embedded in the page (Ashby, Lever, many ATSs).
     This works even when the page itself is rendered by JavaScript.
  2. The page's visible text with scripts, styles, and navigation stripped out.

If neither gives enough text, we raise FetchError and the UI asks the user
to paste the job description instead.
"""

import ipaddress
import json
import re
import socket
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

MIN_CHARS = 400  # anything shorter is probably a login wall or an empty JS shell
MAX_BYTES = 3_000_000  # biggest real posting seen: ~0.7 MB (Lever)
MAX_REDIRECTS = 5

# Non-goal: Tailor does not scrape LinkedIn. Go straight to the paste fallback.
BLOCKED_HOSTS = ("linkedin.com",)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130 Safari/537.36"
)


class FetchError(Exception):
    pass


def _clean(text: str) -> str:
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _html_to_text(html: str) -> str:
    return _clean(BeautifulSoup(html, "html.parser").get_text("\n"))


def _from_json_ld(soup: BeautifulSoup) -> str | None:
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict) or item.get("@type") != "JobPosting":
                continue
            org = item.get("hiringOrganization") or {}
            company = org.get("name", "") if isinstance(org, dict) else ""
            parts = [
                f"Title: {item.get('title', '')}",
                f"Company: {company}",
                _html_to_text(item.get("description", "")),
            ]
            return "\n\n".join(p for p in parts if p.strip())
    return None


def extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    from_ld = _from_json_ld(soup)
    if from_ld and len(from_ld) >= MIN_CHARS:
        return from_ld
    for tag in soup(["script", "style", "noscript", "svg", "nav", "header", "footer", "form"]):
        tag.decompose()
    return _clean(soup.get_text("\n"))


def _resolve(host: str) -> set[str]:
    return {info[4][0] for info in socket.getaddrinfo(host, None)}


def _check_public_url(url: str) -> None:
    """Refuse anything but http(s) links to public internet addresses.

    On a public server, the job-URL box would otherwise let anyone make Tailor fetch
    internal addresses (localhost, the cloud metadata service, private networks): SSRF.
    """
    parts = urlparse(url)
    if parts.scheme not in ("http", "https"):
        raise FetchError("Only http(s) links work.")
    host = (parts.hostname or "").lower()
    if not host:
        raise FetchError("That doesn't look like a URL.")
    if any(host == h or host.endswith("." + h) for h in BLOCKED_HOSTS):
        raise FetchError("Tailor doesn't scrape LinkedIn.")  # UI adds the "paste instead" prompt
    try:
        ips = _resolve(host)
    except (socket.gaierror, UnicodeError) as e:
        raise FetchError("Couldn't find that website.") from e
    for ip in ips:
        addr = ipaddress.ip_address(ip.split("%")[0])
        if addr.version == 6 and addr.ipv4_mapped:
            addr = addr.ipv4_mapped
        if not addr.is_global:
            raise FetchError("That link points to a private or local address, which Tailor won't fetch.")


def fetch_job_text(url: str, transport: httpx.BaseTransport | None = None) -> str:
    # Follow redirects by hand so every hop gets the same public-address check.
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=15, follow_redirects=False, transport=transport) as client:
        for _ in range(MAX_REDIRECTS + 1):
            _check_public_url(url)
            try:
                with client.stream("GET", url) as resp:
                    if resp.is_redirect:
                        url = urljoin(url, resp.headers.get("location", ""))
                        continue
                    if resp.status_code != 200:
                        raise FetchError(f"The page returned HTTP {resp.status_code}.")
                    body = b""
                    for chunk in resp.iter_bytes():
                        body += chunk
                        if len(body) >= MAX_BYTES:  # don't download huge files
                            break
                    html = body[:MAX_BYTES].decode(resp.encoding or "utf-8", errors="replace")
                    break
            except httpx.HTTPError as e:
                raise FetchError(f"Couldn't reach the page ({type(e).__name__}).") from e
        else:
            raise FetchError("Too many redirects.")
    text = extract_text(html)
    if len(text) < MIN_CHARS:
        raise FetchError("The page loaded but had almost no readable text (probably blocked or JavaScript-only).")
    return text[:20000]  # cap what we send to the model
