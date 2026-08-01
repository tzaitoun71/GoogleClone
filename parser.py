"""
  parser.py — turn a raw HTML page into a clean, structured document.

  This is the second stage of the pipeline. Input: one HTML file. Output: a dict
  the indexer and PageRank can consume. Everything here is about turning messy
  HTML into uniform, normalized data.
"""

import re
from pathlib import Path
from urllib.parse import urljoin, urldefrag

from bs4 import BeautfulSoup