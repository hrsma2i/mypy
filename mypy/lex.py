"""Lexical analyzer for mypy.

Translate a string that represents a file or a compilation unit to a list of
tokens.

This module can be run as a script (lex.py FILE).
"""

import re
from re import Match, Pattern

from mypy.util import short_type


class Token:
    """Base class for all tokens"""
    str pre = '' # Space, comments etc. before token
    str string   # Token string
    int line     # Token line number
    
    void __init__(self, str string, str pre=''):
        self.string = string
        self.pre = pre
    
    str __repr__(self):
        """The representation is of form Keyword('  if')."""
        t = short_type(self)
        return t + '(' + self.fix(self.pre) + self.fix(self.string) + ')'
    
    str rep(self):
        return self.pre + self.string
    
    str fix(self, str s):
        """Replace common non-printable chars with escape sequences.

        Do not use repr() since we don't want do duplicate backslashes.
        """
        return s.replace('\n', '\\n').replace('\t', '\\t').replace('\r', '\\r')


# Token classes


class Break(Token):
    """Statement break (line break or semicolon)"""


class Indent(Token):
    """Increase block indent level."""


class Dedent(Token):
    """Decrease block indent level."""


class Eof(Token):
    """End of file"""


class Keyword(Token):
    """Reserved word (other than keyword operators; they use Op).

    Examples: if, class, while, def.
    """


class Name(Token):
    """An alphanumeric identifier"""


class IntLit(Token):
    """Integer literal"""


class StrLit(Token):
    """String literal"""
    str parsed(self):
        """Return the parsed contents of the literal."""
        return _parse_str_literal(self.string)


class BytesLit(Token):
    """Bytes literal"""
    str parsed(self):
        """Return the parsed contents of the literal."""
        return _parse_str_literal(self.string)


class FloatLit(Token):
    """Float literal"""


class Punct(Token):
    """Punctuator (e.g. comma, '(' or '=')"""


class Colon(Token):
    pass


class Op(Token):
    """Operator (e.g. '+' or 'in')"""


class Bom(Token):
    """Byte order mark (at the start of a file)"""


class LexError(Token):
    """Lexer error token"""
    int type # One of the error types below
    
    void __init__(self, str string, int type):
        super().__init__(string)
        self.type = type


# Lexer error types
NUMERIC_LITERAL_ERROR = 0
UNTERMINATED_STRING_LITERAL = 1
INVALID_CHARACTER = 2
NON_ASCII_CHARACTER_IN_COMMENT = 3
NON_ASCII_CHARACTER_IN_STRING = 4
INVALID_UTF8_SEQUENCE = 5
INVALID_BACKSLASH = 6
INVALID_DEDENT = 7

# Encoding contexts
STR_CONTEXT = 1
COMMENT_CONTEXT = 2


Token[] lex(str string, int first_line=1):
    """Analyze string and return an array of token objects.

    The last token is always Eof.
    """
    l = Lexer()
    l.lex(string, first_line)
    return l.tok


# Reserved words (not including operators)
keywords = set([
    'as', 'assert', 'break', 'class', 'continue', 'def', 'del', 'elif',
    'else', 'except', 'finally', 'from', 'for', 'global', 'if', 'import',
    'lambda', 'pass', 'raise', 'return', 'try', 'while', 'with',
    'yield'])

# Alphabetical operators (reserved words)
alpha_operators = set(['in', 'is', 'not', 'and', 'or'])

# String literal prefixes
str_prefixes = set(['r', 'b', 'br'])  

# List of regular expressions that match non-alphabetical operators
Pattern[] operators = [re.compile('[-+*/<>.%&|^~]'),
                           re.compile('==|!=|<=|>=|\\*\\*|//|<<|>>')]

# List of regular expressions that match punctuator tokens
Pattern[] punctuators = [re.compile('[=,()@]'),
                             re.compile('\\['),
                             re.compile(']'),
                             re.compile('([-+*/%&|^]|\\*\\*|//|<<|>>)=')]


# Source file encodings
DEFAULT_ENCODING = 0
ASCII_ENCODING = 1
LATIN1_ENCODING = 2
UTF8_ENCODING = 3


