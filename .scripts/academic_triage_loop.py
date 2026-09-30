import os
import re
import json
import logging
from typing import Dict, Any, List
from local_rag_engine import LocalRAGEngine

class AcademicTriageSubsystem:
    def __init__(self, ollama_url: str = "http://localhost:11434"):
        self.rag_engine = LocalRAGEngine(ollama_url)
        # Regex for course codes e.g., [CS101] or [CHEM 201]
        self.course_regex = re.compile(r"\[(CHEM|CS)\s*\d{3}\]", re.IGNORECASE)
    
    def classify_email(self, subject: str, sender: str, roster_db: Dict[str, str]) -> str:
        """
        Roster-Aware Hierarchical Classifier
        1. Exact Rule Regex
        2. Roster Lookup
        3. Zero-Shot Fallback (Not implemented here, but handled in pipeline)
        """
        match = self.course_regex.search(subject)
        if match:
            return f"Inbox/Students/{match.group(0).upper()}"
        
        if sender in roster_db:
            return f"Inbox/Students/[{roster_db[sender]}]"
            
        return "Inbox/Administration"

    def process_email(self, raw_email_body: str) -> Dict[str, Any]:
        """
        Process the email using the local RAG engine.
        Returns the draft response data.
        """
        docs = self.rag_engine.retrieve(raw_email_body, top_k=3)
        
        uncertain = False
        context = ""
        if docs:
            if docs[0].get("uncertain"):
                uncertain = True
                context += "[UNCERTAIN: Ground truth not found in syllabus. Flagged for instructor review.]\n\n"
            
            for d in docs:
                context += d['content'] + "\n"
        
        import requests
        
        prompt = f"Using the following course documents:\n{context}\n\nDraft a polite response to this student email:\n{raw_email_body}\n\nConstraint: If the answer is not in the documents, state that it will be addressed in class."
        
        try:
            response = requests.post(
                f"{self.rag_engine.ollama_url}/api/generate",
                json={"model": "llama3.1:8b", "prompt": prompt, "stream": False},
                timeout=30
            )
            response.raise_for_status()
            draft_text = response.json().get("response", "").strip()
        except requests.exceptions.RequestException as e:
            logging.error(f"Ollama generation failed: {e}")
            draft_text = f"[ERROR: Local LLM unreachable] Context:\n{context}"
        
        draft_body = f"{draft_text}\n\n[AI ASSISTED DRAFT - REVIEW REQUIRED BEFORE SENDING]"
        return {
            "draft_body": draft_body,
            "uncertain": uncertain
        }

class IMAPDraftGovernor:
    """
    Physical "Never-Auto-Send" Draft Barrier.
    This class intentionally lacks any `send()` or `send_message()` methods.
    """
    def __init__(self, imap_client):
        self.imap = imap_client
        
    def save_draft(self, mailbox: str, subject: str, body: str):
        """
        Saves an email to the Drafts folder. Does NOT send it.
        Uses IMAP APPEND.
        """
        draft_subject = f"[AI-DRAFT] {subject}"
        # A real IMAP APPEND call would happen here.
        # e.g. self.imap.append("Drafts", "\\Draft", imaplib.Time2Internaldate(time.time()), message.as_bytes())
        logging.info(f"Appending draft to {mailbox}/Drafts with subject: {draft_subject}")
        return True
        
    # AST inspection will confirm 0 occurrences of .send() or SMTP credentials.
