"""Deterministic comment removal for generated code.

The developer and debugger write the files that land in the user's
project, and the product rule is that generated code carries NO
comments. The prompts ask the model for comment-free code, but a
small local model cannot be trusted to comply, so every file those
two paths write passes through strip_comments() first.

The strippers are per-language and conservative:

- Python: the tokenize module drops COMMENT tokens (a "#" inside a
  string is part of a STRING token and survives). The result must
  still compile and must keep the exact same string literals,
  otherwise the original content is returned.
- HTML: "<!-- ... -->" spans are removed by a small scanner that
  respects quoted attribute values.
- CSS / JavaScript: a small scanner removes /* ... */ (and, for
  JavaScript, // ...) while respecting single, double and backtick
  strings (and, for JavaScript, regex literals). The sequence of
  string literals before and after stripping must be identical,
  otherwise the original content is returned.

Any exception anywhere falls back to the original content: a file
with comments is a cosmetic issue, a broken file is a real one.
"""

import io
import tokenize

__all__ = ["strip_comments"]


# ----------------------------------------------------------------------
# Python
# ----------------------------------------------------------------------

def _string_token_texts(source: str) -> list[str]:
    texts = []

    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.STRING:
            texts.append(tok.string)
        elif tok.type == getattr(tokenize, "FSTRING_MIDDLE", -1):
            texts.append(tok.string)

    return texts


def _strip_python(content: str) -> str:
    kept = []

    for tok in tokenize.generate_tokens(io.StringIO(content).readline):
        if tok.type == tokenize.COMMENT:
            # Also drops the shebang line: it is a COMMENT token.
            continue
        kept.append(tok)

    stripped = tokenize.untokenize(kept)

    # Never write code that no longer parses, and never let the
    # rebuild mangle a string literal.
    compile(stripped, "<generated>", "exec")

    if _string_token_texts(stripped) != _string_token_texts(content):
        return content

    # untokenize pads each line out to the original column positions,
    # so a removed comment leaves a tail of spaces behind. Trim the
    # trailing whitespace line by line, but never inside a string
    # literal: the kept tokens still carry their (row, col) spans in
    # the stripped text, so the protected end of each line is known.
    protected_end: dict[int, int] = {}

    for tok in kept:
        if "STRING" in tokenize.tok_name[tok.type]:
            (srow, scol), (erow, ecol) = tok.start, tok.end
            for row in range(srow, erow + 1):
                end = ecol if row == erow else 1 << 30
                protected_end[row] = max(protected_end.get(row, 0), end)

    lines = stripped.split("\n")
    cleaned = []
    previous_blank = False

    for number, line in enumerate(lines, start=1):
        limit = protected_end.get(number, 0)
        line = line[:limit] + line[limit:].rstrip()
        # A removed full-line comment leaves a blank line behind;
        # collapse runs of those into one blank line. A blank line
        # inside a string literal has a protected span and is kept
        # exactly as it is.
        if not line and limit == 0:
            if previous_blank:
                continue
            previous_blank = True
        else:
            previous_blank = False
        cleaned.append(line)

    while cleaned and not cleaned[0].strip():
        cleaned.pop(0)
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()

    if not cleaned:
        return ""

    return "\n".join(cleaned) + "\n"


# ----------------------------------------------------------------------
# HTML
# ----------------------------------------------------------------------

def _strip_html(content: str) -> str:
    out = []
    i = 0
    n = len(content)
    in_tag = False
    quote = ""

    while i < n:
        if not in_tag and content.startswith("<!--", i):
            end = content.find("-->", i + 4)
            if end == -1:
                # Unterminated comment: browsers treat the rest of
                # the document as the comment too.
                break
            i = end + 3
            continue

        char = content[i]

        if in_tag:
            if quote:
                if char == quote:
                    quote = ""
            elif char in "\"'":
                quote = char
            elif char == ">":
                in_tag = False
        elif char == "<":
            in_tag = True

        out.append(char)
        i += 1

    return "".join(out)


# ----------------------------------------------------------------------
# CSS / JavaScript (C-style comments, string aware)
# ----------------------------------------------------------------------

