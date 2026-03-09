#!/usr/bin/env python3
"""rate_limit_manager.py - Per-domain rate limiting for all HTTP/Playwright requests.

Replaces the ad-hoc domain_last_request dict scattered throughout find_attorney.py.
Works for both requests-based and Playwright-based access — callers
call `wait(domain)` before every outbound request to that domain.

Policy
------
- Minimum inter-request delay per domain: configurable (default 1.0 s)
- Crawl-delay from robots.txt overrides default when larger
- Concurrent request cap per domain: configurable (default 1)
- Respect: if BLOCKED_BY_BOT → raise RateLimitBlockedError immediately

Legal contract
--------------
- Never submit requests faster than the rate dictated by robots.txt Crawl-delay
- If bot protection is confirmed, do NOT retry or rotate; surface the block
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Defaults
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_DELAY       = 1.0   # seconds between requests per domain
DEFAULT_CONCURRENCY = 1     # max simultaneous requests per domain
MAX_REASONABLE_DELAY = 30.0  # cap robots.txt Crawl-delay at 30 s


# ─────────────────────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────────────────────

class RateLimitBlockedError(RuntimeError):
    """Raised when a domain is known to have active bot protection.

    Callers MUST catch this and record a SourceFailure — do NOT retry,
    rotate IPs, or attempt evasion.
    """
    def __init__(self, domain: str, reason: str = ""):
        self.domain = domain
        self.reason = reason
        super().__init__(
            f"Domain {domain!r} is blocked (bot protection / rate-limited). "
            f"Reason: {reason}. STOP — do NOT attempt evasion."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Per-domain state
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DomainPolicy:
    """Rate-limiting policy and state for one domain."""
    domain: str
    # Minimum seconds between successive requests
    min_delay: float = DEFAULT_DELAY
    # Max concurrent requests (enforced via semaphore)
    max_concurrent: int = DEFAULT_CONCURRENCY
    # Set when bot protection is confirmed for this domain
    blocked: bool = False
    block_reason: str = ""
    # Internal bookkeeping
    _last_request_time: float = field(default=0.0, init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )
    _semaphore: Optional[threading.Semaphore] = field(
        default=None, init=False, repr=False
    )
    # Stats
    total_requests: int = 0
    total_wait_seconds: float = 0.0

    def __post_init__(self) -> None:
        self._semaphore = threading.Semaphore(self.max_concurrent)

    def apply_crawl_delay(self, crawl_delay: float) -> None:
        """Override min_delay from robots.txt Crawl-delay directive."""
        if crawl_delay > 0:
            effective = min(crawl_delay, MAX_REASONABLE_DELAY)
            if effective > self.min_delay:
                self.min_delay = effective
                logger.debug(
                    f"[rate_limit] {self.domain}: Crawl-delay → {effective:.1f}s"
                )

    def mark_blocked(self, reason: str = "") -> None:
        """Mark this domain as bot-protected. Future wait() calls raise."""
        self.blocked = True
        self.block_reason = reason or "bot_protection"
        logger.warning(
            f"[rate_limit] {self.domain} marked BLOCKED: {self.block_reason}"
        )

    def wait(self) -> None:
        """Block until it is safe to make the next request to this domain.

        Raises RateLimitBlockedError if the domain has been marked blocked.
        Thread-safe: multiple workers share a single DomainPolicy instance.
        """
        if self.blocked:
            raise RateLimitBlockedError(self.domain, self.block_reason)

        # Enforce concurrency cap
        assert self._semaphore is not None
        self._semaphore.acquire()
        try:
            with self._lock:
                elapsed = time.monotonic() - self._last_request_time
                wait_time = self.min_delay - elapsed
                if wait_time > 0:
                    self.total_wait_seconds += wait_time
                    time.sleep(wait_time)
                self._last_request_time = time.monotonic()
                self.total_requests += 1
        finally:
            self._semaphore.release()

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "min_delay": self.min_delay,
            "max_concurrent": self.max_concurrent,
            "blocked": self.blocked,
            "block_reason": self.block_reason,
            "total_requests": self.total_requests,
            "total_wait_seconds": round(self.total_wait_seconds, 2),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Manager
# ─────────────────────────────────────────────────────────────────────────────

class RateLimitManager:
    """Global registry of per-domain rate-limit policies.

    Usage
    -----
    rlm = RateLimitManager(default_delay=1.0)

    # From compliance engine, apply robots.txt crawl-delay
    rlm.apply_crawl_delay("www.lw.com", crawl_delay=2.0)

    # Before every HTTP request (requests or Playwright)
    rlm.wait("www.lw.com")
    resp = session.get("https://www.lw.com/people/...")

    # If bot protection detected mid-run
    rlm.mark_blocked("www.lw.com", reason="HTTP 403 + Cloudflare body")

    Raises RateLimitBlockedError if domain is marked blocked.
    """

    def __init__(
        self,
        default_delay: float = DEFAULT_DELAY,
        default_concurrency: int = DEFAULT_CONCURRENCY,
    ) -> None:
        self._default_delay       = default_delay
        self._default_concurrency = default_concurrency
        self._policies: dict[str, DomainPolicy] = {}
        self._global_lock = threading.Lock()

    # ──────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────

    def wait(self, domain: str) -> None:
        """Wait until the next request to `domain` is safe.

        Call this before EVERY outbound request — requests-based or Playwright.
        """
        self._get_policy(domain).wait()

    def wait_for_url(self, url: str) -> None:
        """Convenience: extract domain from URL, then wait."""
        from urllib.parse import urlparse
        domain = urlparse(url).netloc
        self.wait(domain)

    def apply_crawl_delay(self, domain: str, crawl_delay: float) -> None:
        """Apply Crawl-delay from robots.txt to a specific domain."""
        self._get_policy(domain).apply_crawl_delay(crawl_delay)

    def apply_compliance(self, compliance_result) -> None:
        """Apply crawl-delay and blocked status from a ComplianceResult.

        Accepts compliance_engine.ComplianceResult objects.
        """
        domain = compliance_result.domain
        # Apply crawl-delay
        if compliance_result.robots.crawl_delay > 0:
            self.apply_crawl_delay(domain, compliance_result.robots.crawl_delay)
        # Mark blocked if site is not publicly accessible
        from compliance_engine import CLASS_BLOCKED_BY_BOT, CLASS_AUTH_REQUIRED
        if compliance_result.accessibility in (CLASS_BLOCKED_BY_BOT, CLASS_AUTH_REQUIRED):
            self.mark_blocked(domain, reason=compliance_result.accessibility)

    def mark_blocked(self, domain: str, reason: str = "") -> None:
        """Permanently mark a domain as blocked — future wait() raises."""
        self._get_policy(domain).mark_blocked(reason)

    def is_blocked(self, domain: str) -> bool:
        """Return True if domain has active bot protection."""
        return self._policies.get(domain, DomainPolicy(domain=domain)).blocked

    def set_delay(self, domain: str, delay: float) -> None:
        """Override minimum delay for a specific domain (e.g. during debug)."""
        self._get_policy(domain).min_delay = delay

    def stats(self) -> list[dict]:
        """Return serialisable per-domain stats."""
        return [p.to_dict() for p in self._policies.values()]

    def reset_domain(self, domain: str) -> None:
        """Remove policy for a domain (useful in tests)."""
        with self._global_lock:
            self._policies.pop(domain, None)

    # ──────────────────────────────────────────────────────────────────────
    # Internal
    # ──────────────────────────────────────────────────────────────────────

    def _get_policy(self, domain: str) -> DomainPolicy:
        """Get (or lazily create) policy for a domain."""
        policy = self._policies.get(domain)
        if policy is not None:
            return policy
        with self._global_lock:
            # Double-checked locking
            policy = self._policies.get(domain)
            if policy is None:
                policy = DomainPolicy(
                    domain=domain,
                    min_delay=self._default_delay,
                    max_concurrent=self._default_concurrency,
                )
                self._policies[domain] = policy
                logger.debug(
                    f"[rate_limit] new policy for {domain} "
                    f"(delay={self._default_delay}s)"
                )
        return policy


# ─────────────────────────────────────────────────────────────────────────────
# Standalone test
# ─────────────────────────────────────────────────────────────────────────────

def _main() -> None:
    import argparse
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="Rate-limit manager smoke test")
    parser.add_argument("--domain", default="www.example.com")
    parser.add_argument("--requests", type=int, default=5)
    parser.add_argument("--delay", type=float, default=0.2)
    args = parser.parse_args()

    rlm = RateLimitManager(default_delay=args.delay)
    print(f"Sending {args.requests} requests to {args.domain} "
          f"with {args.delay}s min delay...")

    t0 = time.monotonic()
    for i in range(args.requests):
        rlm.wait(args.domain)
        print(f"  request {i+1} at t={time.monotonic() - t0:.2f}s")

    print(f"\nStats: {rlm.stats()}")


if __name__ == "__main__":
    _main()