# Map single-character string escape sequences to corresponding characters.
escape_map = {'a': '\x07',
              'b': '\x08',
              'f': '\x0c',
              'n': '\x0a',
              'r': '\x0d',
              't': '\x09',
              'v': '\x0b',
              '"': '"',
              "'": "'"}


# Matches the optional prefix of a string literal, e.g. the 'r' in r"foo".
str_prefix_re = re.compile('[rRbB]*')

# Matches an escape sequence in a string, e.g. \n or \x4F.
escape_re = re.compile(
    "\\\\([abfnrtv'\"]|x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|[0-7]{1,3})")


str _parse_str_literal(str string):
    """Translate escape sequences in str literal to the corresponding chars.

    For example, \t is translated to the tab character (ascii 9).

    Return the translated contents of the literal.  Also handle raw and
    triple-quoted string literals.
    """
    prefix = str_prefix_re.match(string).group(0).lower()
    s = string[len(prefix):]
    if s.startswith("'''") or s.startswith('"""'):
        return s[3:-3]
    elif 'r' in prefix:
        return s[1:-1].replace('\\' + s[0], s[0])
    else:
        return escape_re.sub(lambda m: escape_repl(m, prefix), s[1:-1])


str escape_repl(Match m, str prefix):
    """Translate a string escape sequence, e.g. \t -> the tab character.

    Assume that the Match object is from escape_re.
    """
    seq = m.group(1)
    if len(seq) == 1 and seq in escape_map:
        # Single-character escape sequence, e.g. \n.
        return escape_map[seq]
    elif seq.startswith('x'):
        # Hexadecimal sequence \xNN.
        return chr(int(seq[1:], 16))
    elif seq.startswith('u'):
        # Unicode sequence \uNNNN.
        if 'b' not in prefix:
            return chr(int(seq[1:], 16))
        else:
            return '\\' + seq
    else:
        # Octal sequence.
        ord = int(seq, 8)
        if 'b' in prefix:
            # Make sure code is no larger than 255 for bytes literals.
            ord = ord % 256
        return chr(ord)


