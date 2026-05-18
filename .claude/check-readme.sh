#!/bin/bash
# Remind to update READMEs if source files were committed after the last README commit.
readme_time=$(git -C /home/user/BPM-Tagger log -1 --format="%ct" -- README.md DOCKERHUB_README.md 2>/dev/null)
code_time=$(git -C /home/user/BPM-Tagger log -1 --format="%ct" -- bpm_tagger.py web_ui.py templates/ docker-compose.yml Dockerfile requirements.txt 2>/dev/null)

if [ -n "$code_time" ] && [ -n "$readme_time" ] && [ "$code_time" -gt "$readme_time" ]; then
  echo '{"systemMessage": "README reminder: source files were committed after the last README update. Update README.md and DOCKERHUB_README.md if the changes are user-facing."}'
fi
