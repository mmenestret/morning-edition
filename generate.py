#!/usr/bin/env python3
"""
Morning Edition — Daily curated Hacker News magazine + daily brief panels.

Usage:
  python3 generate.py [--pro-json PATH] [--perso-json PATH]

If --pro-json or --perso-json are provided, renders daily brief panels
before the veille section.
"""

import argparse
import html
import json
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

REPO_DIR = Path("/opt/data/morning-edition")
MAGAZINES_DIR = REPO_DIR / "magazines"
STATE_DIR = REPO_DIR / ".state"
CLAUDE_CODE_WHATS_NEW_URL = "https://code.claude.com/docs/en/whats-new"
CLAUDE_CODE_WHATS_NEW_SEEN_FILE = STATE_DIR / "claude-code-whats-new-seen.json"
NUM_STORIES = 10
FETCH_TOP_N = 60
PARIS_TZ = timezone(timedelta(hours=2))

TASTE_KEYWORDS = {
    # AI / LLM (core focus)
    "ai": 8, "llm": 8, "gpt": 7, "claude": 8, "openai": 7, "anthropic": 7,
    "gemini": 6, "mistral": 6, "llama": 6, "transformer": 5, "diffusion": 4,
    "deepseek": 6, "qwen": 5, "gemma": 5, "phi": 4, "grok": 5,
    # Agentic / autonomous
    "agent": 9, "agentic": 10, "autonomous agent": 10, "multi-agent": 9,
    "tool use": 7, "function call": 6, "mcp": 7, "a2a": 6,
    "orchestrat": 7, "swarm": 6, "crew": 5,
    # RAG / context / memory
    "rag": 7, "retrieval": 6, "context window": 6, "embedding": 6,
    "vector": 5, "knowledge base": 5, "memory": 5, "chunking": 5,
    # Prompt / inference / training
    "prompt": 5, "inference": 6, "fine-tun": 7, "rlhf": 6, "dpo": 6,
    "quantiz": 6, "gguf": 7, "vllm": 6, "ollama": 6, "llama.cpp": 7,
    "serving": 5, "latency": 5, "throughput": 5, "batch": 4,
    "training": 5, "lora": 6, "qlora": 6, "peft": 5,
    # AI products / success stories
    "copilot": 6, "cursor": 7, "devin": 6, "swe-agent": 7,
    "ai engineer": 7, "ai product": 7, "ai startup": 6,
    "ai-generated": 5, "ai-assisted": 5, "ai-powered": 5,
    "vibe coding": 7, "claude code": 8, "codex": 6,
    # Data / analytics (Martin's bread and butter)
    "data": 4, "sql": 4, "bigquery": 7, "dbt": 7, "analytics": 4,
    "etl": 5, "pipeline": 5, "warehouse": 5, "dashboard": 4,
    "data engineer": 6, "data platform": 6, "lakehouse": 5,
    # Vision / multimodal
    "vision": 5, "multimodal": 6, "image generation": 6,
    "video generation": 5, "text-to": 4, "sora": 4, "midjourney": 4,
    "stable diffusion": 5, "flux": 4,
    # Speech / audio
    "whisper": 5, "tts": 5, "speech": 4, "voice": 4,
    # Open source AI
    "open source": 4, "open-source ai": 7, "hugging face": 6,
    "model weights": 5, "open weights": 6,
    # AI infra / self-host
    "self-host": 5, "homelab": 3, "gpu": 4, "nvidia": 4,
    "tpu": 4, "inference server": 6,
    # AI safety / eval / benchmarks
    "benchmark": 5, "eval": 5, "safety": 4, "alignment": 5,
    "hallucination": 5, "guardrail": 5,
}

NOISE_KEYWORDS = {
    "crypto": -5, "blockchain": -5, "nft": -5, "web3": -5, "defi": -4,
    "game": -4, "gaming": -4, "iphone": -3, "android app": -3,
    "startup funding": -3, "valuation": -3, "ipo": -3,
    # Generalist software engineering — Martin doesn't care
    "react": -3, "vue": -3, "angular": -3, "svelte": -2,
    "css framework": -3, "tailwind": -2, "bootstrap": -3,
    "node.js": -2, "deno": -2, "bun runtime": -2,
    "rest api": -2, "graphql": -2, "microservice": -3,
    "terraform": -2, "ansible": -2, "puppet": -3,
    "leetcode": -4, "coding interview": -4, "hiring": -3,
    "frontend": -3, "backend framework": -2, "full-stack": -3,
    "saas boilerplate": -3, "starter template": -3,
    "chrome extension": -2, "vs code extension": -2,
    "lint": -2, "code review": -2, "refactor": -2,
}


def fetch_json(url, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "MorningEdition/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except Exception as e:
            if attempt == retries - 1:
                print(f"  WARN: {url}: {e}", file=sys.stderr)
                return None


def fetch_html(url, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; MorningEdition/1.0)",
                "Accept": "text/html",
            })
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        except Exception as e:
            if attempt == retries - 1:
                print(f"  WARN: {url}: {e}", file=sys.stderr)
                return ""