def _scan_c_like(content: str, *, line_comments: bool, regex_literals: bool):
    """One pass over CSS/JS source.

    Returns (stripped_text, string_literals). String literals are
    collected in order from BOTH the original (comments included in
    the scan but never inside a string) so the caller can verify the
    strip did not change any literal.
    """

    out = []
    literals = []
    i = 0
    n = len(content)
    prev_significant = ""  # last non-space char emitted, for regex detection

    while i < n:
        char = content[i]
        nxt = content[i + 1] if i + 1 < n else ""

        # Comments -------------------------------------------------
        if char == "/" and nxt == "*":
            end = content.find("*/", i + 2)
            i = n if end == -1 else end + 2
            # A space, not nothing: "a/**/b" must not become "ab".
            out.append(" ")
            continue

        if line_comments and char == "/" and nxt == "/":
            end = content.find("\n", i + 2)
            i = n if end == -1 else end  # keep the newline itself
            continue

        # Strings --------------------------------------------------
        if char in "\"'`":
            quote = char
            j = i + 1
            while j < n:
                current = content[j]
                if current == "\\":
                    j += 2
                    continue
                if quote == "`" and current == "$" and j + 1 < n and content[j + 1] == "{":
                    # Template expression: skip the balanced block;
                    # its contents are code, but for literal-compare
                    # purposes the whole template is one literal.
                    depth = 1
                    j += 2
                    while j < n and depth:
                        if content[j] == "{":
                            depth += 1
                        elif content[j] == "}":
                            depth -= 1
                        j += 1
                    continue
                if current == quote:
                    j += 1
                    break
                if quote != "`" and current == "\n":
                    break  # unterminated string: stop the literal here
                j += 1
            literal = content[i:j]
            literals.append(literal)
            out.append(literal)
            prev_significant = quote
            i = j
            continue

        # Regex literals (JavaScript only) ------------------------
        if (
            regex_literals
            and char == "/"
            and prev_significant in ("", "(", ",", "=", ":", "[", "!", "&", "|", "?", "{", "}", ";", "+", "-", "*", "%", "<", ">")
        ):
            j = i + 1
            in_class = False
            while j < n:
                current = content[j]
                if current == "\\":
                    j += 2
                    continue
                if current == "[":
                    in_class = True
                elif current == "]":
                    in_class = False
                elif current == "/" and not in_class:
                    j += 1
                    break
                elif current == "\n":
                    break
                j += 1
            literal = content[i:j]
            literals.append(literal)
            out.append(literal)
            prev_significant = "/"
            i = j
            continue

        out.append(char)
        if not char.isspace():
            prev_significant = char
        i += 1

    return "".join(out), literals


def _strip_c_like(content: str, *, line_comments: bool, regex_literals: bool) -> str:
    stripped, _ = _scan_c_like(
        content, line_comments=line_comments, regex_literals=regex_literals
    )
    _, original_literals = _scan_c_like(
        content, line_comments=line_comments, regex_literals=regex_literals
    )
    # Safety invariant: re-scanning the stripped text must find the
    # exact same string literals as the original had, and stripping
    # must be a fixed point. If either fails, the scanner misread
    # something (an unhandled string or regex shape) and the
    # original content is safer than a guess.
    rescanned, rescan_literals = _scan_c_like(
        stripped, line_comments=line_comments, regex_literals=regex_literals
    )
    if rescan_literals != original_literals or rescanned != stripped:
        return content

    return stripped


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

def strip_comments(path: str, content: str) -> str:
    """Return `content` with comments removed, or the original on doubt.

    Dispatch is by file extension; unknown types pass through
    unchanged. Any failure returns the original content: never write
    broken code to satisfy a cosmetic rule.
    """

    if not isinstance(content, str) or not content:
        return content

    lowered = str(path).lower()

    try:
        if lowered.endswith(".py"):
            return _strip_python(content)
        if lowered.endswith((".html", ".htm")):
            return _strip_html(content)
        if lowered.endswith(".css"):
            return _strip_c_like(content, line_comments=False, regex_literals=False)
        if lowered.endswith((".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx")):
            return _strip_c_like(content, line_comments=True, regex_literals=True)
    except Exception:
        return content

    return content
