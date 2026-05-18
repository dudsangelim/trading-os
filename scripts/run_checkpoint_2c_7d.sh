#!/bin/bash
set -e
cd /home/agent/trading-os
/usr/bin/python3 scripts/paper_2c_checkpoint.py --days 7 \
  >> /home/agent/trading-os/scripts/checkpoint_2c_7d.log 2>&1
