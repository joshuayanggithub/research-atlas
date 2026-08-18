#!/usr/bin/env bash
cd /home/joshua/Documents/research-atlas || exit 1
export S2_KEY=$(grep "^S2_API_KEY=" .env | cut -d= -f2)
mkdir -p /mnt/wd/s2ag
for attempt in 1 2 3 4; do
  echo "[$(date '+%F %T')] === archive attempt $attempt ===" >> /mnt/wd/s2ag/archive.log
  if uv run python archive_s2_citations.py >> /mnt/wd/s2ag/archive.log 2>&1; then
    echo "[$(date '+%F %T')] archive done" >> /mnt/wd/s2ag/archive.log; break
  fi
  echo "[$(date '+%F %T')] attempt $attempt exited nonzero; resuming in 120s" >> /mnt/wd/s2ag/archive.log
  sleep 120
done
