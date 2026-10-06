import json
import sys

# Paths
main_path = r"C:\Users\ansac\.gemini\config\mcp_config.json"
local_path = r"D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\config\antigravity.local.json"

with open(main_path, "r", encoding="utf-8") as f:
    main_cfg = json.load(f)

with open(local_path, "r", encoding="utf-8") as f:
    local_cfg = json.load(f)

# Merge
servers = main_cfg.get("mcpServers", {})

# Add new servers
new_servers = local_cfg.get("mcpServers", {})
servers.update(new_servers)

# Remove old broken implementations if they exist
servers.pop("claude", None)
servers.pop("codex-headless", None)

main_cfg["mcpServers"] = servers

with open(main_path, "w", encoding="utf-8") as f:
    json.dump(main_cfg, f, indent=2)

print("Merged successfully!")
