"""
Life-Harness: Runtime Adaptation System for Small-LLM Agent Reliability.

Implements four layers that wrap every LLM interaction:
  H2 – Action Gate:       Tool call rescue, normalization, deduplication
  H3 – Task Context:      Dynamic tool hints based on task classification
  H4 – Trajectory Regulation: Failure detection and recovery injection
  H5 – Procedural Skills: Domain micro-hints retrieved by keyword/task-type

Design: Pure stdlib Python, no external deps, fast per-turn execution.
"""

from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

TOOL_NAMES: List[str] = [
    "read_file", "list_directory", "search_files", "write_file",
    "web_search", "rag_query", "send_email", "send_whatsapp",
    "set_reminder", "app_launcher", "clipboard_read", "clipboard_write",
    "pdf_reader", "csv_reader", "system_info", "screenshot",
    "git_status", "url_summarizer", "open_url", "type_in_app",
    "open_file", "browse_web", "mcp_call", "orchestrate",
    "consolidate_memory", "start_goal", "goal_status", "cancel_goal",
    "describe_image", "describe_screen", "skill_factory_create",
    "skill_factory_install",
]

SIMILARITY_THRESHOLD: float = 0.55
DEDUP_WINDOW: int = 4
DEDUP_REPEAT_LIMIT: int = 2
TRAJECTORY_WINDOW: int = 4
BUDGET_FORCE_ROUNDS: int = 8

# ═══════════════════════════════════════════════════════════════════════════════
# H3 – TASK CONTEXT: Task type definitions & tool hints
# ═══════════════════════════════════════════════════════════════════════════════

TASK_TYPES: Dict[str, Dict[str, Any]] = {
    "file_operation": {
        "keywords": [
            "file", "read file", "write file", "save file", "open file",
            "create file", "delete file", "folder", "directory", "path",
            "list files", "list directory", "move file", "copy file", "rename",
            "csv", "pdf", "txt", "json", "document", "toml", "yaml",
        ],
        "tools": ["read_file", "write_file", "list_directory", "search_files", "open_file", "pdf_reader", "csv_reader"],
    },
    "web_search": {
        "keywords": [
            "search the web", "search for", "search online", "google",
            "find online", "look up online", "web search", "internet",
            "browse", "url", "website", "link", "news", "article",
        ],
        "tools": ["web_search", "browse_web", "url_summarizer", "open_url"],
    },
    "communication": {
        "keywords": [
            "email", "send email", "send message", "whatsapp", "notify",
            "remind me", "set reminder", "reminder", "contact", "mail",
            "schedule meeting", "meeting", "call",
        ],
        "tools": ["send_email", "send_whatsapp", "set_reminder"],
    },
    "system_action": {
        "keywords": [
            "launch", "open app", "run app", "execute", "system info",
            "screenshot", "screen", "describe screen", "describe my screen",
            "what's on my screen", "type in", "click", "clipboard",
            "copy", "paste", "application", "program", "window",
            "git status", "git", "cpu", "ram", "memory", "disk",
            "battery", "usage", "uptime", "system",
        ],
        "tools": ["app_launcher", "screenshot", "type_in_app", "clipboard_read",
                  "clipboard_write", "system_info", "git_status", "describe_screen",
                  "describe_image"],
    },
    "knowledge_query": {
        "keywords": [
            "what is a", "what is the", "explain", "how does", "why does",
            "tell me about", "summarize", "knowledge", "information",
            "history of", "definition", "meaning of", "difference between",
            "compare", "capital of", "who is", "when was",
        ],
        "tools": ["rag_query", "web_search", "url_summarizer"],
    },
    "creative_writing": {
        "keywords": [
            "write a poem", "write a story", "compose", "draft a letter",
            "create content", "poem", "story", "essay", "blog post",
            "letter", "report", "generate text", "rewrite", "improve text",
            "haiku", "write me",
        ],
        "tools": ["rag_query", "write_file", "clipboard_write"],
    },
}

# ═══════════════════════════════════════════════════════════════════════════════
# H5 – PROCEDURAL SKILLS: Micro-hint library
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Skill:
    id: str
    trigger_keywords: Tuple[str, ...]
    task_type: str
    hint: str