class Lexer:
    """Lexical analyzer."""
    int i     # Current string index (into s)
    str s     # The string being analyzed
    int line  # Current line number
    str pre_whitespace = ''     # Whitespace and comments before the next token
    int enc = DEFAULT_ENCODING  # Encoding TODO implement properly

    # Generated tokens
    Token[] tok
    
    # Table from byte character value to lexer method. E.g. entry at ord('0')
    # contains the method lex_number().
    func<void()>[] map

    # Indent levels of currently open blocks, in spaces.
    int[] indents
    
    # Open ('s, ['s and {'s without matching closing bracket; used for ignoring
    # newlines within parentheses/brackets.
    str[] open_brackets
    
    void __init__(self):
        self.map = [self.unknown_character] * 256
        self.tok = []
        self.indents = [0]
        self.open_brackets = []
        # Fill in the map from valid character codes to relevant lexer methods.
        for seq, method in [('ABCDEFGHIJKLMNOPQRSTUVWXYZ', self.lex_name),
                            ('abcdefghijklmnopqrstuvwxyz_', self.lex_name),
                            ('0123456789', self.lex_number),
                            ('.', self.lex_number_or_dot),
                            (' ' + '\t' + '\x0c', self.lex_space),
                            ('"', self.lex_str_double),
                            ("'", self.lex_str_single),
                            ('\r' + '\n', self.lex_break),
                            (';', self.lex_semicolon),
                            (':', self.lex_colon),
                            ('#', self.lex_comment),
                            ('\\', self.lex_backslash),
                            ('([{', self.lex_open_bracket),
                            (')]}', self.lex_close_bracket),
                            ('-+*/<>%&|^~=!,@', self.lex_misc)]:
            for c in seq:
                self.map[ord(c)] = method
    
    void lex(self, str s, int first_line):
        """Lexically analyze a string, storing the tokens at the tok array."""
        self.s = s
        self.i = 0
        self.line = first_line

        if s.startswith('\xef\xbb\xbf'):
            self.add_token(Bom(s[0:3]))
            
        # Parse initial indent; otherwise first-line indent would not generate
        # an error.
        self.lex_indent()

        # Make a local copy of map as a simple optimization.
        map = self.map
        
        # Lex the file. Repeatedly call the lexer method for the current char.
        while self.i < len(s):
            # Get the character code of the next character to lex.
            c = ord(s[self.i])
            # Dispatch to the relevant lexer method. This will consume some
            # characters in the text, add a token to self.tok and increment
            # self.i.
            map[c]()
        
        # Append a break if there is no statement/block terminator at the end
        # of input.
        if len(self.tok) > 0 and (not isinstance(self.tok[-1], Break) and
                                  not isinstance(self.tok[-1], Dedent)):
            self.add_token(Break(''))

        # Close remaining open blocks with Dedent tokens.
        self.lex_indent()
        
        self.add_token(Eof(''))
    
    void lex_number_or_dot(self):
        """Analyse a token starting with a dot.

        It can be the member access operator or a float literal such as '.123'.
        """
        if self.is_at_number():
            self.lex_number()
        else:
            self.lex_misc()
    
    number_exp = re.compile(r'[0-9]|\.[0-9]')
    
    bool is_at_number(self):
        """Is the current location at a numeric literal?"""
        return self.match(self.number_exp) != ''
    
    # Regexps used by lex_number

    # Decimal/hex/octal literal
    number_exp1 = re.compile('0[xXoO][0-9a-fA-F]+|[0-9]+')
    # Float literal, e.g. '1.23' or '12e+34'
    number_exp2 = re.compile(
        r'[0-9]*\.[0-9]*([eE][-+]?[0-9]+)?|[0-9]+[eE][-+]?[0-9]+')
    # These characters must not appear after a number literal.
    name_char_exp = re.compile('[a-zA-Z0-9_]')
    
    void lex_number(self):
        """Analyse an int or float literal.

        Assume that the current location points to one of them.
        """
        s1 = self.match(self.number_exp1)
        s2 = self.match(self.number_exp2)
        
        maxlen = max(len(s1), len(s2))
        if self.name_char_exp.match(
                    self.s[self.i + maxlen:self.i + maxlen + 1]) is not None:
            # Error: alphanumeric character after number literal.
            s3 = self.match(re.compile('[0-9][0-9a-zA-Z_]*'))
            maxlen = max(maxlen, len(s3))
            self.add_token(LexError(' ' * maxlen, NUMERIC_LITERAL_ERROR))
        elif len(s1) > len(s2):
            # Integer literal.
            self.add_token(IntLit(s1))
        else:
            # Float literal.
            self.add_token(FloatLit(s2))
    
    name_exp = re.compile('[a-zA-Z_][a-zA-Z0-9_]*')
    
    void lex_name(self):
        """Analyse a name (an identifier, a keyword or an alphabetical
        operator).  This also deals with prefixed string literals such
        as r'...'.
        """
        s = self.match(self.name_exp)
        if s in keywords:
            self.add_token(Keyword(s))
        elif s in alpha_operators:
            self.add_token(Op(s))
        elif s in str_prefixes and self.match(re.compile('[a-z]+[\'"]')) != '':
            self.lex_prefixed_str(s)
        else:
            self.add_token(Name(s))
    
    # Regexps representing components of string literals

    # Initial part of a single-quoted literal, e.g. b'foo' or b'foo\\\n
    str_exp_single = re.compile(
        r"[a-z]*'([^'\\\r\n]|\\[^\r\n])*('|\\(\n|\r\n?))")
    # Non-initial part of a multiline single-quoted literal, e.g. foo'
    str_exp_single_multi = re.compile(
        r"([^'\\\r\n]|\\[^\r\n])*('|\\(\n|\r\n?))")
    # Initial part of a single-quoted raw literal, e.g. r'foo' or r'foo\\\n
    str_exp_raw_single = re.compile(
        r"[a-z]*'([^'\r\n\\]|\\'|\\[^\n\r])*('|\\(\n|\r\n?))")
    # Non-initial part of a raw multiline single-quoted literal, e.g. foo'
    str_exp_raw_single_multi = re.compile(
        r"([^'\r\n]|'')*('|\\(\n|\r\n?))")

    # Start of a ''' literal, e.g. b'''
    str_exp_single3 = re.compile("[a-z]*'''")
    # End of a ''' literal, e.g. foo'''
    str_exp_single3end = re.compile(r"[^\n\r]*?'''")

    # The following are similar to above (but use double quotes).
    
    str_exp_double = re.compile(
        r'[a-z]*"([^"\\\r\n]|\\[^\r\n])*("|\\(\n|\r\n?))')
    str_exp_double_multi = re.compile(
        r'([^"\\\r\n]|\\[^\r\n])*("|\\(\n|\r\n?))')  
    str_exp_raw_double = re.compile(
        r'[a-z]*"([^"\r\n\\]|\\"|\\[^\n\r])*("|\\(\n|\r\n?))')
    str_exp_raw_double_multi = re.compile(
        r'([^"\r\n]|"")*("|\\(\n|\r\n?))')
    
    str_exp_double3 = re.compile('[a-z]*"""')
    str_exp_double3end = re.compile(r'[^\n\r]*?"""')
    
    void lex_str_single(self):
        """Analyse single-quoted string literal"""
        self.lex_str(self.str_exp_single, self.str_exp_single_multi,
                     self.str_exp_single3, self.str_exp_single3end)
    
    void lex_str_double(self):
        """Analyse double-quoted string literal"""
        self.lex_str(self.str_exp_double, self.str_exp_double_multi,
                     self.str_exp_double3, self.str_exp_double3end)
    
    void lex_prefixed_str(self, str prefix):
        """Analyse a string literal with a prefix, such as r'...'."""
        s = self.match(re.compile('[a-z]+[\'"]'))
        if s.endswith("'"):
            re1 = self.str_exp_single
            re2 = self.str_exp_single_multi
            if 'r' in prefix:
                re1 = self.str_exp_raw_single
                re2 = self.str_exp_raw_single_multi
            self.lex_str(re1, re2, self.str_exp_single3,
                         self.str_exp_single3end, prefix)
        else:
            re1 = self.str_exp_double
            re2 = self.str_exp_double_multi
            if 'r' in prefix:
                re1 = self.str_exp_raw_double
                re2 = self.str_exp_raw_double_multi
            self.lex_str(re1, re2, self.str_exp_double3,
                         self.str_exp_double3end, prefix)
    
    void lex_str(self, Pattern regex, Pattern re2, Pattern re3, Pattern re3end,
                 str prefix=''):
        """Analyse a string literal described by regexps. Assume that
        the current location is at the beginning of the literal. The
        arguments re3 and re3end describe the corresponding
        triple-quoted literals.
        """
        s3 = self.match(re3)
        if s3 != '':
            # Triple-quoted string literal.
            self.lex_triple_quoted_str(re3end, prefix)
        else:
            # Single or double quoted string literal.
            s = self.match(regex)
            if s != '':
                if s.endswith('\n') or s.endswith('\r'):
                    self.lex_multiline_string_literal(re2, s)
                else:
                    self.verify_encoding(s, STR_CONTEXT)
                    if 'b' in prefix:
                        self.add_token(BytesLit(s))
                    else:
                        self.add_token(StrLit(s))
            else:
                # Unterminated string literal.
                s = self.match(re.compile('[^\\n\\r]*'))
                self.add_token(LexError(s, UNTERMINATED_STRING_LITERAL))
    
    void lex_triple_quoted_str(self, Pattern re3end, str prefix):
        line = self.line
        ss = self.s[self.i:self.i + len(prefix) + 3]
        self.i += len(prefix) + 3
        while True:
            m = re3end.match(self.s, self.i)
            if m is not None:
                break
            m = re.match('[^\\n\\r]*(\\n|\\r\\n?)', self.s[self.i:])
            if m is None:
                self.add_special_token(
                    LexError(ss, UNTERMINATED_STRING_LITERAL), line, 0)
                return 
            s = m.group(0)
            ss += s
            self.line += 1
            self.i += len(s)
        Token lit
        if 'b' in prefix:
            lit = BytesLit(ss + m.group(0))
        else:
            lit = StrLit(ss + m.group(0))
        self.add_special_token(lit, line, len(m.group(0)))
    
    void lex_multiline_string_literal(self, Pattern re_end, str prefix):
        """Analyze multiline single/double-quoted string literal.

        Use explicit \ for line continuation.
        """
        line = self.line
        self.i += len(prefix)
        ss = prefix
        while True:
            m = self.match(re_end)
            if m == '':
                self.add_special_token(
                    LexError(ss, UNTERMINATED_STRING_LITERAL), line, 0)
                return 
            ss += m
            self.line += 1
            self.i += len(m)        
            if not m.endswith('\n') and not m.endswith('\r'): break
        self.add_special_token(StrLit(ss), line, 0) # TODO bytes
    
    comment_exp = re.compile(r'#[^\n\r]*')
    
    void lex_comment(self):
        """Analyse a comment."""
        s = self.match(self.comment_exp)
        self.verify_encoding(s, COMMENT_CONTEXT)
        self.add_pre_whitespace(s)
    
    backslash_exp = re.compile(r'\\(\n|\r\n?)')
    
    void lex_backslash(self):
        s = self.match(self.backslash_exp)
        if s != '':
            self.add_pre_whitespace(s)
            self.line += 1
        else:
            self.add_token(LexError('\\', INVALID_BACKSLASH))
    
    space_exp = re.compile(r'[ \t\x0c]*')
    indent_exp = re.compile(r'[ \t]*[#\n\r]?')
    
    void lex_space(self):
        """Analyze a run of whitespace characters (within a line, not indents).

        Only store them in self.pre_whitespace.
        """
        s = self.match(self.space_exp)
        self.add_pre_whitespace(s)
    
    str comment_or_newline = '#' + '\n' + '\r'
    
    void lex_indent(self):
        """Analyze whitespace chars at the beginning of a line (indents)."""
        s = self.match(self.indent_exp)
        if s != '' and s[-1] in self.comment_or_newline:
            # Empty line (whitespace only or comment only).
            self.add_pre_whitespace(s[:-1])
            if s[-1] == '#':
                self.lex_comment()
            else:
                self.lex_break()
            self.lex_indent()
            return 
        indent = self.calc_indent(s)
        if indent == self.indents[-1]:
            # No change in indent: just whitespace.
            self.add_pre_whitespace(s)
        elif indent > self.indents[-1]:
            # An increased indent (new block).
            self.indents.append(indent)
            self.add_token(Indent(s))
        else:
            # Decreased indent (end of one or more blocks).
            pre = self.pre_whitespace
            self.pre_whitespace = ''
            while indent < self.indents[-1]:
                self.add_token(Dedent(''))
                self.indents.pop()
            self.pre_whitespace = pre
            self.add_pre_whitespace(s)            
            if indent != self.indents[-1]:
                # Error: indent level does not match a previous indent level.
                self.add_token(LexError('', INVALID_DEDENT))
    
    int calc_indent(self, str s):
        indent = 0
        for ch in s:
            if ch == ' ':
                indent += 1
            else:
                # Tab: 8 spaces (rounded to a multiple of 8).
                indent += 8 - indent % 8
        return indent
    
    break_exp = re.compile(r'\r\n|\r|\n|;')
    
    void lex_break(self):
        """Analyse a line break."""
        s = self.match(self.break_exp)
        if self.ignore_break():
            self.add_pre_whitespace(s)
            self.line += 1
        else:
            self.add_token(Break(s))
            self.line += 1
            self.lex_indent()
    
    void lex_semicolon(self):
        self.add_token(Break(';'))
    
    void lex_colon(self):
        self.add_token(Colon(':'))
    
    Pattern open_bracket_exp = re.compile('[[({]')
    
    void lex_open_bracket(self):
        s = self.match(self.open_bracket_exp)
        self.open_brackets.append(s)
        self.add_token(Punct(s))
    
    Pattern close_bracket_exp = re.compile('[])}]')
    
    dict<str, str> open_bracket = {')': '(', ']': '[', '}': '{'}
    
    void lex_close_bracket(self):
        s = self.match(self.close_bracket_exp)
        if (self.open_brackets != []
                and self.open_bracket[s] == self.open_brackets[-1]):
            self.open_brackets.pop()
        self.add_token(Punct(s))
    
    void lex_misc(self):
        """Analyse a non-alphabetical operator or a punctuator."""
        s = ''
        any t = None
        for re_list, type in [(operators, Op), (punctuators, Punct)]:
            for re in re_list:
                s2 = self.match(re)
                if len(s2) > len(s):
                    t = type
                    s = s2
        if s == '':
            # Could not match any token; report an invalid character. This is
            # reached at least if the current character is '!' not followed by
            # '='.
            self.add_token(LexError(self.s[self.i], INVALID_CHARACTER))
        else:
            self.add_token(t(s))
    
    void unknown_character(self):
        """Report an unknown character as a lexical analysis error."""
        self.add_token(LexError(self.s[self.i], INVALID_CHARACTER))
    
    
    # Utility methods
    
    
    str match(self, Pattern pattern):
        """If the argument regexp is matched at the current location,
        return the matched string; otherwise return the empty string.
        """
        m = pattern.match(self.s, self.i)
        if m is not None:
            return m.group(0)
        else:
            return ''
    
    void add_pre_whitespace(self, str s):
        """Record whitespace and comments before the next token.
        
        The accumulated whitespace/comments will be stored in the next token
        and then it will be cleared.

        This is needed for pretty-printing the original source code while
        preserving comments, indentation, whitespace etc.
        """
        self.pre_whitespace += s
        self.i += len(s)
    
    void add_token(self, Token tok):
        """Store a token. Update its line number and record preceding
        whitespace characters and comments.
        """
        if (tok.string == '' and not isinstance(tok, Eof)
                and not isinstance(tok, Break)
                and not isinstance(tok, LexError)
                and not isinstance(tok, Dedent)):
            raise ValueError('Empty token')
        tok.pre = self.pre_whitespace
        tok.line = self.line
        self.tok.append(tok)
        self.i += len(tok.string)
        self.pre_whitespace = ''
    
    void add_special_token(self, Token tok, int line, int skip):
        """Like add_token, but caller sets the number of chars to skip."""
        if (tok.string == '' and not isinstance(tok, Eof)
                and not isinstance(tok, Break)
                and not isinstance(tok, LexError)
                and not isinstance(tok, Dedent)):
            raise ValueError('Empty token')
        tok.pre = self.pre_whitespace
        tok.line = line
        self.tok.append(tok)
        self.i += skip
        self.pre_whitespace = ''
    
    bool ignore_break(self):
        """If the next token is a break, can we ignore it?"""
        if len(self.open_brackets) > 0 or len(self.tok) == 0:
            # Ignore break after open ( [ or { or at the beginning of file.
            return True
        else:
            # Ignore break after another break or dedent.
            t = self.tok[-1]
            return isinstance(t, Break) or isinstance(t, Dedent)
    
    void verify_encoding(self, str string, int context):
        """Verify that a token (represented by a string) is encoded correctly
        according to the file encoding.
        """
        str codec = None
        if self.enc == ASCII_ENCODING:
            codec = 'ascii'
        elif self.enc in [UTF8_ENCODING, DEFAULT_ENCODING]:
            codec = 'utf8'
        if codec is not None:
            try:
                pass # FIX string.decode(codec)
            except UnicodeDecodeError:
                type = INVALID_UTF8_SEQUENCE
                if self.enc == ASCII_ENCODING:
                    if context == STR_CONTEXT:
                        type = NON_ASCII_CHARACTER_IN_STRING
                    else:
                        type = NON_ASCII_CHARACTER_IN_COMMENT
                self.add_token(LexError('', type))


if __name__ == '__main__':
    import sys
    if len(sys.argv) != 2:
        print('Usage: lex.py FILE')
        sys.exit(2)
    fnam = sys.argv[1]
    s = open(fnam).read()
    for t in lex(s):
        print(t)
