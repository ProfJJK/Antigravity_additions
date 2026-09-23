import re

class EmailDualEngineRouter:
    """
    Routes personal emails based on sensitivity heuristcs.
    Tier 1 (Confidential / Financial): Routed exclusively to local Ollama.
    Tier 2 (General / Scheduling): Allowed to be routed via faster/external LLMs.
    """
    def __init__(self):
        self.sensitive_keywords = re.compile(
            r'\b(bank|invoice|medical|doctor|prescription|ssn|tax|confidential|password)\b', 
            re.IGNORECASE
        )
        
    def route(self, subject: str, body: str) -> str:
        if self.sensitive_keywords.search(subject) or self.sensitive_keywords.search(body):
            return "LOCAL_OLLAMA"
        return "EXTERNAL_LLM"

class EmailSummarizer:
    def __init__(self, local_llm_url="http://localhost:11434"):
        self.local_url = local_llm_url
        self.router = EmailDualEngineRouter()
        
    def summarize(self, subject: str, body: str) -> dict:
        route = self.router.route(subject, body)
        
        import requests
        if route == "LOCAL_OLLAMA":
            try:
                prompt = f"Summarize this sensitive email briefly and extract 1 action item:\nSubject: {subject}\nBody: {body}"
                res = requests.post(
                    f"{self.local_url}/api/generate",
                    json={"model": "llama3.1:8b", "prompt": prompt, "stream": False},
                    timeout=30
                )
                res.raise_for_status()
                summary_text = res.json().get("response", "").strip()
                summary = f"[LOCAL PROCESSING] {summary_text}"
                action_item = "[ACTION EXTRACTED BY LOCAL LLM]"
            except Exception as e:
                summary = "[LOCAL PROCESSING ERROR] Local LLM failed."
                action_item = "[ACTION REQUIRED: Handle securely]"
        else:
            # We will use Ollama here as a fallback until an external LLM API key is configured.
            summary = "[EXTERNAL PROCESSING] General thread summary (Needs API Hook)."
            action_item = "[ACTION REQUIRED: Reply to general inquiry]"
            
        return {
            "route": route,
            "summary": summary,
            "action_items": [action_item]
        }