SKILLS: Tuple[Skill, ...] = (
    # File operations
    Skill("file_read_hint", ("read", "open", "view", "show file"), "file_operation",
          "Use read_file with the absolute path. If unsure of path, use list_directory first."),
    Skill("file_write_hint", ("write", "save", "create file"), "file_operation",
          "Use write_file with path and content. Confirm success before reporting done."),
    Skill("file_search_hint", ("find file", "search", "locate", "where is"), "file_operation",
          "Use search_files with a pattern. Narrow with directory path if you know the area."),
    Skill("dir_list_hint", ("list", "folder", "directory", "ls"), "file_operation",
          "Use list_directory with the folder path. Omit path for current directory."),
    Skill("pdf_read_hint", ("pdf", "document", "extract text"), "file_operation",
          "Use pdf_reader with the file path to extract text content from PDFs."),
    Skill("csv_read_hint", ("csv", "spreadsheet", "data file", "table"), "file_operation",
          "Use csv_reader with path. Specify columns or row range if only part is needed."),
    # Web operations
    Skill("web_search_hint", ("search", "google", "find online", "look up"), "web_search",
          "Use web_search with a concise query. Summarize results, don't dump raw HTML."),
    Skill("url_open_hint", ("open url", "visit", "go to", "navigate"), "web_search",
          "Use open_url to open in browser, or url_summarizer to get content without opening."),
    Skill("browse_hint", ("browse", "website", "web page"), "web_search",
          "Use browse_web for interactive browsing. Use url_summarizer for quick content fetch."),
    Skill("url_summary_hint", ("summarize page", "what does this link say"), "web_search",
          "Use url_summarizer with the URL. Returns key content without opening a browser."),
    # Communication
    Skill("email_hint", ("email", "mail", "send message"), "communication",
          "Use send_email with to, subject, body. Double-check recipient before sending."),
    Skill("whatsapp_hint", ("whatsapp", "text", "message"), "communication",
          "Use send_whatsapp with contact name and message. Confirm contact identity first."),
    Skill("reminder_hint", ("remind", "reminder", "schedule", "later"), "communication",
          "Use set_reminder with message and time. Use natural time format like '5pm' or 'in 2 hours'."),
    # System actions
    Skill("app_launch_hint", ("launch", "open app", "start", "run program"), "system_action",
          "Use app_launcher with the application name. Common names: notepad, chrome, explorer."),
    Skill("screenshot_hint", ("screenshot", "capture screen", "what's on screen"), "system_action",
          "Use screenshot to capture current display. Follow with describe_screen to analyze it."),
    Skill("type_hint", ("type", "enter text", "fill in", "keyboard"), "system_action",
          "Use type_in_app with the text to type. Ensure the target app is focused first."),
    Skill("clipboard_hint", ("copy", "paste", "clipboard"), "system_action",
          "Use clipboard_read to get current content, clipboard_write to set new content."),
    Skill("git_hint", ("git", "repo", "commit", "branch", "status"), "system_action",
          "Use git_status to check repository state. Report branch, changes, and recent commits."),
    Skill("sysinfo_hint", ("system info", "computer", "specs", "os"), "system_action",
          "Use system_info to get OS, CPU, RAM, disk details. Summarize key facts."),
    # Knowledge queries
    Skill("rag_hint", ("knowledge", "remember", "what do you know", "memory"), "knowledge_query",
          "Use rag_query to search local knowledge base before searching the web."),
    Skill("explain_hint", ("explain", "what is", "define", "meaning"), "knowledge_query",
          "Answer from knowledge first. If uncertain, use web_search to verify facts."),
    Skill("compare_hint", ("compare", "difference", "vs", "versus"), "knowledge_query",
          "Structure comparison as a clear list of differences. Use web_search if needed."),
    Skill("describe_img_hint", ("image", "picture", "photo", "what is this"), "knowledge_query",
          "Use describe_image with the image path to get a visual description."),
    Skill("screen_desc_hint", ("what's on screen", "describe screen"), "knowledge_query",
          "Use describe_screen to analyze current display content and report what's visible."),
    # Creative writing
    Skill("write_content_hint", ("write", "compose", "draft", "create"), "creative_writing",
          "Generate content directly in response. Use write_file only if saving to disk is requested."),
    Skill("rewrite_hint", ("rewrite", "improve", "rephrase", "edit"), "creative_writing",
          "Read original with read_file or clipboard_read, then provide improved version."),
    # Goal management
    Skill("goal_start_hint", ("goal", "plan", "objective", "task"), "system_action",
          "Use start_goal with a clear objective description. Track progress with goal_status."),
    Skill("goal_status_hint", ("progress", "how's it going", "status"), "system_action",
          "Use goal_status to check active goal progress. Report completion percentage."),
    Skill("orchestrate_hint", ("orchestrate", "multi-step", "complex task"), "system_action",
          "Use orchestrate for multi-tool workflows. Break complex tasks into ordered steps."),
    Skill("mcp_hint", ("mcp", "external tool", "plugin", "integration"), "system_action",
          "Use mcp_call for external service integrations. Specify the service and action."),
    Skill("skill_create_hint", ("new skill", "teach", "learn to"), "system_action",
          "Use skill_factory_create to define a new reusable skill from the current interaction."),
    Skill("memory_hint", ("remember", "save memory", "store", "note"), "system_action",
          "Use consolidate_memory to save important information for future conversations."),
)

