from email_summarizer import EmailSummarizer

class PersonalTriageSubsystem:
    def __init__(self):
        self.summarizer = EmailSummarizer()
        
    def process_email(self, subject: str, body: str):
        """
        Process personal emails and extract action items.
        """
        result = self.summarizer.summarize(subject, body)
        
        draft_body = (
            f"--- AI SUMMARY ---\n{result['summary']}\n\n"
            f"--- ACTION ITEMS ---\n" + "\n".join(result['action_items']) + "\n\n"
            f"[AI ASSISTED DRAFT - REVIEW REQUIRED BEFORE SENDING]"
        )
        
        return {
            "draft_body": draft_body,
            "route": result["route"]
        }
