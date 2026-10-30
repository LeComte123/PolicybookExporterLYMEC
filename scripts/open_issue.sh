#!/usr/bin/env bash
# Open a GitHub issue, or add a comment if an open issue with the same title exists.
# Usage: scripts/open_issue.sh FILE    FILE: first line "# <title>", then the body (Markdown).
# Needs the GitHub CLI with GH_TOKEN and GH_REPO set (the workflow does this). An optional
# RUN_URL is appended as a link to the run's log.
set -euo pipefail

file=$1
title=$(sed -n '1s/^# //p' "$file")
body=$(mktemp)
tail -n +3 "$file" > "$body"
if [ -n "${RUN_URL:-}" ]; then
  printf '\nDetails for the maintainer: %s\n' "$RUN_URL" >> "$body"
fi

number=$(gh issue list --state open --limit 200 --json number,title --jq '.[] | "\(.number)\t\(.title)"' \
  | awk -F '\t' -v title="$title" '$2 == title { print $1; exit }')
if [ -n "$number" ]; then
  gh issue comment "$number" --body-file "$body"
else
  gh issue create --title "$title" --body-file "$body"
fi