# ═══════════════════════════════════════════════════════════════════════════════
# H2 – ACTION GATE: Tool call extraction, normalization, dedup
# ═══════════════════════════════════════════════════════════════════════════════

# Pre-compiled patterns for extraction (ordered by specificity)
_RE_JSON_BLOCK = re.compile(
    r"```(?:json)?\s*(\{[^`]*?\})\s*```", re.DOTALL
)
_RE_BARE_JSON_START = re.compile(
    r'\{\s*"(?:tool|name|function|action)"', re.DOTALL
)


def _extract_balanced_json(text: str, start: int) -> Optional[str]:
    """Extract a balanced JSON object starting at position `start` (which must be '{')."""
    if start >= len(text) or text[start] != '{':
        return None
    depth = 0
    in_string = False
    escape_next = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape_next:
            escape_next = False
            continue
        if ch == '\\' and in_string:
            escape_next = True
            continue
        if ch == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None
_RE_XML_TOOL = re.compile(
    r"<tool(?:_call)?[^>]*>\s*(.*?)\s*</tool(?:_call)?>", re.DOTALL
)
_RE_XML_ATTRS = re.compile(
    r'<tool_call\s+name=["\']([^"\']+)["\']\s*'
    r'(?:args=(?:"([^"]*)"|\'([^\']*)\'))?\s*/?>',
    re.DOTALL,
)
_RE_KWARGS = re.compile(
    r"(?:call|use|run|invoke|execute)\s+(\w+)\s*\(([^)]*)\)", re.IGNORECASE
)
_RE_PROSE_TOOL = re.compile(
    r"(?:I(?:'ll| will| should| need to)|Let me|Going to)\s+"
    r"(?:use|call|invoke|run|execute)\s+(?:the\s+)?(\w+)"
    r"(?:\s+(?:with|using|passing)\s+(.+?))?(?:\.|$)",
    re.IGNORECASE,
)
_RE_ANSWER_PREAMBLE = re.compile(
    r"^(?:(?:Sure|OK|Okay|Certainly|Of course)[,!.]?\s*)*"
    r"(?:(?:The|Here(?:'s| is)|Based on)[^:]*:\s*)?",
    re.IGNORECASE,
)
_RE_ANSWER_SUFFIX = re.compile(
    r"\s*(?:Let me know if (?:you need|there's) anything else.*|"
    r"Is there anything else.*|Hope (?:this|that) helps.*|"
    r"Feel free to ask.*)$",
    re.IGNORECASE,
)


@dataclass
class ToolCall:
    """Normalized tool call representation."""
    name: str
    args: Dict[str, Any]
    confidence: float = 1.0  # 0.0–1.0 extraction confidence

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "args": self.args, "confidence": self.confidence}

    def signature(self) -> str:
        """Stable string for dedup comparison."""
        return f"{self.name}::{json.dumps(self.args, sort_keys=True, default=str)}"


def _parse_json_obj(text: str) -> Optional[Dict[str, Any]]:
    """Attempt JSON parse with common fixups."""
    # Strip trailing commas before } or ]
    cleaned = re.sub(r",\s*([}\]])", r"\1", text)
    # Fix single quotes to double
    cleaned = cleaned.replace("'", '"')
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        pass
    # Try wrapping bare key:value as JSON
    try:
        return json.loads("{" + cleaned + "}")
    except (json.JSONDecodeError, ValueError):
        return None


