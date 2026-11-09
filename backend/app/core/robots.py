"""
robots.txt

Served by the backend instead of as a static file of the frontend because the paths must carry
settings.base_path: under a sub-path the rules read e.g. "Disallow: /lola/c/". Crawlers only read
robots.txt at the root of a host, so under a sub-path these lines belong in the host's own file.
"""

from fastapi.responses import PlainTextResponse

from app.core.urls import API_PREFIX, site_url

# The public chat is kept out because a rendering crawler would autoplay the greeting (TTS) and
# download the avatar and speech models; the rest has nothing worth finding. Caddy also sends
# X-Robots-Tag: noindex for /c/* for bots that skip robots.txt (see docker/Caddyfile).
DISALLOWED_PATHS = (
    "/c/",
    "/dashboard",
    "/login",
    "/register",
    "/forgot-password",
    "/reset-password",
    f"{API_PREFIX}/",
    "/models/",
)


def robots_txt() -> PlainTextResponse:
    lines = ["User-agent: *", *(f"Disallow: {site_url(path)}" for path in DISALLOWED_PATHS)]
    return PlainTextResponse("\n".join(lines) + "\n")