def strip_html(value):
    if not value:
        return ""
    value = re.sub(r"<code[^>]*>(.*?)</code>", r"\1", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = value.replace("→", "")
    return re.sub(r"\s+", " ", value).strip()


def load_seen_urls(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {str(item) for item in data if item}
    except FileNotFoundError:
        return set()
    except Exception as e:
        print(f"  WARN: could not load seen URLs from {path}: {e}", file=sys.stderr)
    return set()


def save_seen_urls(path, urls):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(urls), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch_claude_code_updates(limit=5):
    page = fetch_html(CLAUDE_CODE_WHATS_NEW_URL)
    if not page:
        return []

    blocks = re.findall(
        r'(<div class="update flex flex-col relative items-start w-full lg:flex-row gap-2 lg:gap-6 py-8 update-container".*?)(?=<div class="update flex flex-col relative items-start w-full lg:flex-row gap-2 lg:gap-6 py-8 update-container"|<div class="feedback-toolbar)',
        page,
        flags=re.S,
    )

    updates = []
    for block in blocks:
        href_match = re.search(r'href="(/docs/en/whats-new/[^"]+)"', block)
        if not href_match:
            continue
        summary_spans = re.findall(r'<span data-as="p">(.*?)</span>', block, flags=re.S)
        if len(summary_spans) < 2:
            continue
        label_match = re.search(r'data-component-part="update-label">(.*?)</div>', block, flags=re.S)
        date_match = re.search(r'data-component-part="update-description">(.*?)</div>', block, flags=re.S)
        url = "https://code.claude.com" + href_match.group(1)
        updates.append({
            "label": strip_html(label_match.group(1) if label_match else ""),
            "date": strip_html(date_match.group(1) if date_match else ""),
            "title": strip_html(summary_spans[0]),
            "details": strip_html(summary_spans[1]),
            "url": url,
        })
        if len(updates) >= limit:
            break
    return updates


def fetch_top_stories():
    ids = fetch_json("https://hacker-news.firebaseio.com/v0/topstories.json")
    return (ids or [])[:FETCH_TOP_N]


def fetch_item(item_id):
    return fetch_json(f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json")


def fetch_all_stories(story_ids):
    stories = []
    with ThreadPoolExecutor(max_workers=15) as pool:
        futures = {pool.submit(fetch_item, sid): sid for sid in story_ids}
        for f in as_completed(futures):
            item = f.result()
            if item and item.get("type") == "story" and item.get("title"):
                stories.append(item)
    return stories


def score_story(story):
    title = story.get("title", "").lower()
    url = (story.get("url") or "").lower()
    text = title + " " + url
    score = 0.0
    hn_score = story.get("score", 0)
    score += min(hn_score / 100, 3)
    for kw, w in TASTE_KEYWORDS.items():
        if kw in text:
            score += w
    for kw, w in NOISE_KEYWORDS.items():
        if kw in text:
            score += w
    if title.startswith("ask hn") and any(k in text for k in ["tool", "workflow", "automat", "best"]):
        score += 2
    if title.startswith("show hn"):
        score += 1
    return score


def curate_stories(stories, n=NUM_STORIES):
    scored = [(score_story(s), s) for s in stories]
    scored.sort(key=lambda x: x[0], reverse=True)
    selected = []
    seen_domains = set()
    for sc, story in scored:
        if len(selected) >= n:
            break
        domain = ""
        if story.get("url"):
            m = re.search(r"https?://([^/]+)", story["url"])
            if m:
                domain = m.group(1).replace("www.", "")
        if domain in seen_domains and len(selected) > 5:
            continue
        seen_domains.add(domain)
        selected.append(story)
    return selected[:n]


THEMES = [
    {"name": "hero", "bg": "#0a0a0a", "fg": "#ffffff", "accent": "#ff3366", "num_style": "giant"},
    {"name": "midnight", "bg": "#0f172a", "fg": "#e2e8f0", "accent": "#38bdf8", "num_style": "outline"},
    {"name": "rose", "bg": "#fff1f2", "fg": "#1c1917", "accent": "#e11d48", "num_style": "stamp"},
    {"name": "terminal", "bg": "#0d1117", "fg": "#c9d1d9", "accent": "#58a6ff", "num_style": "prompt"},
    {"name": "academic", "bg": "#faf9f6", "fg": "#1a1a1a", "accent": "#7c3aed", "num_style": "dropcap"},
    {"name": "stat", "bg": "#18181b", "fg": "#fafafa", "accent": "#22d3ee", "num_style": "bigstat"},
    {"name": "warm", "bg": "#fef3c7", "fg": "#292524", "accent": "#d97706", "num_style": "serif"},
    {"name": "mint", "bg": "#f0fdf4", "fg": "#14532d", "accent": "#16a34a", "num_style": "circle"},
    {"name": "coral", "bg": "#1a1a2e", "fg": "#eee", "accent": "#ff6b6b", "num_style": "neon"},
    {"name": "paper", "bg": "#f5f0e8", "fg": "#2d2d2d", "accent": "#c0392b", "num_style": "roman"},
]


def render_numeral(index, theme):
    num = "{:02d}".format(index + 1)
    ns = theme["num_style"]
    accent = theme["accent"]
    if ns == "giant":
        return '<div class="numeral numeral-giant">' + num + '</div>'
    elif ns == "outline":
        return '<div class="numeral numeral-outline" style="color:transparent;-webkit-text-stroke:2px ' + accent + '">' + num + '</div>'
    elif ns == "stamp":
        return '<div class="numeral numeral-stamp"><span class="stamp-box">' + num + '</span></div>'
    elif ns == "prompt":
        return '<div class="numeral numeral-prompt"><span class="prompt-symbol">$</span> #' + num + '</div>'
    elif ns == "dropcap":
        letters = "ABCDEFGHIJ"
        return '<div class="numeral numeral-dropcap">' + letters[index] + '</div>'
    elif ns == "bigstat":
        return '<div class="numeral numeral-bigstat">' + num + '<span class="stat-slash">/</span>10</div>'
    elif ns == "serif":
        return '<div class="numeral numeral-serif">' + num + '</div>'
    elif ns == "circle":
        return '<div class="numeral numeral-circle"><span>' + num + '</span></div>'
    elif ns == "neon":
        return '<div class="numeral numeral-neon">' + num + '</div>'
    elif ns == "roman":
        romans = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]
        return '<div class="numeral numeral-roman">' + romans[index] + '</div>'
    return '<div class="numeral">' + num + '</div>'


def fetch_description(url):
    """Fetch article summary from URL. Tries meta description first, then extracts body text."""
    if not url:
        return ""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; MorningEdition/1.0)",
            "Accept": "text/html",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "text/html" not in content_type:
                return ""
            html = resp.read(100000).decode("utf-8", errors="ignore")

        # Clean HTML entities and tags
        def clean(s):
            # Strip script and style blocks entirely
            s = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', s, flags=re.I | re.S)
            s = re.sub(r'<[^>]+>', ' ', s)
            s = s.replace('&amp;', '&').replace('&quot;', '"').replace('&#39;', "'").replace('&lt;', '<').replace('&gt;', '>').replace('&nbsp;', ' ')
            return re.sub(r'\s+', ' ', s).strip()

        # Try og:description first, then meta description
        desc = ""
        m = re.search(r'<meta[^>]*property=["\']og:description["\'][^>]*content=["\'](.*?)["\']', html, re.I | re.S)
        if not m:
            m = re.search(r'<meta[^>]*content=["\'](.*?)["\'][^>]*name=["\']description["\']', html, re.I | re.S)
        if not m:
            m = re.search(r'<meta[^>]*name=["\']description["\'][^>]*content=["\'](.*?)["\']', html, re.I | re.S)
        if m:
            desc = clean(m.group(1))

        # If meta description is short, try extracting from article body
        if len(desc) < 200:
            # Try common article containers
            body_match = re.search(r'<(?:article|main|[div][^>]*class=["\'][^"\']*(?:post-content|article-body|entry-content|story-body|post__body)[^"\']*)["\'][^>]*>(.*?)</(?:article|main|div)>', html, re.I | re.S)
            if not body_match:
                # Fallback: first <p> tags after stripping nav/header
                body_match = re.search(r'<body[^>]*>(.*)', html, re.I | re.S)
            if body_match:
                body_text = clean(body_match.group(1))
                # Extract meaningful paragraphs (skip short nav/footer/CSS lines)
                paragraphs = [p.strip() for p in re.split(r'(?<=[.!?])\s+', body_text) if len(p.strip()) > 80 and not re.search(r'[{}]|rgb\(|px\b|margin|padding|font-size|display:', p[:200])]
                if paragraphs:
                    body_summary = ' '.join(paragraphs[:5])
                    if len(body_summary) > len(desc):
                        desc = body_summary

        if desc:
            if len(desc) > 800:
                desc = desc[:797] + "..."
            return desc
    except Exception:
        pass
    return ""


def fetch_descriptions(stories):
    """Fetch descriptions for all stories in parallel."""
    descriptions = {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(fetch_description, s.get("url")): s["id"] for s in stories if s.get("url")}
        for f in as_completed(futures):
            sid = futures[f]
            try:
                descriptions[sid] = f.result()
            except Exception:
                descriptions[sid] = ""
    return descriptions


def get_domain(url):
    if not url:
        return "news.ycombinator.com"
    m = re.search(r"https?://([^/]+)", url)
    return (m.group(1) or "").replace("www.", "") if m else "news.ycombinator.com"


def time_ago(story):
    ts = story.get("time", 0)
    now = datetime.now(timezone.utc).timestamp()
    diff = now - ts
    if diff < 3600:
        return str(int(diff / 60)) + "min"
    if diff < 86400:
        return str(int(diff / 3600)) + "h"
    return str(int(diff / 86400)) + "j"


def render_story(story, index, theme, description=""):
    title = story.get("title", "Untitled")
    url = story.get("url") or ("https://news.ycombinator.com/item?id=" + str(story["id"]))
    domain = get_domain(story.get("url", ""))
    hn_url = "https://news.ycombinator.com/item?id=" + str(story["id"])
    score = story.get("score", 0)
    comments = story.get("descendants", 0)
    ago = time_ago(story)
    by = story.get("by", "anonymous")
    numeral_html = render_numeral(index, theme)

    # Engagement stats
    engagement = "high" if score > 100 else "medium" if score > 50 else "rising"
    comment_ratio = round(comments / max(score, 1) * 100, 1) if score > 0 else 0

    parts = []
    parts.append('<section class="spread spread-' + theme["name"] + '" style="background:' + theme["bg"] + ";color:" + theme["fg"] + '">')
    parts.append('  <div class="spread-inner">')
    parts.append('    ' + numeral_html)
    parts.append('    <div class="story-content">')
    parts.append('      <div class="story-meta">')
    parts.append('        <span class="domain">' + domain + '</span>')
    parts.append('        <span class="dot">·</span>')
    parts.append('        <span class="score">' + str(score) + ' pts</span>')
    parts.append('        <span class="dot">·</span>')
    parts.append('        <span class="comments">' + str(comments) + ' comments</span>')
    parts.append('        <span class="dot">·</span>')
    parts.append('        <span class="ago">' + ago + '</span>')
    parts.append('      </div>')
    parts.append('      <h2 class="story-title">')
    parts.append('        <a href="' + url + '" target="_blank" rel="noopener" style="color:' + theme["fg"] + '">' + title + '</a>')
    parts.append('      </h2>')
    if description:
        parts.append('      <p class="story-description">' + description + '</p>')
    parts.append('      <div class="story-context">')
    parts.append('        <div class="context-row">')
    parts.append('          <span class="context-label">Par</span>')
    parts.append('          <span class="context-value">' + by + '</span>')
    parts.append('          <span class="context-sep">·</span>')
    parts.append('          <span class="context-label">Engagement</span>')
    parts.append('          <span class="context-value engagement-' + engagement + '">' + engagement + '</span>')
    if comments > 0:
        parts.append('          <span class="context-sep">·</span>')
        parts.append('          <span class="context-label">Ratio</span>')
        parts.append('          <span class="context-value">' + str(comment_ratio) + '% commentaires</span>')
    parts.append('        </div>')
    parts.append('      </div>')
    parts.append('      <div class="story-footer">')
    parts.append('        <a href="' + url + '" class="read-link" style="color:' + theme["accent"] + '" target="_blank">Lire l\'article</a>')
    parts.append('        <a href="' + hn_url + '" class="hn-link" style="color:' + theme["accent"] + '">Discussion HN (' + str(comments) + ')</a>')
    parts.append('      </div>')
    parts.append('    </div>')
    parts.append('  </div>')
    parts.append('</section>')
    return "\n".join(parts)


CSS = """
    *, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }
    :root {
      --ff-display: 'Fraunces', serif;
      --ff-body: 'Inter', sans-serif;
      --ff-mono: 'JetBrains Mono', monospace;
    }
    html { scroll-behavior: smooth; }
    body { font-family: var(--ff-body); -webkit-font-smoothing: antialiased; }

    .cover {
      min-height: 100vh;
      background: #0a0a0a;
      color: #fff;
      display: flex;
      flex-direction: column;
      justify-content: center;
      align-items: center;
      text-align: center;
      padding: 2rem;
      position: relative;
      overflow: hidden;
    }
    .cover::before {
      content: '';
      position: absolute;
      top: -50%; left: -50%;
      width: 200%; height: 200%;
      background: radial-gradient(ellipse at 30% 50%, rgba(255,51,102,0.08) 0%, transparent 50%),
                  radial-gradient(ellipse at 70% 50%, rgba(56,189,248,0.06) 0%, transparent 50%);
    }
    .cover-kicker {
      font-family: var(--ff-mono);
      font-size: clamp(0.75rem, 1.2vw, 1rem);
      letter-spacing: 0.3em;
      text-transform: uppercase;
      color: #ff3366;
      margin-bottom: 2rem;
      position: relative;
    }
    .cover-title {
      font-family: var(--ff-display);
      font-size: clamp(3rem, 10vw, 8rem);
      font-weight: 900;
      line-height: 0.9;
      letter-spacing: -0.03em;
      margin-bottom: 1.5rem;
      position: relative;
    }
    .cover-date {
      font-family: var(--ff-body);
      font-size: clamp(1rem, 2vw, 1.5rem);
      font-weight: 300;
      color: #888;
      position: relative;
    }
    .cover-subtitle {
      font-family: var(--ff-body);
      font-size: clamp(0.9rem, 1.5vw, 1.1rem);
      color: #666;
      margin-top: 1rem;
      position: relative;
    }

    .spread {
      min-height: 100vh;
      display: flex;
      align-items: center;
      padding: clamp(2rem, 5vw, 6rem);
      position: relative;
    }
    .spread-inner {
      max-width: 1000px;
      width: 100%;
      margin: 0 auto;
    }

    .numeral {
      font-family: var(--ff-display);
      font-weight: 900;
      line-height: 1;
      margin-bottom: 2rem;
    }
    .numeral-giant {
      font-size: clamp(6rem, 15vw, 14rem);
      opacity: 0.15;
      position: absolute;
      top: clamp(1rem, 3vw, 3rem);
      right: clamp(1rem, 3vw, 3rem);
    }
    .numeral-outline {
      font-size: clamp(5rem, 12vw, 10rem);
      -webkit-text-stroke: 2px;
      color: transparent;
    }
    .numeral-stamp { font-size: clamp(3rem, 8vw, 6rem); }
    .stamp-box {
      border: 4px solid currentColor;
      padding: 0.2em 0.4em;
      letter-spacing: 0.1em;
    }
    .numeral-prompt {
      font-family: var(--ff-mono);
      font-size: clamp(2rem, 5vw, 4rem);
      font-weight: 700;
    }
    .numeral-dropcap {
      font-family: var(--ff-display);
      font-size: clamp(6rem, 15vw, 12rem);
      font-weight: 900;
      font-style: italic;
      float: left;
      line-height: 0.75;
      margin-right: 1.5rem;
      margin-top: 0.3rem;
    }
    .numeral-bigstat { font-size: clamp(4rem, 10vw, 8rem); font-weight: 900; }
    .stat-slash { font-weight: 300; opacity: 0.3; margin: 0 0.1em; }
    .numeral-serif {
      font-family: var(--ff-display);
      font-size: clamp(4rem, 10vw, 8rem);
      font-style: italic;
      font-weight: 300;
    }
    .numeral-circle {
      width: clamp(4rem, 8vw, 7rem);
      height: clamp(4rem, 8vw, 7rem);
      border: 3px solid currentColor;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: clamp(1.5rem, 3vw, 2.5rem);
      font-weight: 700;
    }
    .numeral-neon {
      font-family: var(--ff-mono);
      font-size: clamp(4rem, 10vw, 8rem);
      text-shadow: 0 0 20px currentColor, 0 0 40px currentColor;
    }
    .numeral-roman {
      font-family: var(--ff-display);
      font-size: clamp(4rem, 10vw, 8rem);
      font-weight: 200;
      letter-spacing: 0.15em;
    }

    .story-meta {
      font-family: var(--ff-mono);
      font-size: clamp(0.8rem, 1.2vw, 1rem);
      opacity: 0.6;
      margin-bottom: 1.5rem;
      display: flex;
      flex-wrap: wrap;
      gap: 0.5em;
      align-items: center;
    }
    .dot { opacity: 0.3; }
    .story-title {
      font-family: var(--ff-display);
      font-size: clamp(1.8rem, 4vw, 3.5rem);
      font-weight: 800;
      line-height: 1.1;
      margin-bottom: 2rem;
    }
    .story-title a {
      text-decoration: none;
      transition: opacity 0.2s;
    }
    .story-title a:hover { opacity: 0.7; }
    .story-description {
      font-family: var(--ff-body);
      font-size: clamp(1rem, 1.6vw, 1.2rem);
      line-height: 1.7;
      opacity: 0.75;
      max-width: 750px;
      margin-bottom: 2rem;
      text-align: justify;
      hyphens: auto;
    }
    .story-footer {
      display: flex;
      gap: 2rem;
      align-items: center;
      font-size: clamp(0.85rem, 1.3vw, 1.1rem);
    }
    .hn-link, .read-link {
      font-family: var(--ff-mono);
      text-decoration: none;
      font-weight: 700;
      transition: opacity 0.2s;
    }
    .hn-link:hover, .read-link:hover { opacity: 0.7; }
    .by { opacity: 0.4; }

    .story-context {
      margin-top: 1.5rem;
      margin-bottom: 1.5rem;
      padding: 1rem 1.25rem;
      border-left: 3px solid currentColor;
      opacity: 0.5;
      font-family: var(--ff-mono);
      font-size: clamp(0.75rem, 1vw, 0.9rem);
    }
    .context-row {
      display: flex;
      flex-wrap: wrap;
      gap: 0.4em;
      align-items: center;
    }
    .context-label { text-transform: uppercase; letter-spacing: 0.1em; font-size: 0.85em; }
    .context-value { font-weight: 700; }
    .context-sep { opacity: 0.3; }
    .engagement-high { color: #22c55e; }
    .engagement-medium { color: #eab308; }
    .engagement-rising { color: #3b82f6; }

    .colophon {
      min-height: 50vh;
      background: #0a0a0a;
      color: #666;
      display: flex;
      flex-direction: column;
      justify-content: center;
      align-items: center;
      text-align: center;
      padding: 4rem 2rem;
      font-family: var(--ff-mono);
      font-size: clamp(0.75rem, 1vw, 0.9rem);
    }
    .colophon a { color: #ff3366; text-decoration: none; }

    @media (max-width: 600px) {
      .numeral-dropcap { float: none; margin-right: 0; margin-bottom: 1rem; }
      .story-footer { flex-direction: column; gap: 0.5rem; align-items: flex-start; }
    }
"""


PANELS_CSS = """
    /* ── Section Headers ── */
    .section-header {
      min-height: 40vh;
      display: flex;
      align-items: center;
      justify-content: center;
      text-align: center;
      padding: 4rem 2rem;
    }
    .section-header-inner { max-width: 800px; }
    .section-kicker {
      font-family: var(--ff-mono);
      font-size: clamp(0.7rem, 1vw, 0.9rem);
      letter-spacing: 0.3em;
      text-transform: uppercase;
      opacity: 0.5;
      margin-bottom: 1rem;
    }
    .section-title {
      font-family: var(--ff-display);
      font-size: clamp(2.5rem, 7vw, 5rem);
      font-weight: 900;
      line-height: 0.95;
    }

    /* ── Daily Panels ── */
    .daily-panels {
      padding: clamp(2rem, 5vw, 6rem);
      max-width: 1100px;
      margin: 0 auto;
    }
    .panel {
      margin-bottom: 4rem;
    }
    .panel-header {
      display: flex;
      align-items: center;
      gap: 1rem;
      margin-bottom: 2rem;
      padding-bottom: 1rem;
      border-bottom: 2px solid rgba(128,128,128,0.2);
    }
    .panel-icon {
      font-size: 1.5rem;
    }
    .panel-label {
      font-family: var(--ff-mono);
      font-size: clamp(0.7rem, 1vw, 0.85rem);
      letter-spacing: 0.2em;
      text-transform: uppercase;
      opacity: 0.5;
    }
    .panel-tag {
      font-family: var(--ff-mono);
      font-size: 0.7rem;
      padding: 0.2em 0.6em;
      border-radius: 3px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
    }
    .tag-pro { background: #1e3a5f; color: #7dd3fc; }
    .tag-perso { background: #3b1f3b; color: #f0abfc; }

    .event-row {
      display: grid;
      grid-template-columns: 100px 1fr;
      gap: 1rem;
      padding: 1rem 0;
      border-bottom: 1px solid rgba(128,128,128,0.1);
      align-items: start;
    }
    .event-time {
      font-family: var(--ff-mono);
      font-size: clamp(0.85rem, 1.2vw, 1rem);
      font-weight: 700;
      opacity: 0.8;
    }
    .event-title {
      font-family: var(--ff-body);
      font-size: clamp(1rem, 1.5vw, 1.2rem);
      font-weight: 600;
      margin-bottom: 0.3rem;
    }
    .event-meta {
      font-family: var(--ff-mono);
      font-size: clamp(0.7rem, 0.9vw, 0.8rem);
      opacity: 0.5;
    }
    .event-note {
      font-family: var(--ff-body);
      font-size: clamp(0.8rem, 1vw, 0.95rem);
      opacity: 0.6;
      margin-top: 0.3rem;
      font-style: italic;
    }

    .email-row {
      display: grid;
      grid-template-columns: auto 1fr auto;
      gap: 1rem;
      padding: 0.8rem 0;
      border-bottom: 1px solid rgba(128,128,128,0.1);
      align-items: center;
    }
    .email-from {
      font-family: var(--ff-mono);
      font-size: clamp(0.8rem, 1vw, 0.9rem);
      font-weight: 600;
      white-space: nowrap;
    }
    .email-subject {
      font-family: var(--ff-body);
      font-size: clamp(0.9rem, 1.2vw, 1rem);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .email-waiting {
      font-family: var(--ff-mono);
      font-size: 0.75rem;
      opacity: 0.5;
      white-space: nowrap;
    }

    .todo-row {
      display: grid;
      grid-template-columns: auto 1fr auto;
      gap: 1rem;
      padding: 0.8rem 0;
      border-bottom: 1px solid rgba(128,128,128,0.1);
      align-items: center;
    }
    .todo-priority {
      font-family: var(--ff-mono);
      font-size: 0.75rem;
      font-weight: 700;
      padding: 0.15em 0.5em;
      border-radius: 3px;
    }
    .prio-p1 { background: #dc2626; color: #fff; }
    .prio-p2 { background: #f59e0b; color: #000; }
    .prio-p3 { background: #6b7280; color: #fff; }
    .todo-content {
      font-family: var(--ff-body);
      font-size: clamp(0.9rem, 1.2vw, 1rem);
    }
    .todo-due {
      font-family: var(--ff-mono);
      font-size: 0.75rem;
      opacity: 0.5;
    }

    .empty-panel {
      font-family: var(--ff-body);
      font-size: 1rem;
      opacity: 0.4;
      font-style: italic;
      padding: 1rem 0;
    }

    /* ── Claude Code updates ── */
    .external-updates {
      background: #111827;
      color: #f9fafb;
      padding: clamp(2rem, 5vw, 5rem);
    }
    .external-updates-inner {
      max-width: 1100px;
      margin: 0 auto;
    }
    .update-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 1.25rem;
      margin-top: 2rem;
    }
    .update-card {
      background: rgba(255,255,255,0.04);
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 20px;
      padding: 1.5rem;
      display: flex;
      flex-direction: column;
      gap: 0.9rem;
      min-height: 100%;
    }
    .update-card-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 0.6rem;
      align-items: center;
      font-family: var(--ff-mono);
      font-size: 0.75rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: #93c5fd;
    }
    .update-card-date {
      color: rgba(255,255,255,0.55);
      letter-spacing: normal;
      text-transform: none;
      font-size: 0.8rem;
    }
    .update-card-title {
      font-family: var(--ff-display);
      font-size: clamp(1.3rem, 2vw, 1.9rem);
      line-height: 1.15;
      color: #fff;
    }
    .update-card-details {
      font-family: var(--ff-body);
      font-size: 0.98rem;
      line-height: 1.55;
      color: rgba(255,255,255,0.76);
      flex: 1;
    }
    .update-card-link {
      font-family: var(--ff-mono);
      font-size: 0.8rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: #fda4af;
      text-decoration: none;
    }

    /* ── Collisions ── */
    .collisions {
      background: #450a0a;
      color: #fca5a5;
      padding: clamp(2rem, 4vw, 4rem);
    }
    .collisions-inner {
      max-width: 1100px;
      margin: 0 auto;
    }
    .collisions-header {
      font-family: var(--ff-mono);
      font-size: clamp(0.8rem, 1vw, 0.9rem);
      letter-spacing: 0.2em;
      text-transform: uppercase;
      color: #fecaca;
      margin-bottom: 1.5rem;
    }
    .collision-item {
      padding: 1rem 0;
      border-bottom: 1px solid rgba(252,165,165,0.15);
      display: grid;
      grid-template-columns: 120px 1fr;
      gap: 1rem;
    }
    .collision-time {
      font-family: var(--ff-mono);
      font-weight: 700;
      font-size: clamp(0.9rem, 1.2vw, 1.1rem);
    }
    .collision-detail {
      font-family: var(--ff-body);
      font-size: clamp(0.9rem, 1.2vw, 1rem);
    }

    /* ── Tabs ── */
    .tab-bar {
      position: sticky;
      top: 0;
      z-index: 100;
      background: #0a0a0a;
      border-bottom: 1px solid rgba(255,255,255,0.1);
      display: flex;
      justify-content: center;
      gap: 0;
      padding: 0;
    }
    .tab-btn {
      background: none;
      border: none;
      color: #666;
      font-family: var(--ff-mono);
      font-size: clamp(0.8rem, 1.1vw, 1rem);
      letter-spacing: 0.15em;
      text-transform: uppercase;
      padding: 1rem 2.5rem;
      cursor: pointer;
      transition: color 0.2s, border-color 0.2s;
      border-bottom: 2px solid transparent;
    }
    .tab-btn:hover { color: #aaa; }
    .tab-btn.active { color: #fff; border-bottom-color: #ff3366; }
    .tab-panel { display: none; }
    .tab-panel.active { display: block; }
"""


def parse_time(t):
    """Parse HH:MM to minutes since midnight. Returns None for all-day/invalid."""
    if not t or "all" in t.lower() or ":" not in t:
        return None
    parts = t.split(":")
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, IndexError):
        return None


