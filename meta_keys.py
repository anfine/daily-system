import hashlib


LEGACY_META_KEY_MAP = {
    "阅读": "reading",
    "听力": "listening",
    "游泳": "swimming",
}


def build_meta_key(label: str) -> str:
    mapped = LEGACY_META_KEY_MAP.get(label.strip())
    if mapped:
        return mapped

    sanitized = "".join(ch.lower() if ch.isascii() and ch.isalnum() else "-" for ch in label)
    sanitized = "-".join(part for part in sanitized.split("-") if part)
    if sanitized:
        return sanitized

    digest = hashlib.sha1(label.encode("utf-8")).hexdigest()[:12]
    return f"meta-{digest}"