def _kwargs_to_dict(kwargs_str: str) -> Dict[str, Any]:
    """Parse 'key=value, key2=value2' or 'key="value"' into dict."""
    result: Dict[str, Any] = {}
    # Match key=value pairs (value can be quoted or unquoted)
    for m in re.finditer(r'(\w+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|(\S+))', kwargs_str):
        key = m.group(1)
        val = m.group(2) if m.group(2) is not None else (
            m.group(3) if m.group(3) is not None else m.group(4)
        )
        result[key] = val
    # If no key=value found, treat as positional
    if not result and kwargs_str.strip():
        parts = [p.strip().strip("\"'") for p in kwargs_str.split(",")]
        if len(parts) == 1:
            result["input"] = parts[0]
        else:
            for i, p in enumerate(parts):
                result[f"arg{i}"] = p
    return result


def _match_tool_name(candidate: str, valid_names: List[str]) -> Optional[Tuple[str, float]]:
    """
    Find closest tool name via SequenceMatcher.
    Returns (matched_name, score) if above threshold, else None.
    """
    candidate_lower = candidate.lower().strip().replace("-", "_").replace(" ", "_")
    # Exact match fast path
    if candidate_lower in valid_names:
        return (candidate_lower, 1.0)
    best_name: Optional[str] = None
    best_score: float = 0.0
    for name in valid_names:
        score = SequenceMatcher(None, candidate_lower, name).ratio()
        if score > best_score:
            best_score = score
            best_name = name
    if best_name and best_score >= SIMILARITY_THRESHOLD:
        return (best_name, best_score)
    return None


def _extract_tool_call_from_json(obj: Dict[str, Any], valid_names: List[str]) -> Optional[ToolCall]:
    """Extract tool call from a parsed JSON object with various key conventions."""
    # Try common key patterns for tool name
    name_keys = ("tool", "name", "function", "action", "tool_name", "function_name")
    raw_name: Optional[str] = None
    for k in name_keys:
        if k in obj and isinstance(obj[k], str):
            raw_name = obj[k]
            break
    if not raw_name:
        return None
    # Match to valid tool
    match = _match_tool_name(raw_name, valid_names)
    if not match:
        return None
    tool_name, score = match
    # Extract args
    args_keys = ("args", "arguments", "parameters", "params", "input", "kwargs")
    args: Dict[str, Any] = {}
    for k in args_keys:
        if k in obj:
            val = obj[k]
            if isinstance(val, dict):
                args = val
            elif isinstance(val, str):
                parsed = _parse_json_obj(val)
                args = parsed if parsed else {"input": val}
            break
    else:
        # Use remaining keys as args (exclude the name key)
        args = {k: v for k, v in obj.items() if k not in name_keys}
    return ToolCall(name=tool_name, args=args, confidence=score * 0.95)


