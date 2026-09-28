import csv
import json
import os
import re
import time
import subprocess
from pathlib import Path

from google import genai
from google.genai import types


# ============================================================
# SETTINGS
# ============================================================

CSV_FILE = Path("products.csv")

OUTPUT_DIR = Path("output")
SCENES_DIR = OUTPUT_DIR / "scenes"
FINAL_DIR = OUTPUT_DIR / "final"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyA6GyRN1TyMRxgRBjiALy6lx6ypanzaYG4").strip()

TEXT_MODEL = os.environ.get(
    "TEXT_MODEL",
    "gemini-2.5-flash"
)

VEO_MODEL = os.environ.get(
    "VEO_MODEL",
    "veo-3.1-fast-generate-preview"
)

# Number of CSV rows processed in one GitHub run.
# Keep 1 initially because Veo generation costs money.
MAX_ROWS = int(os.environ.get("MAX_ROWS", "1"))

POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "10"))


# ============================================================
# CHECKS
# ============================================================

if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY is missing. "
        "Add it in GitHub Settings > Secrets and variables > Actions."
    )

if not CSV_FILE.exists():
    raise FileNotFoundError(
        f"{CSV_FILE} not found. Put products.csv in the repository root."
    )


client = genai.Client(api_key=GEMINI_API_KEY)


# ============================================================
# HELPERS
# ============================================================

def safe_filename(value: str) -> str:
    value = str(value or "").strip()

    value = re.sub(
        r'[<>:"/\\|?*\x00-\x1f]',
        "_",
        value
    )

    value = value.replace(" ", "_")

    if not value:
        value = "video"

    return value[:100]


def clean_json_text(text: str) -> str:
    text = text.strip()

    if text.startswith("```"):
        text = re.sub(
            r"^```(?:json)?\s*",
            "",
            text,
            flags=re.IGNORECASE
        )

        text = re.sub(
            r"\s*```$",
            "",
            text
        )

    return text.strip()


def load_csv():
    with CSV_FILE.open(
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        reader = csv.DictReader(f)

        fieldnames = reader.fieldnames

        if not fieldnames:
            raise RuntimeError("CSV has no header.")

        rows = list(reader)

    return fieldnames, rows


def save_csv(fieldnames, rows):
    with CSV_FILE.open(
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
            extrasaction="ignore"
        )

        writer.writeheader()
        writer.writerows(rows)


# ============================================================
# GEMINI -> SCENES
# ============================================================

def create_scene_prompts(row):
    title = (
        row.get("AI Title")
        or row.get("News Title")
        or ""
    ).strip()

    hook = (
        row.get("Hook")
        or ""
    ).strip()

    script = (
        row.get("Script")
        or ""
    ).strip()

    if not script:
        raise RuntimeError(
            "The CSV row has no Script."
        )

    prompt = f"""
You are a professional documentary video director.

Create cinematic video scenes from the Arabic documentary script below.

TITLE:
{title}

HOOK:
{hook}

SCRIPT:
{script}

IMPORTANT RULES:

1. Return ONLY valid JSON.
2. Return a JSON array.
3. Each object must contain:
   - "scene"
   - "prompt"
4. Each scene is designed for an 8-second Veo video.
5. Cover the story from beginning to end.
6. Do not invent facts that are not supported by the script.
7. Keep locations, time period and atmosphere consistent.
8. Use realistic documentary cinematography.
9. Use cinematic camera movement.
10. Do not put text on screen.
11. No subtitles.
12. No logos.
13. No watermarks.
14. No TV/news channel branding.
15. For crime stories, do NOT show graphic gore,
    explicit wounds, corpses, mutilation or disturbing imagery.
16. Prefer environments, streets, buildings, silhouettes,
    investigators, documents, objects and atmospheric shots.
17. Do not create identifiable real victims.
18. Keep the visual style realistic and serious.
19. Every prompt must describe exactly what should appear
    during those 8 seconds.
20. Use 6 to 12 scenes depending on script length.

Return format:

[
  {{
    "scene": 1,
    "prompt": "..."
  }},
  {{
    "scene": 2,
    "prompt": "..."
  }}
]
"""

    response = client.models.generate_content(
        model=TEXT_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.7
        )
    )

    raw = response.text or ""

    raw = clean_json_text(raw)

    try:
        scenes = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Gemini returned invalid JSON:\n{raw[:3000]}"
        ) from e

    if not isinstance(scenes, list):
        raise RuntimeError(
            "Gemini did not return a JSON array."
        )

    cleaned = []

    for index, item in enumerate(scenes, start=1):

        if not isinstance(item, dict):
            continue

        scene_prompt = str(
            item.get("prompt", "")
        ).strip()

        if not scene_prompt:
            continue

        cleaned.append({
            "scene": index,
            "prompt": scene_prompt
        })

    if not cleaned:
        raise RuntimeError(
            "No valid scenes were generated."
        )

    return cleaned


# ============================================================
# VEO
# ============================================================

