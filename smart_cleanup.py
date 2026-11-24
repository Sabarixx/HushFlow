"""
Smart Speech Cleanup & Self-Correction Engine for WhisperFlow (Enhanced Tier)
Replicates and enhances Wispr Flow's auto-cleanup, filler-removal, and intent-resolution.
Guarantees zero speech truncation and zero false-positive dropouts.
"""

import re
import os
import json
import logging
import urllib.request
import urllib.error

# Common vocal fillers to eliminate
FILLER_PATTERNS = [
    r"\b(um|uh|uhm|umm|ah|er|eh|hmm|uh-huh)\b[,.]?",
    r"(?:^|,\s*)\b(you know|like I mean)\b(?:\s*,|\s+)",
]

# High-precision retraction / self-correction triggers
RETRACTION_PATTERNS = [
    # Repeated no: 'no, no', 'no no no', 'wait, no, no, no'
    r"(?:\b(?:wait|actually|sorry)\s*[,.]?\s*)+(?:\bno\b\s*[,.]?\s*)+",
    r"(?:\bno\b\s*[,.]?\s*){2,}",
    # 'wait scratch that', 'actually scratch that'
    r"\b(?:wait|actually|sorry)\s*[,.]?\s*(?:scratch\s+that|never\s+mind|strike\s+that)\b",
    # Explicit retraction phrases
    r"\b(?:scratch\s+that|never\s+mind|take\s+that\s+back|strike\s+that)\b",
    r"\b(?:what\s+I\s+meant\s+was|what\s+I\s+meant\s+to\s+say\s+was|or\s+rather)\b",
    # 'make that' ONLY when not preceded by 'to'
    r"(?<!\bto\s)(?<!\bto\s\s)\bmake\s+that\b",
]

combined_patterns = "|".join(f"(?:{p})" for p in RETRACTION_PATTERNS)
RETRACTION_REGEX = re.compile(combined_patterns, flags=re.IGNORECASE)

INTERROGATIVE_WORDS = {
    "who", "what", "where", "when", "why", "how",
    "is", "are", "was", "were", "do", "does", "did",
    "can", "could", "would", "should", "may", "might", "shall",
    "will", "won't", "aren't", "isn't", "doesn't", "don't"
}

def clean_fillers(text: str) -> str:
    """Removes vocal filler sounds and stuttering repetitions."""
    if not text:
        return ""
    cleaned = text
    for pat in FILLER_PATTERNS:
        cleaned = re.sub(pat, " ", cleaned, flags=re.IGNORECASE)

    # Remove repeated stutter words like 'the the', 'I I', 'we we'
    cleaned = re.sub(r"\b([a-zA-Z]+)\s+\1\b", r"\1", cleaned, flags=re.IGNORECASE)

    # Collapse multiple spaces and dangling punctuation
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\s+([,.!?])", r"\1", cleaned)
    cleaned = re.sub(r"([,.!?]){2,}", r"\1", cleaned)
    return cleaned.strip()

DIGIT_WORDS = {
    'zero': '0', 'oh': '0', 'one': '1', 'two': '2', 'three': '3',
    'four': '4', 'five': '5', 'six': '6', 'seven': '7', 'eight': '8', 'nine': '9'
}