def render_daily_panels(pro_data, perso_data, date_str):
    """Render the daily brief panels (pro + perso)."""
    html = '<section class="daily-panels">\n'

    # ── Pro Panel ──
    if pro_data:
        html += '<div class="panel">\n'
        html += '  <div class="panel-header">'
        html += '    <span class="panel-icon">💼</span>'
        html += '    <span class="panel-label">Journée Pro</span>'
        html += '    <span class="panel-tag tag-pro">Actual Group</span>'
        html += '  </div>\n'

        # Meetings
        meetings = pro_data.get("meetings", [])
        if meetings:
            for ev in meetings:
                html += '  <div class="event-row">\n'
                html += '    <div class="event-time">' + ev.get("start", "") + "–" + ev.get("end", "") + '</div>\n'
                html += '    <div>\n'
                html += '      <div class="event-title">' + ev.get("title", "") + '</div>\n'
                if ev.get("participants"):
                    html += '      <div class="event-meta">' + ", ".join(ev["participants"]) + '</div>\n'
                if ev.get("context"):
                    html += '      <div class="event-note">' + ev["context"] + '</div>\n'
                html += '    </div>\n'
                html += '  </div>\n'
        else:
            html += '  <div class="empty-panel">Aucune réunion aujourd\'hui</div>\n'

        # Emails waiting
        emails = pro_data.get("emails_waiting", [])
        if emails:
            html += '  <div class="panel-header" style="margin-top:2rem">'
            html += '    <span class="panel-icon">📬</span>'
            html += '    <span class="panel-label">Mails en attente</span>'
            html += '  </div>\n'
            for em in emails:
                html += '  <div class="email-row">\n'
                html += '    <div class="email-from">' + em.get("from", "") + '</div>\n'
                html += '    <div class="email-subject">' + em.get("subject", "") + '</div>\n'
                html += '    <div class="email-waiting">' + str(em.get("days_waiting", 0)) + 'j</div>\n'
                html += '  </div>\n'

        # Priorities
        priorities = pro_data.get("priorities", [])
        if priorities:
            html += '  <div class="panel-header" style="margin-top:2rem">'
            html += '    <span class="panel-icon">🔥</span>'
            html += '    <span class="panel-label">Priorités</span>'
            html += '  </div>\n'
            for todo in priorities:
                prio = todo.get("priority", "p3")
                html += '  <div class="todo-row">\n'
                html += '    <div class="todo-priority prio-' + prio + '">' + prio.upper() + '</div>\n'
                html += '    <div class="todo-content">' + todo.get("content", "") + '</div>\n'
                if todo.get("due"):
                    html += '    <div class="todo-due">' + todo["due"] + '</div>\n'
                html += '  </div>\n'

        html += '</div>\n'

    # ── Perso Panel ──
    if perso_data:
        html += '<div class="panel">\n'
        html += '  <div class="panel-header">'
        html += '    <span class="panel-icon">🏠</span>'
        html += '    <span class="panel-label">Journée Perso</span>'
        html += '    <span class="panel-tag tag-perso">Famille</span>'
        html += '  </div>\n'

        # Perso events
        events = perso_data.get("calendar", {}).get("events", [])
        if events:
            for ev in events:
                html += '  <div class="event-row">\n'
                html += '    <div class="event-time">' + ev.get("start", "") + "–" + ev.get("end", "") + '</div>\n'
                html += '    <div>\n'
                html += '      <div class="event-title">' + ev.get("title", "") + '</div>\n'
                if ev.get("participants"):
                    html += '      <div class="event-meta">' + ", ".join(ev["participants"]) + '</div>\n'
                if ev.get("note"):
                    html += '      <div class="event-note">' + ev["note"] + '</div>\n'
                html += '    </div>\n'
                html += '  </div>\n'
        else:
            html += '  <div class="empty-panel">Rien de prévu côté perso</div>\n'

        # Perso emails (support both emails_waiting and emails_candidates)
        p_emails = perso_data.get("emails_waiting", []) or perso_data.get("emails_candidates", [])
        if p_emails:
            html += '  <div class="panel-header" style="margin-top:2rem">'
            html += '    <span class="panel-icon">📬</span>'
            html += '    <span class="panel-label">Mails perso en attente</span>'
            html += '  </div>\n'
            for em in p_emails:
                html += '  <div class="email-row">\n'
                html += '    <div class="email-from">' + em.get("from", "") + '</div>\n'
                html += '    <div class="email-subject">' + em.get("subject", "") + '</div>\n'
                html += '    <div class="email-waiting">' + str(em.get("days_waiting", 0)) + 'j</div>\n'
                html += '  </div>\n'

        # Perso todos
        p_todos = perso_data.get("todos_urgent", [])
        if p_todos:
            html += '  <div class="panel-header" style="margin-top:2rem">'
            html += '    <span class="panel-icon">✅</span>'
            html += '    <span class="panel-label">Todos perso urgentes</span>'
            html += '  </div>\n'
            for todo in p_todos:
                prio = todo.get("priority", "p3")
                html += '  <div class="todo-row">\n'
                html += '    <div class="todo-priority prio-' + prio + '">' + prio.upper() + '</div>\n'
                html += '    <div class="todo-content">' + todo.get("content", "") + '</div>\n'
                if todo.get("due"):
                    html += '    <div class="todo-due">' + todo["due"] + '</div>\n'
                html += '  </div>\n'

        html += '</div>\n'

    html += '</section>\n'
    return html