def generate_video_clip(prompt, output_file):

    print()
    print("=" * 70)
    print("VEO GENERATION")
    print("=" * 70)
    print(prompt)
    print("=" * 70)

    operation = client.models.generate_videos(
        model=VEO_MODEL,
        prompt=prompt,
        config=types.GenerateVideosConfig(
            aspect_ratio="16:9",
            resolution="720p",
            number_of_videos=1
        )
    )

    while not operation.done:

        print(
            f"Waiting for Veo... "
            f"sleep={POLL_SECONDS}s"
        )

        time.sleep(POLL_SECONDS)

        operation = client.operations.get(operation)

    if getattr(operation, "error", None):
        raise RuntimeError(
            f"Veo generation failed: {operation.error}"
        )

    if not operation.response:
        raise RuntimeError(
            "Veo returned no response."
        )

    generated_videos = getattr(
        operation.response,
        "generated_videos",
        None
    )

    if not generated_videos:
        raise RuntimeError(
            "Veo returned no generated video."
        )

    video = generated_videos[0].video

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    print(
        f"Downloading video -> {output_file}"
    )

    client.files.download(
        file=video,
        destination=str(output_file)
    )

    if not output_file.exists():
        raise RuntimeError(
            "Video download failed."
        )

    if output_file.stat().st_size == 0:
        raise RuntimeError(
            "Downloaded video is empty."
        )

    print(
        f"Video saved: {output_file}"
    )


# ============================================================
# FFMPEG
# ============================================================

def join_videos(video_files, output_file):

    if not video_files:
        raise RuntimeError(
            "No video clips to join."
        )

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    concat_file = output_file.parent / "concat.txt"

    with concat_file.open(
        "w",
        encoding="utf-8"
    ) as f:

        for video in video_files:

            absolute_path = video.resolve()

            path_string = str(
                absolute_path
            ).replace(
                "'",
                "'\\''"
            )

            f.write(
                f"file '{path_string}'\n"
            )

    command = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file),
        "-c",
        "copy",
        str(output_file)
    ]

    print()
    print("Joining video clips...")
    print(" ".join(command))

    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:

        print(result.stdout)
        print(result.stderr)

        raise RuntimeError(
            "FFmpeg failed to join videos."
        )

    if not output_file.exists():
        raise RuntimeError(
            "Final MP4 was not created."
        )

    print(
        f"FINAL VIDEO: {output_file}"
    )


# ============================================================
# PROCESS ONE ROW
# ============================================================

def process_row(row):

    row_id = (
        row.get("ID")
        or row.get("id")
        or row.get("AI Title")
        or row.get("News Title")
        or "video"
    ).strip()

    safe_id = safe_filename(row_id)

    print()
    print("#" * 80)
    print(f"PROCESSING: {safe_id}")
    print("#" * 80)

    status = (
        row.get("Status")
        or ""
    ).strip()

    if status == "READY":
        print(
            "Already READY -> skipping."
        )
        return

    row["Error"] = ""

    try:

        # ----------------------------------------------------
        # 1. CREATE SCENES
        # ----------------------------------------------------

        row["Status"] = "GENERATING_SCENES"

        fieldnames, all_rows = load_csv()
        save_csv(fieldnames, all_rows)

        scenes = create_scene_prompts(row)

        row["Scenes"] = json.dumps(
            scenes,
            ensure_ascii=False
        )

        row["Status"] = "SCENES_DONE"

        # ----------------------------------------------------
        # 2. SAVE SCENES JSON
        # ----------------------------------------------------

        scene_folder = (
            SCENES_DIR / safe_id
        )

        scene_folder.mkdir(
            parents=True,
            exist_ok=True
        )

        scenes_json = (
            scene_folder / "scenes.json"
        )

        with scenes_json.open(
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                scenes,
                f,
                ensure_ascii=False,
                indent=2
            )

        # ----------------------------------------------------
        # 3. GENERATE EACH VEO CLIP
        # ----------------------------------------------------

        clip_files = []

        for index, scene in enumerate(
            scenes,
            start=1
        ):

            clip_file = (
                scene_folder
                / f"scene_{index:03d}.mp4"
            )

            clip_files.append(
                clip_file
            )

            if clip_file.exists():
                print(
                    f"Scene {index} already exists -> skip"
                )
                continue

            row["Status"] = (
                f"VIDEO_{index:03d}_GENERATING"
            )

            generate_video_clip(
                scene["prompt"],
                clip_file
            )

            row["Status"] = (
                f"VIDEO_{index:03d}_DONE"
            )

        # ----------------------------------------------------
        # 4. JOIN CLIPS
        # ----------------------------------------------------

        final_file = (
            FINAL_DIR
            / f"{safe_id}.mp4"
        )

        join_videos(
            clip_files,
            final_file
        )

        # ----------------------------------------------------
        # 5. FINISHED
        # ----------------------------------------------------

        row["Status"] = "READY"
        row["Error"] = ""

        print()
        print("=" * 80)
        print("DONE")
        print(f"FINAL FILE: {final_file}")
        print("=" * 80)

    except Exception as e:

        row["Status"] = "ERROR"
        row["Error"] = str(e)

        print()
        print("=" * 80)
        print("ERROR")
        print(str(e))
        print("=" * 80)

        raise


# ============================================================
# MAIN
# ============================================================

def main():

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    SCENES_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    FINAL_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    fieldnames, rows = load_csv()

    required_columns = [
        "Script",
        "Scenes",
        "Status",
        "Error"
    ]

    missing = [
        col
        for col in required_columns
        if col not in fieldnames
    ]

    if missing:
        raise RuntimeError(
            "Missing CSV columns: "
            + ", ".join(missing)
        )

    processed = 0

    for row in rows:

        if processed >= MAX_ROWS:
            break

        status = (
            row.get("Status")
            or ""
        ).strip()

        if status == "READY":
            continue

        try:

            process_row(row)

        except Exception as e:

            row["Status"] = "ERROR"
            row["Error"] = str(e)

        # Save CSV after every row.
        save_csv(
            fieldnames,
            rows
        )

        processed += 1

    print()
    print("=" * 80)
    print("ALL DONE")
    print(f"Rows processed: {processed}")
    print("=" * 80)


if __name__ == "__main__":
    main()
