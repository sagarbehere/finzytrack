"""A small shell lexer for the guard hooks (BL-0042): it reads a command line the way bash splits it, so the
guard can tell text the shell executes from text it only passes along.

`lex(command)` returns the simple commands ("segments") in order. Each segment has its words with quotes
removed, the bodies of its heredocs (and whether the delimiter was quoted, which makes the body literal), the
command substitutions found in it outside single quotes (`$(…)`, backticks, `<(…)`, `>(…)`, and those inside an
unquoted heredoc body), and whether its output is piped on. It knows quoting (`'…'`, `"…"`, `$'…'`, backslash),
comments, the operators `; & | && || |& ( )` and newlines, redirections and heredocs (`<<`, `<<-`).

What it cannot decide (an unterminated quote, substitution or heredoc) raises `LexError`, and the guard
refuses the command: a parser that guesses would let something through (INV-22's spirit). It is not a full
shell grammar; the guard stays a safety net, not a sandbox. Stdlib only: the hooks run on the system Python.
"""

from __future__ import annotations

from dataclasses import dataclass, field

REDIRECTS = ("<<<", "<<-", "<<", ">>", ">&", "<&", "&>>", "&>", ">|", "<>", ">", "<")


class LexError(Exception):
    pass


@dataclass
class Heredoc:
    body: str
    quoted: bool


@dataclass
class Segment:
    words: list[str] = field(default_factory=list)
    heredocs: list[Heredoc] = field(default_factory=list)
    subs: list[str] = field(default_factory=list)
    piped: bool = False
    procsub: bool = False


