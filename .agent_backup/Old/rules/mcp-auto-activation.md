# MCP Tool Auto-Activation Rules

When context demands it, proactively use these MCP tools via `call_mcp_tool`. Do not wait for the user to ask — if the task would benefit, use them.

## BrightData (`brightdata`)
- **`search_engine`**: Use for real-time web searches when `search_web` returns stale or insufficient results.
- **`scrape_as_markdown`**: Use to extract clean content from URLs, especially when `read_url_content` hits JavaScript-rendered pages.
- **`search_engine_batch`**: Use for multi-query research sweeps (e.g., comparing results across multiple search terms simultaneously).
- **`scrape_batch`**: Use for bulk URL extraction across multiple pages.
- **`discover`**: Use to find related URLs and content from a seed URL.

## GitHub Copilot / Ollama (`github-copilot`)
- **`smart_generate`**: Use when generating code that benefits from multi-model consensus or when the primary model is uncertain about implementation details.
- **`ollama_generate`**: Use for **privacy-preserving local generation** — especially when analyzing sensitive research data, proprietary CoChem code, or unpublished manuscripts on the D: drive. Ollama runs entirely locally with zero data leaving the machine.
- **`copilot_generate`**: Use as a second opinion for complex code generation tasks.

## NotebookLM (`notebooklm`)
- **`notebook_create` + `notebook_add_*`**: Use to create research notebooks from sources (URLs, files, text) for deep analysis.
- **`notebook_query`**: Use to query assembled research notebooks for insights.
- **`audio_overview_create`**: Use to generate audio summaries of research topics for the user.
- **`research_start` + `research_poll`**: Use for automated deep research on a topic.
- **`slide_deck_create`**: Use when the user or a teaching agent needs presentation materials.
- **`flashcards_create`**: Use for educational/didactic content generation.
- **`report_create`**: Use to generate structured reports from notebook sources.
- **`mind_map_generate`**: Use for visual concept mapping of research topics.
