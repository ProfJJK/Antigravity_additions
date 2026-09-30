import sys
path = r'd:\__CoChem\GitHub-Repo\CoChem-SCRIBE\ui\voila_layout\scribe_gui_dashboard.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('print(f\"[SCRIBE-ERROR]', 'logger.error(f\"[SCRIBE-ERROR]')
content = content.replace('print(f\"[SCRIBE-FATAL]', 'logger.critical(f\"[SCRIBE-FATAL]')
content = content.replace('print(f\"[SCRIBE-SUCCESS]', 'logger.info(f\"[SCRIBE-SUCCESS]')
content = content.replace('print(f\"', 'logger.info(f\"')
content = content.replace('print(\"', 'logger.info(\"')

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Done")