def render_collisions(pro_data, perso_data):
    """Detect and render scheduling collisions between pro and perso."""
    if not pro_data or not perso_data:
        return ""

    pro_events = pro_data.get("meetings", [])
    perso_events = perso_data.get("calendar", {}).get("events", [])

    collisions = []
    for pe in pro_events:
        p_start = parse_time(pe.get("start", "00:00"))
        p_end = parse_time(pe.get("end", "00:00"))
        if p_start is None or p_end is None:
            continue
        for fe in perso_events:
            f_start = parse_time(fe.get("start", "00:00"))
            f_end = parse_time(fe.get("end", "00:00"))
            if f_start is None or f_end is None:
                continue
            if p_start < f_end and f_start < p_end:
                overlap_start = max(p_start, f_start)
                overlap_end = min(p_end, f_end)
                collisions.append({
                    "time": "{:02d}:{:02d}–{:02d}:{:02d}".format(
                        overlap_start // 60, overlap_start % 60,
                        overlap_end // 60, overlap_end % 60
                    ),
                    "pro": pe.get("title", ""),
                    "perso": fe.get("title", ""),
                })

    if not collisions:
        return ""

    html = '<section class="collisions">\n'
    html += '  <div class="collisions-inner">\n'
    html += '    <div class="collisions-header">⚠️ Conflits de planning</div>\n'
    for c in collisions:
        html += '    <div class="collision-item">\n'
        html += '      <div class="collision-time">' + c["time"] + '</div>\n'
        html += '      <div class="collision-detail">'
        html += '<strong>Pro :</strong> ' + c["pro"] + '<br>'
        html += '<strong>Perso :</strong> ' + c["perso"]
        html += '</div>\n'
        html += '    </div>\n'
    html += '  </div>\n'
    html += '</section>\n'
    return html


