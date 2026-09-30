# System Requirements Specification: Parallel YouTube Daemon
**Component Name:** `parallel_youtube_processor.py`
**Version:** 4.1.2-Parallel
**Status:** Active (Bypasses SQLite Pipeline)

## Overview
The Parallel YouTube Daemon is a massive-concurrency background worker designed to process raw OBS `.mp4` and `.mkv` files natively. It completely bypasses the legacy V3/V4 SQLite `kanban_tasks` queue to eliminate locking contention and pipeline overhead.

## Architecture
- **Daemon Loop:** Managed by a standalone `while True` loop with a `ThreadPoolExecutor` constraint (`MAX_CONCURRENT_VIDEOS=3`) to prevent API throttling and GPU out-of-memory errors on local transcription.
- **Hardware Guards:** Checks file stability using 10-second `st_size` diffs to ensure OBS has finished writing the buffer.
- **State Management:** Uses a dedicated SQLite WAL database (`youtube_processed.db`) to track `status` and `youtube_id`, ensuring idempotency across reboots.

## Publishing & Metadata Handling
### 1. Title Mapping
The daemon deterministically extracts the recording timestamp from the OBS filename (e.g., `2026-09-03 11-11-08.mp4`). It cross-references the time boundary (11:00 AM - 12:15 PM) and date (9/3/2026) against the `26FA Dynamic Schedule - 26FA.csv` to deduce the course, chapter, and topic.
Resulting Title: `CHEM 311-01 - Ch 2 ppt 58 (9/3/2026)`

### 2. FERPA Screening
The daemon uploads the video to the `google-genai` File API and requests the `gemini-3.8-flash-high` model to generate a strict JSON payload containing:
- `lecture_end_time_seconds`: The timestamp where dead-air or off-topic conversation begins.
- `plickers_segments`: Start/End timestamps where Plickers rosters (student names) are visible.
The daemon then dynamically compiles an `ffmpeg` `-filter_complex` command to apply a `boxblur` over the Plickers frames and trim the video duration.

### 3. Closed Captioning (Title II Compliance)
The daemon extracts a localized `.mp3` audio track and runs the open-source `whisper` model (running locally on the host GPU) to generate a raw `.vtt` file. To ensure Title II compliance for scientific courses, the raw `.vtt` is passed through a secondary `gemini-3.8-flash-high` pass to fix scientific jargon, chemical names, and math equations before final publishing.

### 4. YouTube Upload Options
The `youtube_publisher.py` API wrapper automatically handles compliance and routing:
- **Playlists:** The daemon parses the course prefix (e.g., `CHEM 311-01`) and assigns the video to the appropriate YouTube Playlist ID defined in the `playlist_map` dictionary.
- **COPPA Compliance:** It explicitly sets `selfDeclaredMadeForKids=False` to comply with COPPA guidelines.
- **Privacy & Category:** The video is safely initialized as `unlisted` and categorized under `Education` (CategoryID: 27). Captions are uploaded natively and bound to the video ID as an official SubRip/VTT track.