class _Lexer:
    def __init__(self, s: str) -> None:
        self.s = s
        self.segments: list[Segment] = []
        self.seg = Segment()
        self.word: list[str] = []
        self.in_word = False
        self.pending: list[tuple[str, bool, bool, Segment]] = []  # delimiter, quoted, strip tabs, owner

    # --- words and segments
    def end_word(self) -> None:
        if self.in_word:
            self.seg.words.append("".join(self.word))
        self.word, self.in_word = [], False

    def end_segment(self, piped: bool = False) -> None:
        self.end_word()
        self.seg.piped = piped
        if self.seg.words or self.seg.heredocs or self.seg.subs:
            self.segments.append(self.seg)
        self.seg = Segment()

    def add(self, text: str) -> None:
        self.word.append(text)
        self.in_word = True

    # --- nested parts
    def substitution(self, i: int) -> int:
        """`s[i]` is just after `$(`, `<(` or `>(`; returns the index after the matching `)`."""
        inner = _Lexer(self.s)
        end = inner.run(i, stop=True)
        self.seg.subs.append(self.s[i:end - 1])
        return end

    def backtick(self, i: int) -> int:
        """`s[i]` is just after a backtick; returns the index after the closing one."""
        j = i
        while j < len(self.s):
            if self.s[j] == "\\":
                j += 2
                continue
            if self.s[j] == "`":
                self.seg.subs.append(self.s[i:j].replace("\\`", "`"))
                return j + 1
            j += 1
        raise LexError("unterminated backtick")

    def double_quoted(self, i: int) -> int:
        """`s[i]` is just after `"`; adds the cooked text, records substitutions, returns the index after `"`."""
        s = self.s
        while i < len(s):
            c = s[i]
            if c == "\\" and i + 1 < len(s):
                if s[i + 1] in '$`"\\':
                    self.add(s[i + 1])
                elif s[i + 1] != "\n":
                    self.add(c + s[i + 1])
                i += 2
            elif c == '"':
                self.in_word = True
                return i + 1
            elif s.startswith("$(", i):
                end = self.substitution(i + 2)
                self.add(s[i:end])
                i = end
            elif c == "`":
                end = self.backtick(i + 1)
                self.add(s[i:end])
                i = end
            else:
                self.add(c)
                i += 1
        raise LexError("unterminated double quote")

    def heredoc_bodies(self, i: int) -> int:
        """`s[i]` starts the line after a command line with heredocs; reads their bodies in order."""
        s = self.s
        for delim, quoted, strip, owner in self.pending:
            lines = []
            while True:
                if i >= len(s):
                    raise LexError(f"unterminated heredoc {delim!r}")
                nl = s.find("\n", i)
                line = s[i:] if nl < 0 else s[i:nl]
                i = len(s) if nl < 0 else nl + 1
                test = line.lstrip("\t") if strip else line
                if test == delim:
                    break
                lines.append(test)
            body = "\n".join(lines)
            owner.heredocs.append(Heredoc(body, quoted))
            if not quoted:  # the shell expands $(…) and backticks in the body
                scan = _Lexer(body)
                scan.seg = Segment()
                scan.double_quoted_body()
                owner.subs += scan.seg.subs
        self.pending = []
        return i

    def double_quoted_body(self) -> None:
        """Substitutions in an unquoted heredoc body: quoting does not apply there, `$(` and backticks do."""
        s, i = self.s, 0
        while i < len(s):
            if s[i] == "\\":
                i += 2
            elif s.startswith("$(", i):
                i = self.substitution(i + 2)
            elif s[i] == "`":
                i = self.backtick(i + 1)
            else:
                i += 1

    def heredoc_start(self, i: int, strip: bool) -> int:
        """`s[i]` is just after `<<` or `<<-`: reads the delimiter word and registers the heredoc."""
        s = self.s
        while i < len(s) and s[i] in " \t":
            i += 1
        delim, quoted = [], False
        while i < len(s) and s[i] not in " \t\n;&|()<>":
            c = s[i]
            if c in "'\"":
                quoted = True
                end = s.find(c, i + 1)
                if end < 0:
                    raise LexError("unterminated heredoc delimiter")
                delim.append(s[i + 1:end])
                i = end + 1
            elif c == "\\":
                quoted = True
                delim.append(s[i + 1:i + 2])
                i += 2
            else:
                delim.append(c)
                i += 1
        if not delim:
            raise LexError("heredoc without a delimiter")
        self.pending.append(("".join(delim), quoted, strip, self.seg))
        return i

    # --- the main loop
    def run(self, i: int = 0, stop: bool = False) -> int:
        """Lex from `i`; with `stop`, return the index after the `)` that closes a substitution."""
        s, depth = self.s, 0
        while i < len(s):
            c = s[i]
            if c == "\n":
                self.end_segment()
                i = self.heredoc_bodies(i + 1) if self.pending else i + 1
            elif c in " \t":
                self.end_word()
                i += 1
            elif c == "#" and not self.in_word:
                nl = s.find("\n", i)
                i = len(s) if nl < 0 else nl
            elif c == "\\":
                if s[i + 1:i + 2] == "\n":
                    i += 2
                else:
                    self.add(s[i + 1:i + 2])
                    i += 2
            elif c == "'":
                end = s.find("'", i + 1)
                if end < 0:
                    raise LexError("unterminated single quote")
                self.add(s[i + 1:end])
                self.in_word = True
                i = end + 1
            elif s.startswith("$'", i):
                j = i + 2
                while j < len(s) and s[j] != "'":
                    j += 2 if s[j] == "\\" else 1
                if j >= len(s):
                    raise LexError("unterminated $'…' quote")
                self.add(s[i + 2:j])
                i = j + 1
            elif c == '"':
                i = self.double_quoted(i + 1)
            elif s.startswith("$(", i):
                end = self.substitution(i + 2)
                self.add(s[i:end])
                i = end
            elif c == "`":
                end = self.backtick(i + 1)
                self.add(s[i:end])
                i = end
            elif s.startswith(("<(", ">("), i):
                self.end_word()
                self.seg.procsub = True
                i = self.substitution(i + 2)
            elif c in "<>" or (c == "&" and s[i + 1:i + 2] == ">"):
                op = next(o for o in REDIRECTS if s.startswith(o, i))
                if self.in_word and "".join(self.word).isdigit():  # 2> and the like: the fd joins the operator
                    op_word = "".join(self.word) + op
                    self.word, self.in_word = [], False
                else:
                    self.end_word()
                    op_word = op
                self.seg.words.append(op_word)
                i += len(op)
                if op in ("<<", "<<-"):
                    i = self.heredoc_start(i, strip=op == "<<-")
            elif c in ";&|()":
                if c == "(" and stop:
                    depth += 1
                if c == ")" and stop:
                    if depth == 0:
                        self.end_segment()
                        if self.pending:
                            raise LexError("heredoc not closed before ')'")
                        return i + 1
                    depth -= 1
                two = s[i:i + 2]
                piped = c == "|" and two != "||"
                self.end_segment(piped=piped)
                i += 2 if two in ("&&", "||", "|&", ";;") else 1
            else:
                self.add(c)
                i += 1
        if stop:
            raise LexError("unterminated substitution")
        self.end_segment()
        if self.pending:
            raise LexError("unterminated heredoc")
        return i


def lex(command: str) -> list[Segment]:
    lexer = _Lexer(command)
    lexer.run()
    return lexer.segments