def render_claude_code_updates(updates):
    if not updates:
        return ""

    parts = []
    parts.append('<section class="external-updates">')
    parts.append('  <div class="external-updates-inner">')
    parts.append('    <div class="panel-header">')
    parts.append('      <span class="panel-icon">🤖</span>')
    parts.append('      <span class="panel-label">Nouveautés Claude Code</span>')
    parts.append('      <span class="panel-tag tag-pro">Non encore présentées</span>')
    parts.append('    </div>')
    parts.append('    <div class="update-grid">')

    for update in updates:
        parts.append('      <article class="update-card">')
        parts.append('        <div class="update-card-meta">')
        parts.append('          <span>' + update.get("label", "") + '</span>')
        if update.get("date"):
            parts.append('          <span class="update-card-date">' + update["date"] + '</span>')
        parts.append('        </div>')
        parts.append('        <h3 class="update-card-title">' + update.get("title", "") + '</h3>')
        parts.append('        <p class="update-card-details">' + update.get("details", "") + '</p>')
        parts.append('        <a class="update-card-link" href="' + update.get("url", "") + '" target="_blank" rel="noopener">Lire le digest</a>')
        parts.append('      </article>')

    parts.append('    </div>')
    parts.append('  </div>')
    parts.append('</section>')
    return "\n".join(parts)


