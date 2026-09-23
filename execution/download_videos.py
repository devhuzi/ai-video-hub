"""
download_videos.py
==================
Downloads video files from a previously scraped metadata JSON file.
Saves videos to .tmp/<session>/video_01.mp4 ... video_N.mp4

Usage:
    python execution/download_videos.py --session <session_name>
    python execution/download_videos.py --metadata <path_to_videos_metadata.json>

Strategy by platform:
    YouTube  → yt-dlp (best quality available, no expiry on links)
    TikTok   → direct HTTP download from mediaUrls (expires in hours — download fast!)
    Facebook → yt-dlp (handles FB video URLs cleanly)

Requirements:
    pip install yt-dlp requests python-dotenv
"""

import sys
import os
import json
import argparse
import subprocess
import time
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).parent
ROOT_DIR = SCRIPT_DIR.parent


def download_via_ytdlp(url: str, output_path: Path) -> bool:
    """Download a video using yt-dlp. Returns True on success."""
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--quiet",
        "--no-warnings",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", str(output_path),
        "--",  # stop option parsing so a URL can never be read as a yt-dlp flag
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  yt-dlp error: {result.stderr[:200]}")
        return False
    return output_path.exists()


def download_via_http(url: str, output_path: Path) -> bool:
    """Download a video file via direct HTTP request. Returns True on success."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.tiktok.com/",
        }
        with requests.get(url, headers=headers, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(output_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
        return output_path.exists() and output_path.stat().st_size > 1000
    except Exception as e:
        print(f"  HTTP download error: {e}")
        return False


def download_video(video: dict, session_dir: Path) -> dict:
    """Download a single video entry. Returns updated video dict with local_path."""
    idx = video["index"]
    platform = video["platform"]
    filename = f"video_{idx:02d}.mp4"
    output_path = session_dir / filename

    if output_path.exists() and output_path.stat().st_size > 10000:
        print(f"  [{idx:02d}] Already downloaded: {filename}")
        video["local_path"] = str(output_path)
        video["downloaded"] = True
        return video

    download_url = video.get("download_url", "")
    page_url = video.get("url", "")
    title = video.get("title", "")[:60]

    print(f"  [{idx:02d}] {platform.upper()} | {title}")

    success = False

    if platform == "tiktok" and download_url and download_url.startswith("http"):
        # TikTok: try direct HTTP first (faster), fall back to yt-dlp
        print(f"       Trying direct HTTP download...")
        success = download_via_http(download_url, output_path)
        if not success:
            print(f"       Falling back to yt-dlp...")
            success = download_via_ytdlp(page_url, output_path)

    elif platform in ("youtube", "facebook"):
        # YouTube and Facebook: always use yt-dlp
        target_url = page_url or download_url
        print(f"       Using yt-dlp...")
        success = download_via_ytdlp(target_url, output_path)

    else:
        # Generic fallback
        if download_url:
            success = download_via_http(download_url, output_path)
        if not success and page_url:
            success = download_via_ytdlp(page_url, output_path)

    if success:
        size_mb = round(output_path.stat().st_size / (1024 * 1024), 1)
        print(f"       [OK] Saved: {filename} ({size_mb} MB)")
        video["local_path"] = str(output_path)
        video["downloaded"] = True
    else:
        print(f"       [FAILED] to download video {idx}")
        video["local_path"] = None
        video["downloaded"] = False

    return video


def main():
    parser = argparse.ArgumentParser(description="Download videos from scraped metadata")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--session", help="Session folder name inside .tmp/")
    group.add_argument("--metadata", help="Full path to videos_metadata.json")
    args = parser.parse_args()

    # Resolve metadata path
    if args.session:
        metadata_path = ROOT_DIR / ".tmp" / args.session / "videos_metadata.json"
    else:
        metadata_path = Path(args.metadata)

    if not metadata_path.exists():
        print(f"ERROR: Metadata file not found: {metadata_path}")
        sys.exit(1)

    with open(metadata_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    session_dir = metadata_path.parent
    videos = data.get("videos", [])
    platform = data.get("platform", "unknown")

    print(f"Platform: {platform.upper()}")
    print(f"Session : {session_dir}")
    print(f"Videos  : {len(videos)}")
    print("-" * 50)

    # TikTok mediaUrls expire fast — warn user
    if platform == "tiktok":
        print("⚠ TikTok direct URLs expire within a few hours. Downloading now...")
        print()

    start = time.time()
    success_count = 0

    for video in videos:
        video = download_video(video, session_dir)
        if video.get("downloaded"):
            success_count += 1
        # Small delay between downloads to be polite
        time.sleep(0.5)

    elapsed = round(time.time() - start, 1)
    print("-" * 50)
    print(f"Downloaded {success_count}/{len(videos)} videos in {elapsed}s")

    # Update metadata with local paths
    data["videos"] = videos
    data["download_complete"] = True
    data["downloaded_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Metadata updated -> {metadata_path}")

    if success_count < len(videos):
        failed = len(videos) - success_count
        print(f"\n⚠ {failed} video(s) failed to download. Check URLs above.")
        sys.exit(1)
    else:
        print("\n[DONE] All videos downloaded successfully.")
        print(f"Videos are in: {session_dir}")


if __name__ == "__main__":
    main()