def extract_tool_call(raw_output: str, valid_names: Optional[List[str]] = None) -> Optional[ToolCall]:
    """
    Multi-strategy tool call extraction from raw LLM output.
    Tries JSON block, bare JSON, XML, kwargs, prose — in order.
    Returns the highest-confidence extraction, or None.
    """
    if valid_names is None:
        valid_names = TOOL_NAMES
    if not raw_output or not raw_output.strip():
        return None

    candidates: List[ToolCall] = []

    # Strategy 1: JSON in code block
    for m in _RE_JSON_BLOCK.finditer(raw_output):
        obj = _parse_json_obj(m.group(1))
        if obj:
            tc = _extract_tool_call_from_json(obj, valid_names)
            if tc:
                tc.confidence = min(tc.confidence * 1.05, 1.0)  # Bonus for explicit block
                candidates.append(tc)

    # Strategy 2: Bare JSON object (brace-balanced extraction)
    for m in _RE_BARE_JSON_START.finditer(raw_output):
        json_str = _extract_balanced_json(raw_output, m.start())
        if json_str:
            obj = _parse_json_obj(json_str)
            if obj:
                tc = _extract_tool_call_from_json(obj, valid_names)
                if tc:
                    candidates.append(tc)

    # Strategy 3: XML-style <tool_call name="..." args="..."/>
    for m in _RE_XML_ATTRS.finditer(raw_output):
        raw_name = m.group(1)
        raw_args = m.group(2) or m.group(3) or ""
        match = _match_tool_name(raw_name, valid_names)
        if match:
            args = _parse_json_obj(raw_args) or _kwargs_to_dict(raw_args)
            candidates.append(ToolCall(name=match[0], args=args, confidence=match[1] * 0.9))

    # Strategy 4: XML-style <tool>...</tool> with JSON body
    for m in _RE_XML_TOOL.finditer(raw_output):
        body = m.group(1).strip()
        obj = _parse_json_obj(body)
        if obj:
            tc = _extract_tool_call_from_json(obj, valid_names)
            if tc:
                tc.confidence *= 0.9
                candidates.append(tc)

    # Strategy 5: Function-call syntax: use tool_name(key=val, ...)
    for m in _RE_KWARGS.finditer(raw_output):
        raw_name = m.group(1)
        raw_args = m.group(2)
        match = _match_tool_name(raw_name, valid_names)
        if match:
            args = _kwargs_to_dict(raw_args)
            candidates.append(ToolCall(name=match[0], args=args, confidence=match[1] * 0.8))

    # Strategy 6: Prose detection — "I'll use read_file with path=..."
    for m in _RE_PROSE_TOOL.finditer(raw_output):
        raw_name = m.group(1)
        raw_context = m.group(2) or ""
        match = _match_tool_name(raw_name, valid_names)
        if match:
            args = _kwargs_to_dict(raw_context) if raw_context else {}
            candidates.append(ToolCall(name=match[0], args=args, confidence=match[1] * 0.6))

    # Strategy 7: Bare tool name in code block — ```tool_name``` or ```tool_name {}```
    _bare_block = re.search(r"```\s*(\w+)\s*(\{[^`]*\})?\s*```", raw_output)
    if _bare_block:
        raw_name = _bare_block.group(1)
        raw_args_str = _bare_block.group(2) or ""
        match = _match_tool_name(raw_name, valid_names)
        if match:
            args = _parse_json_obj(raw_args_str) if raw_args_str.strip() else {}
            candidates.append(ToolCall(name=match[0], args=args or {}, confidence=match[1] * 0.7))

    # Strategy 8: Bare tool name as entire response (model just outputs the tool name)
    stripped = raw_output.strip().strip("`").strip()
    if stripped and " " not in stripped and len(stripped) < 40:
        match = _match_tool_name(stripped, valid_names)
        if match and match[1] > 0.8:
            candidates.append(ToolCall(name=match[0], args={}, confidence=match[1] * 0.65))

    if not candidates:
        return None
    candidates.sort(key=lambda c: c.confidence, reverse=True)
    return candidates[0]


def normalize_answer(text: str) -> str:
    """Strip common preamble/suffix fluff from LLM answers."""
    text = text.strip()
    text = _RE_ANSWER_PREAMBLE.sub("", text, count=1)
    text = _RE_ANSWER_SUFFIX.sub("", text)
    # Remove "The answer is:" pattern
    text = re.sub(r"^(?:The answer is|Answer):\s*", "", text, flags=re.IGNORECASE)
    return text.strip()