def render_html(stories, date_str, descriptions=None, pro_data=None, perso_data=None, claude_updates=None):
    if descriptions is None:
        descriptions = {}
    if claude_updates is None:
        claude_updates = []
    spreads = []
    for i, story in enumerate(stories):
        theme = THEMES[i % len(THEMES)]
        desc = descriptions.get(story["id"], "")
        spreads.append(render_story(story, i, theme, desc))
    spreads_html = "\n".join(spreads)

    gen_time = datetime.now(PARIS_TZ).strftime("%H:%M %Z")

    # ── Build panels ──
    panels_html = ""
    collisions_html = ""

    if pro_data or perso_data:
        panels_html = render_daily_panels(pro_data, perso_data, date_str)
        collisions_html = render_collisions(pro_data, perso_data)

    claude_updates_html = render_claude_code_updates(claude_updates)

    cover_subtitle = "Daily brief + 10 stories curated for mmenestret" if (pro_data or perso_data) else "10 stories curated for mmenestret"

    return """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Morning Edition — """ + date_str + """</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,100..900;1,9..144,100..900&family=Inter:wght@300;400;600;700;900&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
  <style>
""" + CSS + PANELS_CSS + """
  </style>
</head>
<body>
  <header class="cover">
    <div class="cover-kicker">Morning Edition</div>
    <h1 class="cover-title">""" + ("Ma<br>Journée" if (pro_data or perso_data) else "Hacker<br>News") + """</h1>
    <div class="cover-date">""" + date_str + """</div>
    <div class="cover-subtitle">""" + cover_subtitle + """</div>
  </header>

  <nav class="tab-bar">
    <button class="tab-btn active" data-tab="journee">Journée</button>
    <button class="tab-btn" data-tab="veille">Veille</button>
  </nav>

  <div id="tab-journee" class="tab-panel active">
""" + collisions_html + panels_html + """
  </div>

  <div id="tab-veille" class="tab-panel">
""" + claude_updates_html + """
    <section class="section-header" style="background:#0a0a0a;color:#fff">
      <div class="section-header-inner">
        <div class="section-kicker">Veille</div>
        <h2 class="section-title">Hacker News</h2>
      </div>
    </section>

""" + spreads_html + """
  </div>

  <footer class="colophon">
    <p>Morning Edition — curated daily by <a href="https://github.com/mmenestret">mmenestret</a></p>
    <p style="margin-top:0.5rem;opacity:0.5">Generated """ + gen_time + """</p>
  </footer>

  <script>
    document.querySelectorAll('.tab-btn').forEach(function(btn) {
      btn.addEventListener('click', function() {
        document.querySelectorAll('.tab-btn').forEach(function(b) { b.classList.remove('active'); });
        document.querySelectorAll('.tab-panel').forEach(function(p) { p.classList.remove('active'); });
        btn.classList.add('active');
        document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
      });
    });
  </script>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="Morning Edition generator")
    parser.add_argument("--pro-json", help="Path to pro data JSON")
    parser.add_argument("--perso-json", help="Path to perso data JSON")
    args = parser.parse_args()

    now = datetime.now(PARIS_TZ)
    date_str = now.strftime("%Y-%m-%d")
    day_names = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    day_name = day_names[now.weekday()]
    month_names = ["janvier", "fevrier", "mars", "avril", "mai", "juin",
                   "juillet", "aout", "septembre", "octobre", "novembre", "decembre"]
    display_date = day_name + " " + now.strftime("%d") + " " + month_names[now.month - 1] + " " + now.strftime("%Y")

    # Load pro/perso data if provided
    pro_data = None
    perso_data = None
    if args.pro_json:
        try:
            pro_data = json.loads(Path(args.pro_json).read_text(encoding="utf-8"))
            print("  Loaded pro data from " + args.pro_json)
        except Exception as e:
            print("  WARN: could not load pro JSON: " + str(e), file=sys.stderr)
    if args.perso_json:
        try:
            perso_data = json.loads(Path(args.perso_json).read_text(encoding="utf-8"))
            print("  Loaded perso data from " + args.perso_json)
        except Exception as e:
            print("  WARN: could not load perso JSON: " + str(e), file=sys.stderr)

    print("Morning Edition — " + date_str)
    print("  Fetching Claude Code updates...")
    seen_claude_updates = load_seen_urls(CLAUDE_CODE_WHATS_NEW_SEEN_FILE)
    claude_updates = fetch_claude_code_updates()
    new_claude_updates = [item for item in claude_updates if item.get("url") not in seen_claude_updates]
    print("  Found " + str(len(new_claude_updates)) + " new Claude Code update(s)")

    print("  Fetching top " + str(FETCH_TOP_N) + " HN stories...")

    story_ids = fetch_top_stories()
    if not story_ids:
        print("  ERROR: could not fetch top stories", file=sys.stderr)
        sys.exit(1)

    print("  Fetching details...")
    stories = fetch_all_stories(story_ids)
    print("  Got " + str(len(stories)) + " valid stories")

    print("  Curating top " + str(NUM_STORIES) + "...")
    curated = curate_stories(stories)
    print("  Selected " + str(len(curated)) + " stories:")
    for i, s in enumerate(curated):
        print("    {:2d}. [{:.1f}] {}".format(i + 1, score_story(s), s["title"][:70]))

    print("  Fetching article descriptions...")
    descriptions = fetch_descriptions(curated)
    desc_count = sum(1 for v in descriptions.values() if v)
    print("  Got " + str(desc_count) + "/" + str(len(curated)) + " descriptions")

    print("  Generating HTML...")
    html = render_html(curated, display_date, descriptions, pro_data, perso_data, new_claude_updates)

    MAGAZINES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = MAGAZINES_DIR / (date_str + ".html")
    out_path.write_text(html, encoding="utf-8")
    print("  Written to " + str(out_path))

    # Push
    print("  Pushing to GitHub...")
    os.chdir(REPO_DIR)
    subprocess.run(["git", "add", "magazines/"], check=True)
    result = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
    if result.returncode == 0:
        print("  No changes to commit")
    else:
        subprocess.run(["git", "commit", "-m", "📰 " + date_str], check=True)
        push = subprocess.run(["git", "push", "origin", "main"], capture_output=True, text=True)
        if push.returncode != 0:
            print("  ERROR: push failed: " + push.stderr, file=sys.stderr)
            sys.exit(1)
        print("  Pushed!")

    if new_claude_updates:
        save_seen_urls(
            CLAUDE_CODE_WHATS_NEW_SEEN_FILE,
            seen_claude_updates.union({item["url"] for item in new_claude_updates}),
        )
        print("  Recorded " + str(len(new_claude_updates)) + " Claude Code update(s) as presented")

    page_url = "https://geekocephale.com/morning-edition/magazines/" + date_str + ".html"
    print("")
    print("PUBLISH_URL=" + page_url)


if __name__ == "__main__":
    main()
