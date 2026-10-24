"""Translation-stage text units used by I0's exchange import/export:
T2 normalize, T3 mask, T11 validate, T12 postprocess (PROJECT_PLAN §6)."""
import re

from .langdetect import KANA

STYLES = {"<{}>": re.compile(r"<(\d+)>"), "⟦{}⟧": re.compile(r"⟦(\d+)⟧")}
_EXTRA = re.compile(r"^\s*(翻[譯译]|译文|譯文|以下是|Translation)\s*[:：]?", re.I)
_REPEAT = re.compile(r"(.{1,20}?)\1{3,}", re.S)
_KATAKANA = re.compile(r"[ァ-ヺー]")


def normalize(text: str) -> tuple[str, tuple[str, str]]:
    """T2: strip surrounding whitespace into `pad`; unify newlines."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    core = text.strip()
    if not core:
        return "", (text, "")
    start = text.index(core)
    return core, (text[:start], text[start + len(core):])


def mask(text: str, control_codes: list[str]) -> tuple[str, dict[str, str]]:
    """T3: adjacent control codes become one placeholder. `<n>` was best in the Sakura test (§17.2)."""
    style = "⟦{}⟧" if STYLES["<{}>"].search(text) else "<{}>"
    first = max((int(n) for n in STYLES[style].findall(text)), default=-1) + 1
    codes = re.compile("(?:" + "|".join(control_codes) + ")+") if control_codes else None
    masks: dict[str, str] = {}

    def rep(m):
        key = style.format(first + len(masks))
        masks[key] = m.group(0)
        return key

    return (codes.sub(rep, text) if codes else text), masks


def unmask(text: str, masks: dict[str, str]) -> str:
    for key, code in masks.items():
        text = text.replace(key, code)
    return text


def placeholders(text: str, masks: dict[str, str]) -> list[str]:
    style = next(iter(masks))[0] if masks else "<"
    pattern = STYLES["⟦{}⟧" if style == "⟦" else "<{}>"]
    return sorted(pattern.findall(text))


def validate(src: str, dst: str, masks: dict[str, str], repeat_threshold: int = 4) -> list[str]:
    """T11: per-sentence checks (§6.3). `src` and `dst` are masked text."""
    errors = []
    if src.strip() and not dst.strip():
        return ["empty"]
    if placeholders(src, masks) != placeholders(dst, masks):
        errors.append("placeholders")
    if "\n" in src and dst.count("\n") != src.count("\n"):
        errors.append("newlines")
    plain_src, plain_dst = _plain(src, masks), _plain(dst, masks)
    ratio = len(plain_dst) / len(plain_src) if plain_src else 1
    # Katakana loanwords shrink a lot in Chinese (コンティニュー → 继续), so skip the lower bound for them.
    loanword = len(_KATAKANA.findall(plain_src)) * 2 >= len(plain_src)
    if len(plain_src) >= 4 and (ratio > 3.0 or ratio < 0.3 and not loanword):
        errors.append("length_ratio")
    if len(plain_dst) >= 4 and len(KANA.findall(plain_dst)) / len(plain_dst) > 0.3:
        errors.append("kana_residue")
    repeat = _REPEAT if repeat_threshold == 4 else re.compile(r"(.{1,20}?)\1{" + str(repeat_threshold - 1) + r",}", re.S)
    if repeat.search(dst) and not repeat.search(src):
        errors.append("repetition")
    if _EXTRA.search(dst) and not _EXTRA.search(src):
        errors.append("extra_text")
    return errors


_BREAK = set("、。，．,.！？!?…‥」』）)】〉》 　")
_UNIT = re.compile(r"<\d+>|⟦\d+⟧|.", re.S)


def join_lines(lines: list[str]) -> str:
    """One message broken mid-sentence, as one line: CJK lines join directly, others with a space."""
    out = lines[0]
    for line in lines[1:]:
        out += ("" if not out or not line or not (out[-1].isascii() and line[0].isascii()) else " ") + line
    return out


def resplit(dst: str, lines: list[str]) -> str | None:
    """Break a one-line translation of `lines` back into as many lines, sized like the source lines; each cut moves
    to the nearest punctuation if one is close. Placeholders are never cut. None when there are too few characters."""
    units = _UNIT.findall(dst)
    k, n = len(lines), len(units)
    if n < k:
        return None
    weights = [max(1, len(line)) for line in lines]
    total, acc, prev, cuts = sum(weights), 0, 0, []
    span = max(2, n // (2 * k))
    for i in range(k - 1):
        acc += weights[i]
        lo, hi = prev + 1, n - (k - 1 - i)
        target = min(max(round(n * acc / total), lo), hi)
        near = [c for d in range(span + 1) for c in (target - d, target + d) if lo <= c <= hi and units[c - 1] in _BREAK]
        prev = near[0] if near else target
        cuts.append(prev)
    return "\n".join("".join(units[a:b]).strip() or "".join(units[a:b]) for a, b in zip([0] + cuts, cuts + [n]))


def postprocess(dst: str, masks: dict[str, str], pad: tuple[str, str]) -> str:
    """T12: restore control codes and surrounding whitespace. No Simplified/Traditional conversion."""
    return pad[0] + unmask(dst, masks) + pad[1]


def _plain(text: str, masks: dict[str, str]) -> str:
    for key in masks:
        text = text.replace(key, "")
    return re.sub(r"\s", "", text)
