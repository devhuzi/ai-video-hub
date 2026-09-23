"""
scrape_social_page.py
=====================
Scrapes the 20 most recent videos from a YouTube, TikTok, or Facebook page
using the appropriate Apify actor.

Usage:
    python execution/scrape_social_page.py <page_url> [--session <name>] [--limit <n>]

Output:
    .tmp/<session>/videos_metadata.json

Apify Actors Used:
    YouTube  → apify/youtube-scraper
    TikTok   → clockworks/tiktok-profile-scraper
    Facebook → apify/facebook-posts-scraper (filters to video posts)

Requirements:
    pip install apify-client python-dotenv
"""

import sys
import os
import json
import re
import argparse
import time
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv

# Load env from project root
SCRIPT_DIR = Path(__file__).parent
ROOT_DIR = SCRIPT_DIR.parent
load_dotenv(ROOT_DIR / ".env")

APIFY_TOKEN = os.environ.get("APIFY_API_TOKEN", "")
if not APIFY_TOKEN:
    print("ERROR: APIFY_API_TOKEN not set in .env")
    sys.exit(1)

try:
    from apify_client import ApifyClient
except ImportError:
    print("ERROR: apify-client not installed. Run: pip install apify-client")
    sys.exit(1)


# ─────────────────────────────────────────────
# Platform Detection
# ─────────────────────────────────────────────

def detect_platform(url: str) -> str:
    url_lower = url.lower()
    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "youtube"
    elif "tiktok.com" in url_lower:
        return "tiktok"
    elif "facebook.com" in url_lower or "fb.com" in url_lower:
        return "facebook"
    else:
        raise ValueError(f"Unsupported platform URL: {url}")


# ─────────────────────────────────────────────
# YouTube Scraper
# ─────────────────────────────────────────────

def scrape_youtube(client: ApifyClient, url: str, limit: int) -> list[dict]:
    print(f"[YouTube] Scraping channel: {url}")
    print(f"[YouTube] Actor: apify/youtube-scraper | Limit: {limit} videos")

    run = client.actor("apify/youtube-scraper").call(run_input={
        "startUrls": [{"url": url}],
        "maxResults": limit,
        "maxResultsShorts": 0,
        "maxResultStreams": 0,
        "subtitlesLanguage": "any",
        "subtitlesFormat": "plaintext",
    })

    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    print(f"[YouTube] Raw items returned: {len(items)}")

    results = []
    for i, item in enumerate(items[:limit]):
        results.append({
            "index": i + 1,
            "platform": "youtube",
            "title": item.get("title", ""),
            "description": item.get("text", ""),
            "hashtags": [],
            "url": item.get("url", ""),
            "download_url": item.get("url", ""),  # yt-dlp will handle download
            "thumbnail_url": item.get("thumbnailUrl", ""),
            "views": item.get("viewCount", 0),
            "likes": item.get("likes", 0),
            "duration": item.get("duration", ""),
            "upload_date": item.get("date", ""),
            "transcript": item.get("subtitles", None),
            "channel": item.get("channelName", ""),
        })

    return results


# ─────────────────────────────────────────────
# TikTok Scraper
# ─────────────────────────────────────────────

def extract_tiktok_username(url: str) -> str:
    """Extract @username from TikTok profile URL."""
    # Handles: https://www.tiktok.com/@username, https://tiktok.com/@username
    match = re.search(r"tiktok\.com/@([^/?&#]+)", url)
    if match:
        return match.group(1)
    raise ValueError(f"Could not extract TikTok username from URL: {url}")


def scrape_tiktok(client: ApifyClient, url: str, limit: int) -> list[dict]:
    username = extract_tiktok_username(url)
    print(f"[TikTok] Scraping profile: @{username}")
    print(f"[TikTok] Actor: clockworks/tiktok-profile-scraper | Limit: {limit} videos")

    run = client.actor("clockworks/tiktok-profile-scraper").call(run_input={
        "profiles": [username],
        "resultsPerPage": limit,
        "shouldDownloadVideos": False,
        "shouldDownloadCovers": False,
    })

    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    print(f"[TikTok] Raw items returned: {len(items)}")

    results = []
    for i, item in enumerate(items[:limit]):
        # Extract direct MP4 URL
        media_urls = item.get("mediaUrls", [])
        video_meta = item.get("videoMeta", {})
        download_url = media_urls[0] if media_urls else video_meta.get("downloadAddr", "")

        hashtags = [h.get("name", "") for h in item.get("hashtags", [])]
        create_time = item.get("createTimeISO", "")

        results.append({
            "index": i + 1,
            "platform": "tiktok",
            "title": item.get("text", "")[:100],  # caption as title
            "description": item.get("text", ""),
            "hashtags": hashtags,
            "url": item.get("webVideoUrl", ""),
            "download_url": download_url,
            "thumbnail_url": video_meta.get("coverUrl", ""),
            "views": item.get("playCount", 0),
            "likes": item.get("diggCount", 0),
            "duration": video_meta.get("duration", 0),
            "upload_date": create_time[:10] if create_time else "",
            "transcript": None,
            "channel": item.get("authorMeta", {}).get("name", ""),
        })

    return results


