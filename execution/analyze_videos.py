"""
analyze_videos.py
=================
Uploads downloaded videos from a session to Google AI Studio
and extracts a detailed "Content DNA" visual analysis for each, 
then summarizes the overall style formula.

Requirements:
    pip install google-genai python-dotenv pydantic
"""

import sys
import os
import json
import time
import argparse
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

SCRIPT_DIR = Path(__file__).parent
ROOT_DIR = SCRIPT_DIR.parent
load_dotenv(ROOT_DIR / ".env")

API_KEY = os.environ.get("GOOGLE_AI_STUDIO_API_KEY")
if not API_KEY:
    print("ERROR: GOOGLE_AI_STUDIO_API_KEY not found in .env")
    sys.exit(1)

client = genai.Client(api_key=API_KEY)
# We use gemini-2.5-pro as it's the highest reasoning model available for video analysis via API
MODEL_NAME = "gemini-2.5-pro"

PROMPT_PER_VIDEO = """
Analyze this video thoroughly and extract its "Content DNA". Focus ONLY on the visual logic, structure, and pacing, as if you were writing a blueprint for an AI video generator to recreate this exact style of video. Do NOT just summarize the plot.

Document the following:
1. Camera Style & Movement: (e.g., locked/static, handheld, smooth tracking, pov, drone, zooming, panning. Does the camera move at all?)
2. Primary Focus: (What is the main subject? A person speaking, an object transforming, a landscape, etc.)
3. Subject Motion: (Does the subject move fast, slow, or is it mostly still?)
4. Visual Progression: (Does it show a before/after, a continuous linear journey, a single unchanging shot, or a loop?)
5. Lighting & Color: (Natural daylight, cinematic mood lighting, studio lights, vibrant vs desaturated)
6. Edit/Pacing: (Are there many fast cuts, slow motion, real-time continuous flow, or just one long unbroken shot?)

Keep it concise and highly descriptive. Use bullet points.
"""

PROMPT_SUMMARY = """
Below are the individual visual analyses of multiple recent videos from a single creator's social media page.

Your job is to find the CORE REPEATABLE FORMULA across all these videos. 
Synthesize exactly what makes this creator's visual style unique and consistent.

You must output:
1. THE FORMULA: A 2-3 sentence summary of the exact visual, camera, and structural formula used in these videos.
2. SYSTEM DEFAULTS: What should be the default camera motion, lighting, and pacing for this style?
3. RECOMMENDED VIDEO SYSTEM: Should this be generated using a static locked camera system ('veo31_frame') or a sequential/moving camera system ('grok_sequential_extend')? Why? Use veo31_frame if the camera is mostly locked/static with simple subject motion. Use grok_sequential_extend if there is heavy camera movement, continuous journey, or heavy scene changes.
4. VARIATIONS (VIDEO TYPES): Suggest 5-8 sub-variants or specific actions that could be offered to a user as drop-down options to generate content in this style. Include a name and a short description for each.

Be authoritative, precise, and format your response in clean Markdown.
"""

def get_or_upload_file(local_path: Path):
    """Uploads file to Gemini File API, waiting for processing to complete."""
    filename = local_path.name
    print(f"  Uploading {filename} to Gemini...")
    uploaded_file = client.files.upload(file=str(local_path))
    print(f"  [OK] Uploaded as {uploaded_file.name}. Waiting for processing...")
    
    while True:
        uploaded_file = client.files.get(name=uploaded_file.name)
        state_str = str(uploaded_file.state)
        # `state` might be `State.PROCESSING` Enum, so we convert to string or check its `.name`.
        # Under google-genai, uploaded_file.state.name gives "PROCESSING"
        if "PROCESSING" in state_str:
            print("  ...processing video on Google servers...")
            time.sleep(10)
        elif "FAILED" in state_str:
            raise ValueError(f"File processing failed for {filename} on Google servers.")
        else:
            break
            
    print(f"  [OK] Video ready for analysis.")
    return uploaded_file

def analyze_single_video(video_path: Path, caption: str) -> str:
    gemini_file = get_or_upload_file(video_path)
    print(f"  Analyzing with {MODEL_NAME}...")
    caption_context = f"\nOriginal Caption: {caption}\n" if caption else ""
    
    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=[gemini_file, caption_context + PROMPT_PER_VIDEO]
        )
        return response.text
    finally:
        try:
            print("  Cleaning up file from Google AI Studio...")
            client.files.delete(name=gemini_file.name)
        except Exception as e:
            print(f"  Warning: failed to delete file {gemini_file.name}: {e}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True, help="Session folder in .tmp/")
    parser.add_argument("--limit", type=int, default=5, help="Number of videos to analyze")
    parser.add_argument("--offset", type=int, default=0, help="Skip the first N videos")
    args = parser.parse_args()

    session_dir = ROOT_DIR / ".tmp" / args.session
    meta_path = session_dir / "videos_metadata.json"
    
    if not meta_path.exists():
        print(f"ERROR: No metadata found at {meta_path}")
        sys.exit(1)

    with open(meta_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    videos_to_analyze = [v for v in data.get("videos", []) if v.get("downloaded") and v.get("local_path")]
    
    if not videos_to_analyze:
        print("ERROR: No successfully downloaded videos found.")
        sys.exit(1)
        
    videos_to_analyze = videos_to_analyze[args.offset:args.offset + args.limit]
    print(f"=== Starting Analysis of {len(videos_to_analyze)} Videos ===")
    
    individual_analyses = []
    
    for i, video in enumerate(videos_to_analyze):
        local_path = Path(video["local_path"])
        caption = video.get("title", "")
        print(f"\n[{i+1}/{len(videos_to_analyze)}] {local_path.name}")
        
        try:
            analysis_text = analyze_single_video(local_path, caption)
            print("  [OK] Analysis complete.")
            individual_analyses.append(f"### Video {i+1} ({local_path.name})\nCaption: {caption}\n{analysis_text}\n")
        except Exception as e:
            print(f"  [FAILED] Failed to analyze {local_path.name}: {e}")

    if not individual_analyses:
        print("\nERROR: All analyses failed. Exiting.")
        sys.exit(1)

    dna_path = session_dir / "content_dna.md"
    write_mode = "a" if args.offset > 0 and dna_path.exists() else "w"
    with open(dna_path, write_mode, encoding="utf-8") as f:
        if write_mode == "w":
            f.write("# Individual Video Analyses\n\n")
        f.write("\n\n---\n\n".join(individual_analyses))
        f.write("\n\n---\n\n")

    print("\n=== Generating Master Summary Formula ===")
    all_dna = open(dna_path, encoding="utf-8").read()
    summary_prompt = PROMPT_SUMMARY + "\n\n" + all_dna
    
    try:
        summary_response = client.models.generate_content(
            model=MODEL_NAME,
            contents=summary_prompt
        )
        
        master_dna_path = session_dir / "master_dna_summary.md"
        with open(master_dna_path, "w", encoding="utf-8") as f:
            f.write(summary_response.text)
            
        print(f"[OK] Master summary saved to: {master_dna_path}")
        print("\nAnalysis Complete! Please review the master summary.")
        
    except Exception as e:
        print(f"\nFailed to generate master summary: {e}")

if __name__ == "__main__":
    main()