# ═══════════════════════════════════════════════════════════════════════════════
# H4 – TRAJECTORY REGULATION: State tracking & failure detection
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class TrajectoryState:
    """Tracks agent execution trajectory for failure detection."""
    tool_calls: deque = field(default_factory=lambda: deque(maxlen=TRAJECTORY_WINDOW))
    outputs: deque = field(default_factory=lambda: deque(maxlen=TRAJECTORY_WINDOW))
    error_streak: int = 0
    empty_streak: int = 0
    loop_streak: int = 0
    total_rounds: int = 0
    best_candidate: Optional[str] = None  # Best answer seen so far

    def record(self, tool_call: Optional[ToolCall], output: Optional[str], is_error: bool = False):
        """Record a tool round result."""
        self.total_rounds += 1
        if tool_call:
            self.tool_calls.append(tool_call.signature())
        if output is not None:
            self.outputs.append(output)

        # Update streaks
        if is_error:
            self.error_streak += 1
            self.empty_streak = 0
        elif not output or not output.strip():
            self.empty_streak += 1
            self.error_streak = 0
        else:
            self.error_streak = 0
            self.empty_streak = 0
            # Track best candidate (longest meaningful output)
            if self.best_candidate is None or (
                len(output.strip()) > len(self.best_candidate)
                and len(output.strip()) > 20
            ):
                self.best_candidate = output.strip()

        # Detect loops
        if len(self.tool_calls) >= 3:
            recent = list(self.tool_calls)
            if recent[-1] == recent[-2] == recent[-3]:
                self.loop_streak += 1
            else:
                self.loop_streak = 0
        else:
            self.loop_streak = 0

    def detect_pattern(self) -> Optional[str]:
        """Detect failure patterns. Returns pattern name or None."""
        if self.loop_streak >= 1:
            return "repeated_tool"
        if self.empty_streak >= 2:
            return "empty_result"
        if self.error_streak >= 2:
            return "error_pattern"
        # Stalled: 4 rounds with no meaningful progress
        if self.total_rounds >= 4 and self.best_candidate is None:
            return "stalled"
        return None

    def should_force_output(self) -> bool:
        """Budget forcing: enough rounds with a valid candidate."""
        return self.total_rounds >= BUDGET_FORCE_ROUNDS and self.best_candidate is not None

    def reset(self):
        """Reset state for a new user turn."""
        self.tool_calls.clear()
        self.outputs.clear()
        self.error_streak = 0
        self.empty_streak = 0
        self.loop_streak = 0
        self.total_rounds = 0
        self.best_candidate = None


_RECOVERY_HINTS: Dict[str, str] = {
    "repeated_tool": (
        "STOP: You are repeating the same tool call. The previous result is already available. "
        "Use the information you already have to answer the user's question directly."
    ),
    "empty_result": (
        "The last tool calls returned empty results. Try a different approach: "
        "use a different tool, adjust your parameters, or answer with what you know."
    ),
    "error_pattern": (
        "Multiple tool errors detected. Check: Is the tool name correct? Are the arguments valid? "
        "Try a simpler call or a different tool entirely."
    ),
    "stalled": (
        "You have used several tool rounds without making progress. "
        "Step back and answer the user directly with what you know, or try a completely different strategy."
    ),
}


# ═══════════════════════════════════════════════════════════════════════════════
# H3 – TASK CONTEXT: Classification & hint generation
# ═══════════════════════════════════════════════════════════════════════════════

def classify_task(user_message: str) -> str:
    """Classify user message into a task type. Returns best-matching type."""
    if not user_message:
        return "knowledge_query"

    msg_lower = user_message.lower()
    scores: Dict[str, int] = {}

    for task_type, config in TASK_TYPES.items():
        score = 0
        for kw in config["keywords"]:
            if kw in msg_lower:
                # Longer keywords get more weight (more specific)
                score += len(kw.split())
        scores[task_type] = score

    best_type = max(scores, key=scores.get)  # type: ignore[arg-type]
    if scores[best_type] == 0:
        return "knowledge_query"  # Default fallback
    return best_type


def get_tool_hints(task_type: str) -> List[str]:
    """Get relevant tool names for a task type."""
    config = TASK_TYPES.get(task_type)
    if not config:
        return TOOL_NAMES[:5]
    return config["tools"]


def build_tool_hint_prompt(task_type: str, tools: List[str]) -> str:
    """Build a concise hint string for system prompt injection."""
    hint = (
        f"[Task type: {task_type}] "
        f"Most relevant tools for this request: {', '.join(tools)}. "
        f"Prefer these tools. Only use others if these cannot accomplish the task."
    )
    if task_type in ("creative_writing", "knowledge_query"):
        hint += (
            " IMPORTANT: Respond directly in chat. Do NOT use write_file or any file tool. "
            "The user wants to see the content here, not saved to a file."
        )
    return hint


# ═══════════════════════════════════════════════════════════════════════════════
# H5 – PROCEDURAL SKILLS: Retrieval
# ═══════════════════════════════════════════════════════════════════════════════