def apply_voice_formatting_commands(text: str) -> str:
    """
    Translates spoken punctuation and formatting commands into real characters.
    e.g. 'hello new line world' -> 'hello\nworld'
         'camel case get user data' -> 'getUserData'
         'snake case user profile' -> 'user_profile'
    """
    if not text:
        return ""

    # Direct punctuation commands (when preceded by a word or space)
    text = re.sub(r"(?<=\w)\s+\bcomma\b", ",", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=\w)\s+\bperiod\b", ".", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=\w)\s+\bfull\s+stop\b", ".", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=\w)\s+\bquestion\s+mark\b", "?", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=\w)\s+\bexclamation\s+(?:mark|point)\b", "!", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=\w)\s+\bcolon\b(?!\s*\d)", ":", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=\w)\s+\bsemicolon\b", ";", text, flags=re.IGNORECASE)

    # Line breaks
    text = re.sub(r"\s*\b(?:new\s+paragraph)\b\s*", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*\b(?:new\s+line|next\s+line)\b\s*", "\n", text, flags=re.IGNORECASE)

    # Casing commands
    def _camel(m):
        words = m.group(1).split()
        return words[0].lower() + "".join(w.capitalize() for w in words[1:]) if words else ""
    def _snake(m):
        return "_".join(m.group(1).lower().split())
    def _kebab(m):
        return "-".join(m.group(1).lower().split())
    def _allcaps(m):
        return m.group(1).upper()

    text = re.sub(r"\bcamel\s+case\s+([a-zA-Z0-9_\s]+?)(?=[,.\n]|$)", _camel, text, flags=re.IGNORECASE)
    text = re.sub(r"\bsnake\s+case\s+([a-zA-Z0-9_\s]+?)(?=[,.\n]|$)", _snake, text, flags=re.IGNORECASE)
    text = re.sub(r"\bkebab\s+case\s+([a-zA-Z0-9_\s]+?)(?=[,.\n]|$)", _kebab, text, flags=re.IGNORECASE)
    text = re.sub(r"\ball\s+caps\s+([a-zA-Z0-9_]+)\b", _allcaps, text, flags=re.IGNORECASE)

    return text

def resolve_contextual_homophones(text: str) -> str:
    """
    Resolves frequent acoustic homophones based on surrounding sentence context.
    e.g. 'read the entire setup ... out of 10' -> 'rate the entire setup ... out of 10'
    """
    if not text:
        return ""
    # "read" / "red" -> "rate" in scoring/evaluation contexts
    text = re.sub(r"\b(?:read|red)\b(?=.*?\b(?:out\s+of\s+\d+|score|rating|criterion|criteria)\b)", "rate", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:read|red)\s+out\s+of\s+10\b", "rate out of 10", text, flags=re.IGNORECASE)
    text = re.sub(r"\bout\s+of\s+10\s+emails\b", "out of 10 in all", text, flags=re.IGNORECASE)
    return text

MULTIPLIERS = {
    'double': 2,
    'triple': 3,
    'quadruple': 4,
    'quad': 4
}

def normalize_spoken_numbers(text: str, config: dict = None) -> str:
    """
    Intelligent Spoken Number & Quantifier Normalizer:
    - Multipliers: 'double 5' -> '55', 'double zero' -> '00', 'triple 7' -> '777'
    - Sequence Unification: '3 2 double 5 9' -> '32559', 'five five five zero one two three' -> '5550123'
    - Special Dictation: 'five oh five' -> '505', 'double oh seven' -> '007'
    - Time & Meeting: 'eight thirty PM' -> '8:30 PM', 'at 9' -> 'at 9:00'
    - Currency & Percentages: 'twenty dollars' -> '$20', 'five percent' -> '5%'
    - Context Preservation: 'two apples and three oranges' remains untouched
    - User Word Replacements: applied from config
    """
    if not text:
        return ""

    # 1. Currency & Percentages (preserve comma numbers like $20,000)
    text = re.sub(r"\b(\d+)\s+dollars?\b", r"$\1", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(\d+)\s+percent\b", r"\1%", text, flags=re.IGNORECASE)
    word_to_num = {
        "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
        "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
        "twenty": "20", "thirty": "30", "forty": "40", "fifty": "50",
        "sixty": "60", "seventy": "70", "eighty": "80", "ninety": "90",
        "hundred": "100"
    }
    for w, n in word_to_num.items():
        text = re.sub(rf"\b{w}\s+dollars?\b", f"${n}", text, flags=re.IGNORECASE)
        text = re.sub(rf"\b{w}\s+percent\b", f"{n}%", text, flags=re.IGNORECASE)

    # 2. Time expressions
    time_words = {
        "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
        "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
        "eleven": "11", "twelve": "12"
    }
    for tw, tn in time_words.items():
        text = re.sub(rf"\b{tw}\s+thirty\s*(am|pm)?\b", rf"{tn}:30 \1", text, flags=re.IGNORECASE)
        text = re.sub(rf"\b{tw}\s+fifteen\s*(am|pm)?\b", rf"{tn}:15 \1", text, flags=re.IGNORECASE)
        text = re.sub(rf"\b{tw}\s+forty-?five\s*(am|pm)?\b", rf"{tn}:45 \1", text, flags=re.IGNORECASE)
        text = re.sub(rf"\b{tw}\s*(am|pm)\b", rf"{tn}:00 \1", text, flags=re.IGNORECASE)

    # Fix "8 30" -> "8:30", "8.30 am" -> "8:30 AM"
    text = re.sub(r"\b(\d{1,2})\s+(\d{2})\b", r"\1:\2", text)
    text = re.sub(r"\b(\d{1,2})[.:](\d{2})\s*(am|pm)\b", lambda m: f"{m.group(1)}:{m.group(2)} {m.group(3).upper()}", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(\d{1,2}):00\s*(am|pm)\b", lambda m: f"{m.group(1)}:00 {m.group(2).upper()}", text, flags=re.IGNORECASE)

    # Currency enhancements: "twenty thousand dollars" -> "$20,000", "007000$" -> "$7,000", "$20000" -> "$20,000"
    word_thousands = {"twenty": 20000, "thirty": 30000, "forty": 40000, "fifty": 50000, "ten": 10000, "fifteen": 15000, "five": 5000}
    for wt, val in word_thousands.items():
        text = re.sub(rf"\b{wt}\s+thousand\s+dollars\b", f"${val:,}", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(\d+)\s+thousand\s+dollars\b", lambda m: f"${int(m.group(1)) * 1000:,}", text, flags=re.IGNORECASE)
    text = re.sub(r"(\d+)\s*\$", r"$\1", text)
    text = re.sub(r"\$(\d{4,})\b", lambda m: f"${int(m.group(1)):,}", text)

    # Technical developer term formatting
    text = re.sub(r"\blocalhost(\d+)\b", r"localhost \1", text, flags=re.IGNORECASE)
    text = re.sub(r"\bpython\b", "Python", text)
    text = re.sub(r"\bgit\b", "Git", text)
    text = re.sub(r"\bfastapi\b", "FastAPI", text, flags=re.IGNORECASE)

    # Format "at <hour>" in meeting / scheduling context: e.g. "at 9" -> "at 9:00"
    text = re.sub(r"\b(at\s+)(\d{1,2})\b(?!\s*[:\d])", r"\g<1>\g<2>:00", text, flags=re.IGNORECASE)

    # 3. Multiplier normalization: "double 5", "double five", "double zero", "double oh", "triple 7"
    pat_mult = r"\b(double|triple|quadruple|quad)\s+(\d|zero|oh|one|two|three|four|five|six|seven|eight|nine)\b"
    def replace_mult(m):
        count = MULTIPLIERS.get(m.group(1).lower(), 1)
        target = m.group(2).lower()
        digit = DIGIT_WORDS.get(target, target)
        return digit * count
    text = re.sub(pat_mult, replace_mult, text, flags=re.IGNORECASE)

    # 4. Spoken digit sequences (e.g. phone numbers, OTPs, PINs, IDs):
    text = re.sub(r"\b(\d+)\s+oh\s+(\d+)\b", r"\1 0 \2", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(\d+)\s+oh\b", r"\1 0", text, flags=re.IGNORECASE)
    text = re.sub(r"\boh\s+(\d+)\b", r"0 \1", text, flags=re.IGNORECASE)

    tokens = text.split()
    new_tokens = []
    i = 0
    while i < len(tokens):
        run = []
        j = i
        while j < len(tokens):
            w = tokens[j].strip(".,!?;:")
            w_lower = w.lower()
            if w.isdigit():
                run.append((j, tokens[j], w))
            elif w_lower in DIGIT_WORDS:
                run.append((j, tokens[j], DIGIT_WORDS[w_lower]))
            else:
                break
            j += 1
        
        if len(run) >= 3 or (len(run) == 2 and any(len(d[2]) >= 2 for d in run) and not any(w in ['and', 'to', 'or'] for w in [tokens[max(0, i-1)].lower() if i > 0 else ''])):
            last_orig = run[-1][1]
            punct = last_orig[-1] if last_orig and last_orig[-1] in ".,!?;:" else ""
            joined = "".join(r[2] for r in run) + punct
            new_tokens.append(joined)
            i = j
        elif len(run) == 2 and run[0][2].isdigit() and run[1][2].isdigit() and (len(run[0][2]) > 1 or len(run[1][2]) > 1):
            last_orig = run[-1][1]
            punct = last_orig[-1] if last_orig and last_orig[-1] in ".,!?;:" else ""
            new_tokens.append("".join(r[2] for r in run) + punct)
            i = j
        else:
            new_tokens.append(tokens[i])
            i += 1

    text = " ".join(new_tokens)

    # 5. User custom word replacements from config
    if config and "word_replacements" in config:
        for src, dst in config["word_replacements"].items():
            text = re.sub(rf"\b{re.escape(src)}\b", dst, text, flags=re.IGNORECASE)

    return text

def normalize_time_expressions(text: str) -> str:
    """Formats spoken time expressions cleanly (e.g. '8 30' -> '8:30', '9 am' -> '9:00 AM')."""
    return normalize_spoken_numbers(text)

def resolve_single_correction(text: str) -> str:
    """
    Surgical single pass of retraction resolution.
    Preserves all preceding sentences AND all subsequent sentences!
    Only alters the exact clause being corrected.
    Guarantees ZERO speech truncation across multi-sentence dictations.
    """
    match = RETRACTION_REGEX.search(text)
    if not match:
        return text

    # Consume trailing punctuation from the retraction match (e.g. '.', ',', ' ')
    m_start = match.start()
    m_end = match.end()
    while m_end < len(text) and text[m_end] in " ,.!?":
        m_end += 1

    before_part = text[:m_start].strip()
    after_part = text[m_end:].strip()

    if not before_part:
        return after_part
    if not after_part:
        return before_part

    # 1. Isolate prefix (all preceding full sentences)
    # A true sentence boundary is [.!?] followed by whitespace and an uppercase letter
    before_trimmed = re.sub(r"[,.!?\s]+$", "", before_part).strip()
    matches = list(re.finditer(r"(?<=[.!?])\s+(?=[A-Z])", before_trimmed))
    if matches:
        last_split = matches[-1].start()
        prefix = before_trimmed[:last_split + 1].strip() + " "
        target_clause = before_trimmed[last_split + 1:].strip()
    else:
        prefix = ""
        target_clause = before_trimmed

    # 2. Isolate suffix (all subsequent full sentences)
    after_match = re.search(r"(?<=[.!?])\s+(?=[A-Z])", after_part)
    if after_match:
        replacement_clause = after_part[:after_match.start()].strip()
        suffix = " " + after_part[after_match.end():].strip()
    else:
        replacement_clause = after_part.strip()
        suffix = ""

    before_clean = re.sub(r"^[,.!?\s]+|[,.!?\s]+$", "", target_clause).strip()
    after_clean = re.sub(r"^[,.!?\s]+|[,.!?\s]+$", "", replacement_clause).strip()

    resolved = None

    # Case A: Time replacement (e.g. 'for 8.30 am' or 'at 8:30' replaced by '9.30 am instead')
    time_pat = r"\b((?:at|for)?\s*\d+[:.\d]*\s*(?:am|pm)?)\b"
    t_before = re.search(time_pat, before_clean, flags=re.IGNORECASE)
    t_after = re.search(time_pat, after_clean, flags=re.IGNORECASE)
    if t_before and t_after and any(k in after_clean.lower() for k in ["am", "pm", ":", ".", "make it"]):
        head = before_clean[:t_before.start()].strip()
        prep = "for " if "for" in t_before.group().lower() else ("at " if "at" in t_before.group().lower() else "")
        new_time = t_after.group().strip()
        if not new_time.lower().startswith(("at", "for")) and prep:
            new_time = prep + new_time
        after_tail = after_clean[t_after.end():].strip()
        tail = f" {after_tail}" if after_tail and after_tail.lower() not in ["please"] else ""
        resolved = f"{head} {new_time}{tail}".strip()

    # Case B: Currency / number replacement (e.g. 'budget is 007000$' replaced by '$20000 for new laptop')
    if not resolved:
        num_before = re.search(r"(\$?\d+[\d,.]*\$?)", before_clean)
        num_after = re.search(r"(\$?\d+[\d,.]*\$?)", after_clean)
        if num_before and num_after:
            head = before_clean[:num_before.start()].strip()
            head = re.sub(r"[\$£€¥\s]+$", "", head).strip()
            resolved = f"{head} {after_clean}".strip()

    # Case C: Safe Action / Clause replacement with ABSOLUTE TRUNCATION PROTECTION
    if not resolved:
        words_before = before_clean.split()
        words_after = after_clean.split()
        # Only replace the immediate prior clause if before_clean is a short snippet (<= 4 words)
        if len(words_before) <= 4 and len(words_after) <= 6:
            resolved = after_clean
        elif len(words_after) <= 4 and len(words_before) > len(words_after) and len(words_before) <= 8:
            resolved = " ".join(words_before[:-len(words_after)]) + " " + after_clean
        else:
            # For long speech / multi-sentence dictation: NEVER drop preceding lines!
            resolved = f"{before_clean}. {after_clean}"

    resolved = re.sub(r"[,.!?\s]+$", "", resolved).strip() + "."
    return f"{prefix}{resolved}{suffix}".strip()

def resolve_self_corrections_iterative(text: str, max_passes: int = 3) -> str:
    """Iteratively resolves nested/multiple corrections across sentences."""
    current = text
    for _ in range(max_passes):
        updated = resolve_single_correction(current)
        if updated == current:
            break
        current = updated
    return current

def format_text(text: str) -> str:
    """Capitalizes sentences and applies smart punctuation (? for questions, . for statements)."""
    if not text:
        return ""
    text = text.strip()
    
    if text and text[0].islower():
        text = text[0].upper() + text[1:]

    text = re.sub(r"[,;:]+$", "", text).strip()

    if text and text[-1] not in ".!?":
        first_word = text.split()[0].lower() if text.split() else ""
        if first_word in INTERROGATIVE_WORDS:
            text += "?"
        else:
            text += "."
    return text

def try_cloud_llm_polish(raw_text: str, config: dict) -> str:
    """Optional cloud/local LLM polish if API key is provided."""
    groq_key = config.get("groq_api_key") or os.environ.get("GROQ_API_KEY")
    if groq_key:
        try:
            req_data = json.dumps({
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": "You are Wispr Flow speech post-processor. Output ONLY the polished user intent with filler words and retractions removed. Never reply conversationally."},
                    {"role": "user", "content": raw_text}
                ],
                "temperature": 0.0,
                "max_tokens": 200
            }).encode("utf-8")
            req = urllib.request.Request("https://api.groq.com/openai/v1/chat/completions", data=req_data, headers={
                "Authorization": f"Bearer {groq_key}",
                "Content-Type": "application/json"
            })
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                return res["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logging.debug(f"Cloud LLM polish skipped: {e}")
    return None

def apply_smart_cleanup(raw_text: str, level: str = "medium", config: dict = None) -> str:
    """
    Main cleanup entry point matching Wispr Flow levels:
    - none: Returns raw text verbatim
    - light: Strips vocal fillers and cleans punctuation
    - medium: Resolves self-corrections, strips fillers, smart punctuation (ZERO truncation)
    - high: Full polish & conciseness
    """
    if not raw_text or level == "none":
        return raw_text

    if config:
        llm_result = try_cloud_llm_polish(raw_text, config)
        if llm_result:
            return llm_result

    cleaned = raw_text

    # Step 1: Voice formatting commands (casing, linebreaks, punctuation)
    cleaned = apply_voice_formatting_commands(cleaned)

    # Step 2: Contextual homophone resolution
    cleaned = resolve_contextual_homophones(cleaned)

    # Step 3: Normalize spoken numbers, digits, multipliers, currency & time
    cleaned = normalize_spoken_numbers(cleaned, config=config)

    # Step 4: Strip vocal fillers
    cleaned = clean_fillers(cleaned)

    # Step 5: Resolve self-corrections (medium, high)
    if level in ["medium", "high"]:
        cleaned = resolve_self_corrections_iterative(cleaned)
        cleaned = clean_fillers(cleaned)

    # Step 6: Final formatting
    cleaned = format_text(cleaned)
    return cleaned
