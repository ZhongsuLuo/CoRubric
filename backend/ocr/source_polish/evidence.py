"""Native PDF evidence and character offsets. No model calls or audit labels."""
import re
import unicodedata


ALIASES = {'−': '-', '⁻': '-', '，': ',', '。': '.', '；': ';', '：': ':',
           '（': '(', '）': ')', '／': '/', '＼': '\\', '．': '.', '～': '~'}
MACROS = {'mu': 'μ', 'Delta': 'Δ', 'varepsilon': 'ε', 'epsilon': 'ε',
          'approx': '≈', 'sim': '~', 'rightarrow': '→', 'times': '×',
          'cdot': '·', 'div': '÷', 'therefore': '∴'}

SCRIPT_CHARS = dict(zip('⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₓₛ',
                            '0123456789+-=()0123456789+-=()xs'))
SUB_CHARS = set('₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₓₛ')


def canon(char):
    return SCRIPT_CHARS.get(char, ALIASES.get(char, char))


def readable(text):
    return bool(text) and all(c != '\ufffd' and unicodedata.category(c) not in {'Co', 'Cs'} for c in text)


def project(text):
    """Map visible characters to raw offsets; protect math, code, links and tables.

    This intentionally recognizes a limited syntax, not a general LaTeX parser.
    Unknown commands remain protected; we never rewrite their internals.
    """
    chars, offsets, protected = [], [], []
    pattern = re.compile(r'```[\s\S]*?```|`[^`\n]*`|!\[[^\]]*\]\([^\n)]*\)|'
                         r'<!--.*?-->|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]|'
                         r'\$\$[\s\S]*?\$\$|(?<!\\)\$[^$\n]*\$|'
                         r'<(?:sup|sub)>.*?</(?:sup|sub)>|<[^>]+>|'
                         r'\\[A-Za-z]+|\\[,;! ]|[{}^_$#*]')
    def append(value, start, end, guard=False):
        for char in value:
            if not char.isspace():
                chars.append(canon(char)); offsets.append((start, end)); protected.append(guard or char in SCRIPT_CHARS)
    cursor = 0
    for match in pattern.finditer(text):
        for i in range(cursor, match.start()):
            append(text[i], i, i + 1)
        value = match[0]
        if value.startswith(('<!--', '![', '```', '`')):
            pass
        elif value.startswith(('$', '\\(', '\\[')):
            inner = value.strip('$') if value.startswith('$') else value[2:-2]
            inner = re.sub(r'\\(?:text|mathrm|operatorname)\{([^{}]*)\}', r'\1', inner)
            inner = re.sub(r'\\([A-Za-z]+)', lambda m: MACROS.get(m[1], m[0]), inner)
            inner = re.sub(r'\\[,;! ]|[{}^_$]', '', inner)
            append(inner, match.start(), match.end(), True)
        elif value.startswith(('<sup>', '<sub>')):
            append(re.sub('<[^>]+>', '', value), match.start(), match.end(), True)
        elif value.startswith('\\'):
            append(MACROS.get(value[1:], value), match.start(), match.end(), True)
        cursor = match.end()
    for i in range(cursor, len(text)):
        append(text[i], i, i + 1)
    return {'text': ''.join(chars), 'offsets': offsets, 'protected': protected}