# ─────────────────────────────────────────────
# Facebook Scraper
# ─────────────────────────────────────────────

def is_video_post(post: dict) -> bool:
    """Return True if this Facebook post is a video or reel."""
    url = post.get("url", "")
    # Reels have /reel/ in the URL
    if "/reel/" in url:
        return True
    # Posts with viewsCount are almost always videos
    if post.get("viewsCount", 0) > 0:
        return True
    # Check media type
    for media in post.get("media", []):
        typename = media.get("__typename", "")
        if typename in ("Video", "UnifiedVideo"):
            return True
    return False


def scrape_facebook(client: ApifyClient, url: str, limit: int) -> list[dict]:
    print(f"[Facebook] Scraping page: {url}")
    print(f"[Facebook] Actor: apify/facebook-posts-scraper | Fetching EXACTLY {limit} posts")

    # Fetch exactly `limit` posts as requested by user
    run = client.actor("apify/facebook-posts-scraper").call(run_input={
        "startUrls": [{"url": url}],
        "resultsLimit": limit,
    })

    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    print(f"[Facebook] Raw posts returned: {len(items)}")

    # Filter to video/reel posts only
    video_posts = [p for p in items if is_video_post(p)]
    print(f"[Facebook] Video/reel posts out of the {limit} fetched: {len(video_posts)}")

    if len(video_posts) < limit:
        print(f"[Facebook] INFO: {len(video_posts)} video posts found in the last {limit} posts. Proceeding with available.")

    results = []
    for i, post in enumerate(video_posts):
        results.append({
            "index": i + 1,
            "platform": "facebook",
            "title": post.get("text", "")[:100],
            "description": post.get("text", ""),
            "hashtags": [],  # Facebook posts scraper doesn't extract hashtags separately
            "url": post.get("url", ""),
            "download_url": post.get("url", ""),  # yt-dlp will handle FB video download
            "thumbnail_url": (post.get("media") or [{}])[0].get("thumbnail", "") if post.get("media") else "",
            "views": post.get("viewsCount", 0),
            "likes": post.get("likes", 0),
            "duration": 0,  # Not available in posts scraper
            "upload_date": (post.get("time", "") or "")[:10],
            "transcript": None,
            "channel": post.get("user", {}).get("name", ""),
        })

    return results


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Scrape 20 videos from a social media page via Apify")
    parser.add_argument("url", help="YouTube channel, TikTok profile, or Facebook page URL")
    parser.add_argument("--session", default=None, help="Session folder name (default: auto-generated timestamp)")
    parser.add_argument("--limit", type=int, default=20, help="Number of videos to scrape (default: 20)")
    args = parser.parse_args()

    url = args.url.strip()
    limit = args.limit

    # Create session folder
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_name = args.session or timestamp
    session_dir = ROOT_DIR / ".tmp" / session_name
    session_dir.mkdir(parents=True, exist_ok=True)
    print(f"Session folder: {session_dir}")

    # Detect platform
    platform = detect_platform(url)
    print(f"Detected platform: {platform.upper()}")

    # Run appropriate scraper
    client = ApifyClient(APIFY_TOKEN)

    start = time.time()
    if platform == "youtube":
        videos = scrape_youtube(client, url, limit)
    elif platform == "tiktok":
        videos = scrape_tiktok(client, url, limit)
    elif platform == "facebook":
        videos = scrape_facebook(client, url, limit)
    else:
        print(f"ERROR: Unsupported platform: {platform}")
        sys.exit(1)

    elapsed = round(time.time() - start, 1)
    print(f"\nScraping complete in {elapsed}s — {len(videos)} videos found")

    # Save output
    output = {
        "session": session_name,
        "input_url": url,
        "platform": platform,
        "scraped_at": datetime.now().isoformat(),
        "video_count": len(videos),
        "videos": videos,
    }

    output_path = session_dir / "videos_metadata.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Metadata saved → {output_path}")
    print(f"\nNext step: run download_videos.py --session {session_name}")

    return str(output_path)


if __name__ == "__main__":
    main()
