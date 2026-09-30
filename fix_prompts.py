import os
import glob
import re

def main():
    dir_path = r"D:\__CoChem\__agentic\.prompts\.SRS\CoChem-SCRIBE\.finished_coding_prompts"
    files = glob.glob(os.path.join(dir_path, "*.md"))
    
    pattern = re.compile(r'Zero-Mock Anti-Spoofing Protocol(?:\s*v[0-9]+)?', re.IGNORECASE)
    replace_with = "Anti-Spoofing Protocol v2 (enforcing Zero-Mock, Asymmetric Verification, Hard Abort Criteria, and MAX_PIVOT_CYCLES)"
    
    count = 0
    for f in files:
        with open(f, 'r', encoding='utf-8') as file:
            content = file.read()
        
        if pattern.search(content):
            new_content = pattern.sub(replace_with, content)
            
            with open(f, 'w', encoding='utf-8') as file:
                file.write(new_content)
            count += 1
            print(f"Updated: {os.path.basename(f)}")
            
    print(f"\nTotal files updated: {count}")

if __name__ == "__main__":
    main()
