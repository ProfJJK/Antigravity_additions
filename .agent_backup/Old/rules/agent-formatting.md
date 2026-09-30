# Agent File Formatting Rules

When editing `.agent.md` files, ALWAYS use LF (Unix) line endings, never CRLF.

## Rules and Guidelines

1. **LF Line Endings Only**: Never write or edit `.agent.md` files with CRLF line endings.
2. **Writing Files from PowerShell**: Do NOT use PowerShell's `Set-Content` to write `.agent.md` files. Use `[System.IO.File]::WriteAllText()` instead to explicitly ensure LF line endings:
   ```powershell
   [System.IO.File]::WriteAllText("$Path", $Content.Replace("`r`n", "`n"), [System.Text.Encoding]::UTF8)
   ```

## Rationale
- CRLF line endings break the YAML frontmatter parser in `.agent.md` files, which silently removes tool access from agents.
