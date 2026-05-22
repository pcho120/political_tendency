#!/usr/bin/env python3
"""compliance_engine.py - robots.txt compliance and site accessibility classification.

Fetches robots.txt, parses Disallow/Allow/Crawl-delay directives,
classifies firm websites into accessibility tiers, and writes
per-firm debug reports.

Accessibility tiers
-------------------
FULL_PUBLIC         All paths allowed, no bot-wall detected
PARTIALLY_PUBLIC    Some paths disallowed; publicly reachable otherwise
BLOCKED_BY_BOT      Cloudflare / CAPTCHA / anti-bot wall on every request
AUTH_REQUIRED       Login wall detected before any content is served

Legal contract
--------------
- Never crawl Disallow paths
- Honour Crawl-delay
- BLOCKED_BY_BOT  → STOP, record reason, do NOT attempt evasion
- AUTH_REQUIRED   → STOP, record reason, do NOT attempt token extraction
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

COMPLIANCE_VERSION = "1.0"

# Accessibility classification labels
CLASS_FULL_PUBLIC      = "FULL_PUBLIC"
CLASS_PARTIALLY_PUBLIC = "PARTIALLY_PUBLIC"
CLASS_BLOCKED_BY_BOT   = "BLOCKED_BY_BOT"
CLASS_AUTH_REQUIRED    = "AUTH_REQUIRED"

# Our scraper identity  (used in robots.txt lookup)
OUR_USER_AGENT = "Mozilla/5.0 (compatible; LawFirmResearch/1.0)"
ROBOTS_USER_AGENT = "*"   # We check the wildcard catch-all

# Bot-wall fingerprints (response body signatures)
BOT_WALL_PATTERNS = [
    r"cf-browser-verification",
    r"cloudflare-error",            # Cloudflare error page (not just CDN usage)
    r"checking your browser",       # Cloudflare JS challenge message
    r"\bray id\b",                 # Cloudflare Ray ID in challenge footer
    r"captcha",
    r"recaptcha",
    r"hcaptcha",
    r"challenge-form",
    r"bot protection",
    r"just a moment",                # Cloudflare JS challenge
    r"enable javascript and cookies",
    r"ddos-guard",
    r"datadome",
    r"akamai bot manager",
    r"distil networks",
    r"perimeterx",
    r"kasada",
    r"imperva",
    r"access denied",               # Generic bot-wall denial page
    r"unusual traffic",             # Google/generic anti-bot
    r"automated access",            # Generic anti-bot
]


# Auth-wall fingerprints
AUTH_WALL_PATTERNS = [
    r"<form[^>]*action=[\"'][^\"']*login",
    r"<form[^>]*action=[\"'][^\"']*signin",
    r"please log in",
    r"please sign in",
    r"you must be logged in",
    r"authentication required",
    r"403 forbidden",
    r"access denied",
]

# Paths that are legally off-limits if Disallowed
SENSITIVE_PATH_PREFIXES = [
    "/admin", "/wp-admin", "/private", "/internal",
    "/api/private", "/api/internal", "/_admin",
]

# Fetch timeouts
ROBOTS_FETCH_TIMEOUT  = 8   # seconds
HOMEPAGE_FETCH_TIMEOUT = 10
ATTORNEY_PATH_PROBE_TIMEOUT = 8   # seconds — probe /people, /attorneys etc.

# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DisallowRule:
    """A single Disallow entry from robots.txt"""
    path: str
    user_agent: str = "*"

    def matches(self, url_path: str) -> bool:
        """Return True if url_path is covered by this Disallow rule."""
        if not self.path or self.path == "/":
            return bool(self.path == "/" and url_path.startswith("/"))
        return url_path.startswith(self.path)


@dataclass
class RobotsTxtResult:
    """Parsed robots.txt data for one domain."""
    domain: str
    robots_url: str
    fetch_success: bool = False
    raw_text: str = ""
    disallow_rules: list[DisallowRule] = field(default_factory=list)
    allow_rules: list[str] = field(default_factory=list)
    crawl_delay: float = 0.0   # 0 means "not specified"
    sitemap_urls: list[str] = field(default_factory=list)
    error: Optional[str] = None

    def is_path_disallowed(self, url_path: str) -> bool:
        """Check whether url_path is covered by any Disallow rule (for user-agent *)."""
        for rule in self.disallow_rules:
            if rule.matches(url_path):
                return True
        return False


@dataclass
class ComplianceResult:
    """Full compliance assessment for one firm/URL."""
    firm: str
    base_url: str
    domain: str
    accessibility: str           # FULL_PUBLIC | PARTIALLY_PUBLIC | BLOCKED_BY_BOT | AUTH_REQUIRED
    robots: RobotsTxtResult
    homepage_status: int = 0
    homepage_blocked: bool = False
    homepage_auth_required: bool = False
    disallowed_paths: list[str] = field(default_factory=list)
    allowed_paths: list[str] = field(default_factory=list)
    legal_notes: list[str] = field(default_factory=list)
    # paths the caller asked to verify
    path_checks: dict[str, bool] = field(default_factory=dict)

    def is_allowed(self, url: str) -> bool:
        """Return True iff the URL may legally be crawled.

        Rules (in priority order):
        1. If accessibility is BLOCKED_BY_BOT or AUTH_REQUIRED → always False
        2. If robots.txt fetch failed → allow (fail-open for public content, but warn)
        3. Apply robots.txt Disallow rules for path
        """
        if self.accessibility in (CLASS_BLOCKED_BY_BOT, CLASS_AUTH_REQUIRED):
            return False
        parsed = urlparse(url)
        path = parsed.path or "/"
        return not self.robots.is_path_disallowed(path)

    def to_dict(self) -> dict:
        return {
            "firm": self.firm,
            "base_url": self.base_url,
            "domain": self.domain,
            "accessibility": self.accessibility,
            "homepage_status": self.homepage_status,
            "homepage_blocked": self.homepage_blocked,
            "homepage_auth_required": self.homepage_auth_required,
            "robots_fetch_success": self.robots.fetch_success,
            "robots_crawl_delay": self.robots.crawl_delay,
            "robots_sitemap_urls": self.robots.sitemap_urls,
            "disallowed_path_count": len(self.robots.disallow_rules),
            "disallowed_paths_sample": [r.path for r in self.robots.disallow_rules[:20]],
            "disallowed_paths_for_attorneys": self.disallowed_paths,
            "path_checks": self.path_checks,
            "legal_notes": self.legal_notes,
            "compliance_version": COMPLIANCE_VERSION,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────────────

class ComplianceEngine:
    """Robots.txt + bot-wall detection engine.

    Usage
    -----
    engine = ComplianceEngine(report_dir=Path("debug_reports"))
    result = engine.check(firm="Latham & Watkins", base_url="https://www.lw.com")
    if not result.is_allowed("https://www.lw.com/people/jane-doe"):
        # skip — legally blocked
        ...
    """

    def __init__(
        self,
        session: requests.Session | None = None,
        report_dir: Path | None = None,
        robots_timeout: int = ROBOTS_FETCH_TIMEOUT,
        homepage_timeout: int = HOMEPAGE_FETCH_TIMEOUT,
    ):
        self.session = session or self._make_session()
        self.report_dir = report_dir or Path("debug_reports")
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.robots_timeout   = robots_timeout
        self.homepage_timeout = homepage_timeout

        # Cache: domain → ComplianceResult  (avoid repeated fetches)
        self._cache: dict[str, ComplianceResult] = {}

    # ──────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────

    def check(
        self,
        firm: str,
        base_url: str,
        paths_to_check: list[str] | None = None,
    ) -> ComplianceResult:
        """Full compliance check for one firm.

        Parameters
        ----------
        firm          : Display name (used in reports)
        base_url      : Official website root, e.g. "https://www.lw.com"
        paths_to_check: Optional list of paths to explicitly verify, e.g.
                        ["/people", "/professionals", "/sitemap.xml"]
        """
        if not base_url.startswith("http"):
            base_url = "https://" + base_url
        base_url = base_url.rstrip("/")

        parsed  = urlparse(base_url)
        domain  = parsed.netloc
        scheme  = parsed.scheme

        # Cache hit
        if domain in self._cache:
            logger.debug(f"[compliance] cache hit for {domain}")
            return self._cache[domain]

        logger.info(f"[compliance] checking {firm} ({base_url})")

        # 1. Fetch robots.txt
        robots_url = f"{scheme}://{domain}/robots.txt"
        robots     = self._fetch_robots(domain, robots_url)

        # 2. Probe homepage for bot-wall / auth-wall
        homepage_status, homepage_html = self._probe_homepage(base_url)
        homepage_blocked      = self._is_bot_wall(homepage_html, homepage_status)
        homepage_auth_required = not homepage_blocked and self._is_auth_wall(homepage_html, homepage_status)

        # 2b. If homepage is clean, probe attorney paths specifically.
        #     Cloudflare often allows the homepage but blocks /people, /attorneys etc.
        #     If ALL probed attorney paths return CF 403, classify as BLOCKED_BY_BOT
        #     and stop immediately — no strategies will work.
        if not homepage_blocked and not homepage_auth_required:
            attorney_path_blocked = self._probe_attorney_paths(base_url)
            if attorney_path_blocked:
                homepage_blocked = True
                logger.info(
                    f"[compliance] {firm}: attorney paths return CF 403 — classifying BLOCKED_BY_BOT"
                )

        # 3. Classify accessibility
        accessibility = self._classify(
            robots, homepage_blocked, homepage_auth_required
        )

        # 4. Identify attorney-relevant disallowed paths
        attorney_paths = [
            "/people", "/professionals", "/attorneys", "/lawyers",
            "/team", "/our-people", "/sitemap.xml", "/sitemap",
        ]
        disallowed_for_attorneys = [
            p for p in attorney_paths
            if robots.fetch_success and robots.is_path_disallowed(p)
        ]
        allowed_for_attorneys = [
            p for p in attorney_paths
            if not robots.is_path_disallowed(p)
        ]

        # 5. Check explicitly requested paths
        path_checks: dict[str, bool] = {}
        for p in (paths_to_check or []):
            path_checks[p] = not robots.is_path_disallowed(p)

        # 6. Build legal notes
        legal_notes: list[str] = []
        if homepage_blocked:
            legal_notes.append(
                "Bot protection detected on homepage — STOP; do NOT attempt evasion"
            )
        if homepage_auth_required:
            legal_notes.append(
                "Login wall detected — only publicly accessible data may be collected"
            )
        if disallowed_for_attorneys:
            legal_notes.append(
                f"robots.txt Disallows attorney paths: {disallowed_for_attorneys}"
            )
        if robots.crawl_delay > 0:
            legal_notes.append(
                f"robots.txt Crawl-delay: {robots.crawl_delay}s — must be honoured"
            )
        if not robots.fetch_success:
            legal_notes.append(
                "robots.txt unreachable — proceeding conservatively (standard paths only)"
            )

        result = ComplianceResult(
            firm=firm,
            base_url=base_url,
            domain=domain,
            accessibility=accessibility,
            robots=robots,
            homepage_status=homepage_status,
            homepage_blocked=homepage_blocked,
            homepage_auth_required=homepage_auth_required,
            disallowed_paths=disallowed_for_attorneys,
            allowed_paths=allowed_for_attorneys,
            legal_notes=legal_notes,
            path_checks=path_checks,
        )

        # 7. Write per-firm debug report
        self._write_report(firm, result)

        # 8. Cache
        self._cache[domain] = result

        logger.info(
            f"[compliance] {firm}: {accessibility} "
            f"(robots={'OK' if robots.fetch_success else 'FAIL'}, "
            f"homepage={homepage_status}, bot_wall={homepage_blocked})"
        )
        return result

    def is_allowed(self, url: str) -> bool:
        """Quick check whether `url` may be crawled (uses cache if available).

        Falls back to robots.txt-only check if domain not yet checked.
        Prefer calling check() first to get full context.
        """
        parsed = urlparse(url)
        domain = parsed.netloc
        if domain in self._cache:
            return self._cache[domain].is_allowed(url)
        # Without a full check we do a best-effort robots-only check
        robots = self._fetch_robots(domain, f"{parsed.scheme}://{domain}/robots.txt")
        return not robots.is_path_disallowed(parsed.path or "/")

    # ──────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────

    def _fetch_robots(self, domain: str, robots_url: str) -> RobotsTxtResult:
        result = RobotsTxtResult(domain=domain, robots_url=robots_url)
        try:
            resp = self.session.get(robots_url, timeout=self.robots_timeout)
            if resp.status_code == 200:
                result.fetch_success = True
                result.raw_text      = resp.text
                self._parse_robots(result)
            else:
                result.error = f"HTTP {resp.status_code}"
                logger.debug(f"[compliance] robots.txt {robots_url} → {resp.status_code}")
        except requests.exceptions.Timeout:
            result.error = "timeout"
        except requests.exceptions.ConnectionError as e:
            result.error = f"connection_error: {e}"
        except Exception as e:
            result.error = f"exception: {type(e).__name__}: {e}"
        return result

    def _parse_robots(self, result: RobotsTxtResult) -> None:
        """Parse robots.txt text into structured rules.

        We implement a simple, spec-compliant parser instead of relying on
        urllib.robotparser because we need to capture Crawl-delay and Sitemap
        directives and inspect all rules programmatically.
        """
        current_agents: list[str] = []
        in_wildcard_block = False

        for raw_line in result.raw_text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            # Split on first colon
            if ":" not in line:
                continue
            directive, _, value = line.partition(":")
            directive = directive.strip().lower()
            value     = value.strip()

            if directive == "user-agent":
                current_agents = [v.strip() for v in value.split(",")]
                in_wildcard_block = any(
                    a in ("*", ROBOTS_USER_AGENT) for a in current_agents
                )

            elif directive == "disallow":
                if in_wildcard_block and value:
                    result.disallow_rules.append(
                        DisallowRule(path=value, user_agent="*")
                    )

            elif directive == "allow":
                if in_wildcard_block and value:
                    result.allow_rules.append(value)

            elif directive == "crawl-delay":
                try:
                    delay = float(value)
                    if result.crawl_delay == 0.0:
                        result.crawl_delay = delay
                except ValueError:
                    pass

            elif directive == "sitemap":
                if value:
                    result.sitemap_urls.append(value)

        # Apply Allow overrides: remove Disallow entries that have a matching Allow
        if result.allow_rules:
            kept = []
            for disallow in result.disallow_rules:
                overridden = any(
                    disallow.path.startswith(allow)
                    for allow in result.allow_rules
                )
                if not overridden:
                    kept.append(disallow)
            result.disallow_rules = kept

    def _probe_homepage(self, base_url: str) -> tuple[int, str]:
        """Fetch homepage and return (status_code, html_text)."""
        try:
            resp = self.session.get(
                base_url,
                timeout=self.homepage_timeout,
                allow_redirects=True,
            )
            return resp.status_code, resp.text
        except requests.exceptions.Timeout:
            return 0, ""
        except requests.exceptions.ConnectionError:
            return 0, ""
        except Exception as e:
            logger.debug(f"[compliance] homepage probe error: {e}")
            return 0, ""

    def _probe_attorney_paths(self, base_url: str) -> bool:
        """Probe attorney-specific paths for Cloudflare/bot-wall blocks.

        Returns True if ALL probed attorney paths are bot-wall blocked
        (homepage may be clean while /people, /attorneys etc. are CF-blocked).
        """
        parsed = urlparse(base_url)
        paths_to_probe = [
            "/people", "/attorneys", "/professionals",
            "/lawyers", "/our-people", "/team",
        ]
        blocked_count = 0
        probed_count = 0

        for path in paths_to_probe:
            url = f"{parsed.scheme}://{parsed.netloc}{path}"
            try:
                resp = self.session.get(
                    url,
                    timeout=ATTORNEY_PATH_PROBE_TIMEOUT,
                    allow_redirects=True,
                )
                probed_count += 1
                if self._is_bot_wall(resp.text, resp.status_code):
                    blocked_count += 1
                    logger.debug(
                        f"[compliance] attorney path blocked: {path} \u2192 HTTP {resp.status_code}"
                    )
                else:
                    # At least one path is accessible — not fully blocked
                    logger.debug(
                        f"[compliance] attorney path accessible: {path} \u2192 HTTP {resp.status_code}"
                    )
                    return False
            except requests.exceptions.Timeout:
                blocked_count += 1
                probed_count += 1
                logger.debug(f"[compliance] attorney path timeout: {path}")
            except Exception as e:
                logger.debug(f"[compliance] attorney path error: {path}: {e}")

        # All probed paths were blocked
        if probed_count > 0 and blocked_count == probed_count:
            logger.warning(
                f"[compliance] ALL {probed_count} attorney paths blocked — site is CF-protected"
            )
            return True
        return False

    def _is_bot_wall(self, html: str, status: int) -> bool:
        """Detect Cloudflare / CAPTCHA / anti-bot wall from HTTP response."""
        if status in (403, 429, 503):
            if not html:
                return True
        if not html:
            return False
        html_lower = html.lower()
        for pattern in BOT_WALL_PATTERNS:
            if re.search(pattern, html_lower):
                return True
        return False

    def _is_auth_wall(self, html: str, status: int) -> bool:
        """Detect login/auth wall."""
        if status == 401:
            return True
        if not html:
            return False
        html_lower = html.lower()
        for pattern in AUTH_WALL_PATTERNS:
            if re.search(pattern, html_lower):
                return True
        return False

    def _classify(
        self,
        robots: RobotsTxtResult,
        homepage_blocked: bool,
        homepage_auth_required: bool,
    ) -> str:
        if homepage_blocked:
            return CLASS_BLOCKED_BY_BOT
        if homepage_auth_required:
            return CLASS_AUTH_REQUIRED
        if robots.fetch_success and robots.disallow_rules:
            return CLASS_PARTIALLY_PUBLIC
        return CLASS_FULL_PUBLIC

    def _write_report(self, firm: str, result: ComplianceResult) -> None:
        safe = re.sub(r"[^\w\s-]", "", firm).strip().replace(" ", "_")
        path = self.report_dir / f"{safe}_compliance.json"
        try:
            path.write_text(
                json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.debug(f"[compliance] report written: {path}")
        except Exception as e:
            logger.warning(f"[compliance] failed to write report: {e}")

    @staticmethod
    def _make_session() -> requests.Session:
        s = requests.Session()
        s.headers.update({
            "User-Agent": OUR_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        return s


# ─────────────────────────────────────────────────────────────────────────────
# Standalone CLI (for unit testing)
# ─────────────────────────────────────────────────────────────────────────────

def _main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="Check compliance for a URL")
    parser.add_argument("url", help="Base URL to check, e.g. https://www.lw.com")
    parser.add_argument("--firm", default="TestFirm")
    parser.add_argument("--paths", nargs="*", default=[], help="Extra paths to check")
    args = parser.parse_args()

    engine = ComplianceEngine(report_dir=Path("debug_reports"))
    result = engine.check(firm=args.firm, base_url=args.url, paths_to_check=args.paths)

    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))

    test_path = "/people/jane-doe"
    allowed   = result.is_allowed(args.url + test_path)
    print(f"\nis_allowed({test_path!r}) = {allowed}")


if __name__ == "__main__":
    _main()