def retrieve_skills(
    user_message: str,
    task_type: str,
    top_k: int = 2,
    failure_mode: bool = False,
) -> List[Skill]:
    """
    Retrieve relevant procedural skills by keyword matching + task type affinity.
    Returns top_k skills sorted by relevance score.
    """
    if not user_message:
        return []

    msg_lower = user_message.lower()
    scored: List[Tuple[float, Skill]] = []

    for skill in SKILLS:
        score = 0.0
        # Keyword match
        for kw in skill.trigger_keywords:
            if kw in msg_lower:
                score += 2.0 * len(kw.split())
        # Task type affinity bonus
        if skill.task_type == task_type:
            score += 1.5
        # Only include if some relevance
        if score > 0:
            scored.append((score, skill))

    scored.sort(key=lambda x: x[0], reverse=True)

    # In failure mode, return more skills
    limit = top_k + 1 if failure_mode else top_k
    return [s for _, s in scored[:limit]]


def build_skill_prompt(skills: List[Skill]) -> str:
    """Format retrieved skills as a prompt injection string."""
    if not skills:
        return ""
    lines = ["[Procedural hints]"]
    for sk in skills:
        lines.append(f"  - {sk.hint}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN HARNESS CLASS
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class HarnessConfig:
    """Toggle individual harness layers on/off."""
    enable_action_gate: bool = True       # H2
    enable_task_context: bool = True      # H3
    enable_trajectory: bool = True        # H4
    enable_skills: bool = True            # H5
    # Tuning
    similarity_threshold: float = SIMILARITY_THRESHOLD
    dedup_window: int = DEDUP_WINDOW
    budget_force_rounds: int = BUDGET_FORCE_ROUNDS
    valid_tool_names: List[str] = field(default_factory=lambda: list(TOOL_NAMES))


class Harness:
    """
    Life-Harness: Runtime adaptation layer for small-LLM agents.

    Usage:
        harness = Harness()

        # At start of user turn
        hints = harness.prepare_turn(user_message)
        # -> inject hints["system_injection"] into the system prompt

        # After each LLM response
        result = harness.process_output(raw_llm_output)
        # result.tool_call: extracted ToolCall or None
        # result.force_answer: str if budget-forcing, else None
        # result.recovery_hint: str if failure detected, else None

        # After tool execution
        harness.record_tool_result(tool_call, output, is_error)
    """

    def __init__(self, config: Optional[HarnessConfig] = None):
        self.config = config or HarnessConfig()
        self._trajectory = TrajectoryState()
        self._dedup_history: deque = deque(maxlen=self.config.dedup_window)
        self._dedup_outputs: Dict[str, str] = {}
        self._current_task_type: str = "knowledge_query"
        self._current_user_message: str = ""

    # ─── Public API ───────────────────────────────────────────────────────────

    def prepare_turn(self, user_message: str) -> Dict[str, str]:
        """
        Prepare harness for a new user turn.
        Returns dict with optional prompt injections.
        Call at the START of each new user message.
        """
        self._trajectory.reset()
        self._dedup_history.clear()
        self._dedup_outputs.clear()
        self._current_user_message = user_message

        injections: Dict[str, str] = {}
        parts: List[str] = []

        # H3: Task classification & tool hints
        if self.config.enable_task_context:
            self._current_task_type = classify_task(user_message)
            tools = get_tool_hints(self._current_task_type)
            parts.append(build_tool_hint_prompt(self._current_task_type, tools))

        # H5: Cold-start skill (top-1 from built-in library)
        if self.config.enable_skills:
            skills = retrieve_skills(user_message, self._current_task_type, top_k=1)
            skill_prompt = build_skill_prompt(skills)
            if skill_prompt:
                parts.append(skill_prompt)

            # H5+: Inject learned skills from auto-extraction (Hermes-style)
            try:
                from app.services.auto_skill import get_learned_skills
                learned = get_learned_skills(user_message, top_k=1)
                if learned:
                    hints = [f"  - Learned pattern: {s['hint']}" for s in learned]
                    parts.append("[Learned from previous sessions]\n" + "\n".join(hints))
            except Exception:
                pass

        if parts:
            injections["system_injection"] = "\n".join(parts)

        return injections

    def process_output(self, raw_output: str) -> "HarnessResult":
        """
        Process raw LLM output through the harness layers.
        Returns a HarnessResult with extracted tool call, forced answer, or recovery hints.
        """
        result = HarnessResult()

        if not raw_output or not raw_output.strip():
            result.raw = ""
            return result

        result.raw = raw_output

        # H4: Check budget forcing first
        if self.config.enable_trajectory and self._trajectory.should_force_output():
            result.force_answer = normalize_answer(self._trajectory.best_candidate or raw_output)
            return result

        # H2: Try to extract a tool call
        tool_call: Optional[ToolCall] = None
        if self.config.enable_action_gate:
            tool_call = extract_tool_call(raw_output, self.config.valid_tool_names)

            # Deduplication check
            if tool_call:
                sig = tool_call.signature()
                repeat_count = sum(1 for s in self._dedup_history if s == sig)
                if repeat_count >= DEDUP_REPEAT_LIMIT and sig in self._dedup_outputs:
                    # Force the cached output instead of re-calling
                    result.force_answer = self._dedup_outputs[sig]
                    result.dedup_triggered = True
                    return result

        result.tool_call = tool_call

        # If no tool call found, normalize the answer
        if not tool_call and self.config.enable_action_gate:
            result.normalized_answer = normalize_answer(raw_output)

        # H4: Check for failure patterns and generate recovery hints
        if self.config.enable_trajectory:
            pattern = self._trajectory.detect_pattern()
            if pattern:
                recovery = _RECOVERY_HINTS.get(pattern, "")
                # H5: Add extra skills during failure
                if self.config.enable_skills:
                    skills = retrieve_skills(
                        self._current_user_message,
                        self._current_task_type,
                        top_k=2,
                        failure_mode=True,
                    )
                    skill_prompt = build_skill_prompt(skills)
                    if skill_prompt:
                        recovery = recovery + "\n" + skill_prompt
                result.recovery_hint = recovery
                result.failure_pattern = pattern

        return result

    def record_tool_result(
        self,
        tool_call: Optional[ToolCall],
        output: Optional[str],
        is_error: bool = False,
    ):
        """
        Record a tool execution result. Call AFTER each tool is executed.
        Updates trajectory state and dedup cache.
        """
        # H4: Trajectory tracking
        if self.config.enable_trajectory:
            self._trajectory.record(tool_call, output, is_error)

        # H2: Dedup cache
        if self.config.enable_action_gate and tool_call:
            sig = tool_call.signature()
            self._dedup_history.append(sig)
            if output and output.strip() and not is_error:
                self._dedup_outputs[sig] = output.strip()

    def get_step_injection(self) -> Optional[str]:
        """
        Get any per-step injection (recovery hints, skill hints) for the next prompt.
        Call BEFORE sending the next prompt to the LLM within a multi-step turn.
        """
        parts: List[str] = []

        # H4: Recovery hint based on trajectory
        if self.config.enable_trajectory:
            pattern = self._trajectory.detect_pattern()
            if pattern:
                hint = _RECOVERY_HINTS.get(pattern, "")
                if hint:
                    parts.append(hint)

                # H5: Inject skills during failure
                if self.config.enable_skills:
                    skills = retrieve_skills(
                        self._current_user_message,
                        self._current_task_type,
                        top_k=2,
                        failure_mode=True,
                    )
                    skill_prompt = build_skill_prompt(skills)
                    if skill_prompt:
                        parts.append(skill_prompt)

        return "\n".join(parts) if parts else None

    @property
    def task_type(self) -> str:
        """Current classified task type."""
        return self._current_task_type

    @property
    def trajectory(self) -> TrajectoryState:
        """Access to trajectory state (read-only recommended)."""
        return self._trajectory

    @property
    def rounds(self) -> int:
        """Number of tool rounds executed in current turn."""
        return self._trajectory.total_rounds


@dataclass
class HarnessResult:
    """Result from processing LLM output through the harness."""
    raw: str = ""
    tool_call: Optional[ToolCall] = None
    normalized_answer: Optional[str] = None
    force_answer: Optional[str] = None
    recovery_hint: Optional[str] = None
    failure_pattern: Optional[str] = None
    dedup_triggered: bool = False

    @property
    def has_tool_call(self) -> bool:
        return self.tool_call is not None

    @property
    def should_force(self) -> bool:
        return self.force_answer is not None

    @property
    def has_recovery(self) -> bool:
        return self.recovery_hint is not None

    @property
    def final_text(self) -> Optional[str]:
        """The best text answer to return to the user, if available."""
        if self.force_answer:
            return self.force_answer
        if self.normalized_answer:
            return self.normalized_answer
        return None
