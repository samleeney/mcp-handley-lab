---
name: recursive-llm
description: Process documents too large for context using REPL + sub-LLM calls
allowed-tools: []
---

# Recursive LLM Pattern

Process documents that exceed context limits using a REPL environment with sub-LLM calls. Based on the [Recursive Language Models](https://arxiv.org/abs/2512.24601) (RLM) paper.

## Key Insight

The prompt is loaded as a **variable in the REPL environment**, not fed directly to the LLM. The LLM then writes code to symbolically interact with, decompose, and recursively process the content. This lets it handle inputs orders of magnitude beyond its context window.

## When to Use

- Processing very large files (multi-MB text, code repos, >100K tokens)
- Aggregating information from many documents
- Tasks requiring dense access to long content (every line matters)
- When direct context would cause quality degradation

**When NOT to use**: For small inputs (<50K tokens), direct LLM calls are faster and slightly more accurate. The RLM pattern adds overhead that only pays off at scale.

## Prerequisites

- `mcp_handley_lab` must be installed in the Python environment
- API keys configured for desired providers (GEMINI_API_KEY, OPENAI_API_KEY, etc.)

## Pattern

1. **Create Python REPL session**
   ```
   mcp__repl__session(action="create", backend="python")
   ```

2. **Import llm module and survey the context**
   ```python
   from mcp_handley_lab import llm
   from pathlib import Path

   context_path = Path("/path/to/large/document.txt")
   context_size = context_path.stat().st_size
   print(f"Context: {context_size:,} bytes (~{context_size // 4:,} tokens)")

   # Peek at the structure before choosing a chunking strategy
   with open(context_path) as f:
       head = f.read(2000)
   print(head)
   ```

3. **Process in chunks with stateless sub-LLM calls**
   ```python
   results = []
   chunk_size = 100_000  # bytes (~25K tokens); adjust based on measured token usage

   with open(context_path, 'rb') as f:
       offset = 0
       while chunk := f.read(chunk_size):
           text = chunk.decode('utf-8', errors='replace')
           # branch="" makes call stateless (no memory accumulation)
           result = llm.chat(f'''Extract key information from this document chunk.
   <document_chunk offset="{offset}">
   {text}
   </document_chunk>
   Summarize the key facts found in this chunk only.''', branch="")
           results.append({"offset": offset, "summary": result.content})
           offset += len(chunk)

   # Aggregate results
   summaries = "\n".join(f"[{r['offset']}]: {r['summary']}" for r in results)
   final = llm.chat(f"Synthesize these chunk summaries:\n{summaries}", branch="")
   print(final.content)
   ```

## Advanced Patterns

### Filter Before Calling LLMs

Use code to narrow the search space before spending tokens on sub-calls:

```python
import re
# Use regex/string ops to find relevant sections first
with open(context_path) as f:
    content = f.read()
matches = [m.start() for m in re.finditer(r'keyword|pattern', content)]
print(f"Found {len(matches)} relevant sections")

# Only send relevant sections to sub-LLMs
for start in matches:
    excerpt = content[max(0, start-500):start+2000]
    result = llm.chat(f"Analyze this excerpt:\n{excerpt}", branch="")
    results.append(result.content)
```

### Recursive Sub-Calls (True RLM Depth)

Sub-LLM calls can themselves spawn further sub-calls. Use this when a single level of chunking isn't enough — e.g., each chunk still exceeds context or requires its own decomposition:

```python
def process_section(text, depth=0, max_depth=2):
    """Recursively process text, spawning sub-calls if too large."""
    if len(text) < 100_000 or depth >= max_depth:
        result = llm.chat(f"Analyze:\n{text}", branch="")
        return result.content

    # Chunk and recurse
    mid = len(text) // 2
    left = process_section(text[:mid], depth + 1, max_depth)
    right = process_section(text[mid:], depth + 1, max_depth)
    merged = llm.chat(f"Merge these analyses:\n1: {left}\n2: {right}", branch="")
    return merged.content
```

### Build Output Through REPL Variables

For tasks producing long output (e.g., transforming every line), accumulate results in REPL variables rather than trying to generate everything in one LLM call:

```python
output_parts = []
for i, section in enumerate(sections):
    transformed = llm.chat(f"Transform this section:\n{section}", branch="")
    output_parts.append(transformed.content)
    print(f"Processed {i+1}/{len(sections)}")

# Stitch together — the final output can exceed any single LLM's output limit
final_output = "\n\n".join(output_parts)
Path("/tmp/result.txt").write_text(final_output)
print(f"Written {len(final_output):,} chars to /tmp/result.txt")
```

### Answer Verification

Use a separate sub-call to verify answers, avoiding context rot in the main reasoning chain:

```python
# After computing an answer from chunks
answer = llm.chat(f"Based on these summaries, answer: {query}\n{summaries}", branch="")

# Verify with targeted evidence
verification = llm.chat(
    f"Verify this answer: '{answer.content}'\nUsing this evidence:\n{relevant_excerpt}",
    branch=""
)
print(f"Answer: {answer.content}\nVerification: {verification.content}")
```

## Batching Guidance

**Batch aggressively** — each `llm.chat()` call has fixed overhead (API latency, token preamble). Aim for ~100-200K characters per sub-call rather than many small calls.

```python
# BAD: 1000 individual calls (slow, expensive)
for line in lines:
    result = llm.chat(f"Classify: {line}", branch="")

# GOOD: batch into ~10 calls of 100 lines each
batch_size = 100
for i in range(0, len(lines), batch_size):
    batch = "\n".join(lines[i:i+batch_size])
    result = llm.chat(f"Classify each line:\n{batch}", branch="")
```

## API Reference

```python
from mcp_handley_lab import llm

result = llm.chat(
    prompt: str,                    # The question/task
    branch: str = "session",        # Conversation branch ("" or "false" for stateless)
    model: str = "gemini",          # Provider or model name
    system_prompt: str | None = None,  # System instructions
    temperature: float = 1.0,
    files: list[str] | None = None, # Paths to pass as context
    options: dict = None,           # Provider-specific (grounding, reasoning_effort, etc.)
)

# Result fields (LLMResult):
result.content                # Response text
result.usage.input_tokens     # Tokens used for input
result.usage.output_tokens    # Tokens used for output
result.usage.cost             # Cost in USD
result.usage.model_used       # Canonical model ID
result.branch                 # Branch name (empty if stateless)
result.commit_sha             # Git commit (None if stateless)
```

Available if configured in models.yaml with API keys set:
gemini, openai, claude, mistral, grok, groq

## Tips

- Use `branch=""` for stateless sub-calls (no memory accumulation)
- Use `branch="session"` (default) for multi-turn conversations
- Use `model="gemini"` for large context windows in sub-calls
- Use delimiters (`<document>...</document>`) to prevent prompt injection
- Include byte offsets in prompts to preserve provenance
- Cache repeated queries in Python variables
- Use regex/string ops for pattern matching (faster than LLM)
- Monitor token usage via `result.usage` to tune chunk sizes
- Survey the context structure (peek at head, count lines/sections) before choosing a chunking strategy — structure-aware chunking (by headers, paragraphs, functions) beats fixed-size byte chunking
