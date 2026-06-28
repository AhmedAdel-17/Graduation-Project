"""
Scrapling smoke test against Egyptian-finance Facebook groups.

Goal: empirically answer the question "can Scrapling read posts from these
groups without an authenticated cookie jar?". Tries the cheap path (Fetcher
with TLS impersonation) first, then the expensive path (StealthyFetcher
headless browser). Reports HTTP status, final URL, page length, login-wall
detection, and a count of post-shaped containers it could see.

Install:
    pip install scrapling
    scrapling install        # downloads Playwright + stealth deps

Run:
    python scripts/test_scrapling_facebook.py
    python scripts/test_scrapling_facebook.py --browser   # browser path too
    python scripts/test_scrapling_facebook.py --cookies path/to/storage_state.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

GROUPS = {
    "جروب الخبره":                      "https://www.facebook.com/groups/618025406208276/",
    "البورصة المصرية":                  "https://www.facebook.com/groups/4021602644518797",
    "بورصة مصر - Egypt Stock Exchange": "https://www.facebook.com/groups/955090341273238/",
    "اسهم عليها العين":                 "https://www.facebook.com/groups/1445825095646831/",
    "بورصة الغد":                       "https://www.facebook.com/groups/428469887311443",
}

LOGIN_WALL_MARKERS = (
    "login_form", "loginform", "Log in to Facebook", "تسجيل الدخول",
    "You must log in", "checkpoint", '"loginButton"',
)
POST_HINT_SELECTORS = [
    'div[role="article"]',
    'div[data-pagelet^="GroupFeed"]',
    'div[data-ad-preview="message"]',
]


@dataclass
class Result:
    name: str
    url: str
    method: str
    status: int | None
    final_url: str | None
    html_len: int
    login_wall: bool
    post_candidates: int
    sample_text: str
    error: str | None = None


def detect_login_wall(html: str) -> bool:
    lower = html.lower()
    return any(m.lower() in lower for m in LOGIN_WALL_MARKERS)


def count_posts(page) -> tuple[int, str]:
    best_n, sample = 0, ""
    for sel in POST_HINT_SELECTORS:
        try:
            nodes = page.css(sel)
        except Exception:
            continue
        if nodes and len(nodes) > best_n:
            best_n = len(nodes)
            try:
                sample = (nodes[0].text or "").strip()[:240]
            except Exception:
                sample = ""
    return best_n, sample


def try_http(name: str, url: str) -> Result:
    from scrapling.fetchers import Fetcher
    try:
        page = Fetcher.get(url, stealthy_headers=True, follow_redirects=True, timeout=30)
        html = page.html_content if hasattr(page, "html_content") else str(page)
        n, sample = count_posts(page)
        return Result(
            name=name, url=url, method="Fetcher",
            status=getattr(page, "status", None),
            final_url=getattr(page, "url", None),
            html_len=len(html),
            login_wall=detect_login_wall(html),
            post_candidates=n,
            sample_text=sample,
        )
    except Exception as e:
        return Result(name, url, "Fetcher", None, None, 0, False, 0, "", error=repr(e))


def try_browser(name: str, url: str, cookies: str | None) -> Result:
    from scrapling.fetchers import StealthyFetcher
    kwargs = dict(
        headless=True,
        network_idle=True,
        humanize=True,
        block_images=True,
        wait=4000,
        timeout=60000,
    )
    if cookies:
        # Scrapling accepts a Playwright storage_state path via load_dom_cookies
        kwargs["load_dom_cookies"] = cookies
    try:
        page = StealthyFetcher.fetch(url, **kwargs)
        html = page.html_content if hasattr(page, "html_content") else str(page)
        n, sample = count_posts(page)
        return Result(
            name=name, url=url, method="StealthyFetcher",
            status=getattr(page, "status", None),
            final_url=getattr(page, "url", None),
            html_len=len(html),
            login_wall=detect_login_wall(html),
            post_candidates=n,
            sample_text=sample,
        )
    except Exception as e:
        return Result(name, url, "StealthyFetcher", None, None, 0, False, 0, "", error=repr(e))


def pretty(r: Result) -> str:
    head = f"[{r.method}] {r.name}"
    if r.error:
        return f"{head}\n  ERROR: {r.error}\n"
    return (
        f"{head}\n"
        f"  url           : {r.url}\n"
        f"  final_url     : {r.final_url}\n"
        f"  status        : {r.status}\n"
        f"  html_len      : {r.html_len}\n"
        f"  login_wall    : {r.login_wall}\n"
        f"  post_candidates: {r.post_candidates}\n"
        f"  sample        : {r.sample_text[:160]!r}\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--browser", action="store_true",
                    help="Also try StealthyFetcher (headless browser, slow).")
    ap.add_argument("--cookies", default=None,
                    help="Path to a Playwright storage_state.json with FB cookies.")
    ap.add_argument("--out", default="logs/scrapling_fb_test.json",
                    help="Where to dump structured results.")
    args = ap.parse_args()

    try:
        import scrapling  # noqa: F401
    except ImportError:
        print("Scrapling not installed. Run: pip install scrapling && scrapling install",
              file=sys.stderr)
        return 2

    results: list[Result] = []
    for name, url in GROUPS.items():
        r = try_http(name, url)
        print(pretty(r))
        results.append(r)
        time.sleep(1.5)

    if args.browser:
        print("\n=== Browser pass (StealthyFetcher) ===\n")
        for name, url in GROUPS.items():
            r = try_browser(name, url, args.cookies)
            print(pretty(r))
            results.append(r)
            time.sleep(2.0)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"\nWrote {out_path}")

    # Honest verdict
    useful = [r for r in results if not r.login_wall and r.post_candidates > 0 and not r.error]
    print("\n=== Verdict ===")
    if useful:
        print(f"{len(useful)}/{len(results)} attempts saw post-shaped content without a login wall.")
    else:
        print("No attempt saw real group posts without a login wall.")
        print("Expected: Facebook groups (even 'public' ones) gate post HTML behind login.")
        print("Next step: capture cookies via Playwright (logged-in FB account) and re-run with --cookies.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
