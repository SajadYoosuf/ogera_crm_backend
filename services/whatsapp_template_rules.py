"""
Template rules: validation, Meta payload building and parsing. Pure functions, no database or network.

Ported from the WhatsApp Business app (src/lib/whatsapp/template-validators.ts, template-components.ts,
template-status-normalize.ts). Every check raises TemplateRuleError with one plain sentence, so a
mistake is reported at save time ("Button 2 needs a URL") and not as a bare 400 from Meta.

Limits follow Meta's Cloud API template limits.
"""
import re
from urllib.parse import urlparse

from schemas.whatsapp_template import TemplateButton, WhatsAppTemplateInput

BODY_MAX = 1024
FOOTER_MAX = 60
HEADER_TEXT_MAX = 60
BUTTON_TEXT_MAX = 25
MAX_BUTTONS = 10
MAX_URL_BUTTONS = 2
MAX_PHONE_BUTTONS = 1
MAX_COPY_CODE_BUTTONS = 1
NAME_RE = re.compile(r"^[a-z0-9_]{1,512}$")

STATUSES = ("DRAFT", "PENDING", "APPROVED", "REJECTED", "PAUSED", "DISABLED", "IN_APPEAL", "PENDING_DELETION")
_WEBHOOK_EVENT_STATUS = {"REINSTATED": "APPROVED", "DELETED": "PENDING_DELETION"}


class TemplateRuleError(ValueError):
    """The template breaks one of Meta's rules. The message is safe to show to the user."""


def extract_variable_indices(text: str | None) -> list[int]:
    """Sorted, unique {{N}} numbers: 'Hi {{1}} {{2}}, item {{4}}' -> [1, 2, 4]."""
    found = {int(m) for m in re.findall(r"\{\{(\d+)\}\}", text or "") if int(m) >= 1}
    return sorted(found)


def _is_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _check_contiguous(indices: list[int]) -> None:
    for position, number in enumerate(indices, start=1):
        if number != position:
            found = ", ".join("{{%d}}" % n for n in indices)
            raise TemplateRuleError(f"Body variables must run {{{{1}}}}, {{{{2}}}}, ... with no gaps. Found {found}.")


def validate_body(body: str) -> list[int]:
    if not body.strip():
        raise TemplateRuleError("The message body is required.")
    if len(body) > BODY_MAX:
        raise TemplateRuleError(f"The body can be at most {BODY_MAX} characters (it is {len(body)}).")
    indices = extract_variable_indices(body)
    _check_contiguous(indices)
    return indices


def validate_footer(footer: str | None) -> None:
    if not footer:
        return
    if len(footer) > FOOTER_MAX:
        raise TemplateRuleError(f"The footer can be at most {FOOTER_MAX} characters (it is {len(footer)}).")
    if extract_variable_indices(footer):
        raise TemplateRuleError("The footer cannot contain variables like {{1}}.")


def validate_header(t: WhatsAppTemplateInput) -> int:
    """Returns how many variables the header has (0 or 1)."""
    if not t.header_type:
        return 0
    if t.header_type == "text":
        text = t.header_content
        if not text or not text.strip():
            raise TemplateRuleError("The header text is required.")
        if len(text) > HEADER_TEXT_MAX:
            raise TemplateRuleError(f"The header text can be at most {HEADER_TEXT_MAX} characters (it is {len(text)}).")
        indices = extract_variable_indices(text)
        if len(indices) > 1:
            raise TemplateRuleError(f"The header can have only one variable (it has {len(indices)}).")
        if indices and indices[0] != 1:
            raise TemplateRuleError("The header variable must be {{1}}.")
        return len(indices)
    if not t.header_media_url and not t.header_handle:
        raise TemplateRuleError(f"A {t.header_type} header needs a sample file link.")
    if t.header_media_url and not _is_http_url(t.header_media_url):
        raise TemplateRuleError("The header sample link must be a web address starting with https://.")
    return 0


def validate_buttons(buttons: list[TemplateButton] | None) -> None:
    if not buttons:
        return
    if len(buttons) > MAX_BUTTONS:
        raise TemplateRuleError(f"A template can have at most {MAX_BUTTONS} buttons (it has {len(buttons)}).")
    counts = {kind: sum(1 for b in buttons if b.type == kind) for kind in ("QUICK_REPLY", "URL", "PHONE_NUMBER", "COPY_CODE")}
    if counts["URL"] > MAX_URL_BUTTONS:
        raise TemplateRuleError(f"A template can have at most {MAX_URL_BUTTONS} URL buttons.")
    if counts["PHONE_NUMBER"] > MAX_PHONE_BUTTONS:
        raise TemplateRuleError(f"A template can have at most {MAX_PHONE_BUTTONS} phone button.")
    if counts["COPY_CODE"] > MAX_COPY_CODE_BUTTONS:
        raise TemplateRuleError(f"A template can have at most {MAX_COPY_CODE_BUTTONS} copy-code button.")

    left_quick_replies = False
    for b in buttons:  # Meta: quick replies must come together, not mixed in with other buttons
        if b.type == "QUICK_REPLY":
            if left_quick_replies:
                raise TemplateRuleError("Quick-reply buttons must be grouped together, not mixed with other buttons.")
        else:
            left_quick_replies = True

    for n, b in enumerate(buttons, start=1):
        text = (b.text or "").strip()
        if not text:
            raise TemplateRuleError(f"Button {n} ({b.type}) needs text.")
        if len(text) > BUTTON_TEXT_MAX:
            raise TemplateRuleError(f"Button {n} text can be at most {BUTTON_TEXT_MAX} characters.")
        if b.type == "URL":
            if not (b.url or "").strip():
                raise TemplateRuleError(f"Button {n} needs a URL.")
            url_vars = extract_variable_indices(b.url)
            # {{1}} is not a valid URL character set, so check the address with the variable swapped out
            if not _is_http_url(re.sub(r"\{\{\d+\}\}", "x", b.url)):
                raise TemplateRuleError(f"Button {n} URL is not a valid web address.")
            if len(url_vars) > 1:
                raise TemplateRuleError(f"Button {n} URL can have only one variable.")
            if url_vars:
                if url_vars[0] != 1:
                    raise TemplateRuleError(f"Button {n} URL variable must be {{{{1}}}}.")
                if not (b.example or "").strip():
                    raise TemplateRuleError(f"Button {n} URL has {{{{1}}}}, so it needs an example link.")
        elif b.type == "PHONE_NUMBER":
            if not (b.phone_number or "").strip():
                raise TemplateRuleError(f"Button {n} needs a phone number.")
        elif b.type == "COPY_CODE":
            if not (b.example or "").strip():
                raise TemplateRuleError(f"Button {n} needs an example code.")


def validate_sample_values(t: WhatsAppTemplateInput, body_vars: int, header_vars: int) -> None:
    """Meta reviews templates by example, so every variable needs a sample value."""
    body = t.sample_values.body if t.sample_values else []
    header = t.sample_values.header if t.sample_values else []
    if len(body) != body_vars:
        raise TemplateRuleError(f"The body has {body_vars} variable(s) but {len(body)} sample value(s) were given.")
    if len(header) != header_vars:
        raise TemplateRuleError(f"The header has {header_vars} variable(s) but {len(header)} sample value(s) were given.")
    for n, value in enumerate(body, start=1):
        if not value.strip():
            raise TemplateRuleError(f"Body sample value {n} is empty.")
    for n, value in enumerate(header, start=1):
        if not value.strip():
            raise TemplateRuleError(f"Header sample value {n} is empty.")


def validate_template(t: WhatsAppTemplateInput) -> tuple[int, int]:
    """Run every rule. Returns (body variable count, header variable count)."""
    if not t.name:
        raise TemplateRuleError("The template name is required.")
    if not NAME_RE.match(t.name):
        raise TemplateRuleError("The name can use only lowercase letters, numbers and underscores.")
    if not t.language:
        raise TemplateRuleError("The language is required.")
    body_vars = validate_body(t.body_text)
    validate_footer(t.footer_text)
    header_vars = validate_header(t)
    validate_buttons(t.buttons)
    validate_sample_values(t, len(body_vars), header_vars)
    return len(body_vars), header_vars


# ── Meta payloads ──────────────────────────────────────────────────────────────

def _button_payload(b: TemplateButton) -> dict:
    if b.type == "QUICK_REPLY":
        return {"type": "QUICK_REPLY", "text": b.text}
    if b.type == "URL":
        out = {"type": "URL", "text": b.text, "url": b.url}
        if b.example:
            out["example"] = [b.example]
        return out
    if b.type == "PHONE_NUMBER":
        return {"type": "PHONE_NUMBER", "text": b.text, "phone_number": b.phone_number}
    return {"type": "COPY_CODE", "text": b.text, "example": [b.example]}


def build_meta_components(t: WhatsAppTemplateInput) -> list[dict]:
    """The `components` list Meta expects, in order: HEADER, BODY, FOOTER, BUTTONS."""
    components: list[dict] = []
    samples = t.sample_values

    if t.header_type == "text":
        header: dict = {"type": "HEADER", "format": "TEXT", "text": t.header_content}
        if samples and samples.header:
            header["example"] = {"header_text": samples.header}
        components.append(header)
    elif t.header_type:
        header = {"type": "HEADER", "format": t.header_type.upper()}
        if t.header_handle:
            header["example"] = {"header_handle": [t.header_handle]}
        elif t.header_media_url:
            header["example"] = {"header_url": [t.header_media_url]}
        components.append(header)

    body: dict = {"type": "BODY", "text": t.body_text}
    if samples and samples.body:
        body["example"] = {"body_text": [samples.body]}  # outer list = example rows; we send one
    components.append(body)

    if t.footer_text and t.footer_text.strip():
        components.append({"type": "FOOTER", "text": t.footer_text})
    if t.buttons:
        components.append({"type": "BUTTONS", "buttons": [_button_payload(b) for b in t.buttons]})
    return components


def build_meta_submit_payload(t: WhatsAppTemplateInput) -> dict:
    return {"name": t.name, "category": t.category, "language": t.language, "components": build_meta_components(t)}


def parse_meta_components(components: list[dict] | None) -> dict:
    """Turn Meta's `components` back into our columns (used when syncing templates made in Meta)."""
    out: dict = {"header_type": None, "header_content": None, "header_media_url": None, "header_handle": None,
                 "body_text": "", "footer_text": None, "buttons": None, "sample_values": None}
    body_samples: list[str] = []
    header_samples: list[str] = []
    for c in components or []:
        kind = c.get("type")
        example = c.get("example") or {}
        if kind == "HEADER":
            fmt = (c.get("format") or "TEXT").lower()
            out["header_type"] = fmt if fmt in ("text", "image", "video", "document") else None
            if fmt == "text":
                out["header_content"] = c.get("text")
                header_samples = list(example.get("header_text") or [])
            else:
                handles = example.get("header_handle") or []
                urls = example.get("header_url") or []
                out["header_handle"] = handles[0] if handles else None
                out["header_media_url"] = urls[0] if urls else None
        elif kind == "BODY":
            out["body_text"] = c.get("text") or ""
            rows = example.get("body_text") or []
            body_samples = list(rows[0]) if rows else []
        elif kind == "FOOTER":
            out["footer_text"] = c.get("text")
        elif kind == "BUTTONS":
            buttons = []
            for b in c.get("buttons") or []:
                if b.get("type") not in ("QUICK_REPLY", "URL", "PHONE_NUMBER", "COPY_CODE"):
                    continue  # e.g. flow, order buttons: not supported here
                ex = b.get("example")
                buttons.append({
                    "type": b["type"], "text": b.get("text") or "", "url": b.get("url"),
                    "phone_number": b.get("phone_number"),
                    "example": (ex[0] if isinstance(ex, list) and ex else ex) or None,
                })
            out["buttons"] = buttons or None
    if body_samples or header_samples:
        out["sample_values"] = {"body": body_samples, "header": header_samples}
    return out


# ── Status words ───────────────────────────────────────────────────────────────

def normalize_status(raw: str | None) -> str:
    """Meta's status string -> ours. Meta sometimes says PENDING_REVIEW; unknown words become PENDING."""
    upper = (raw or "").upper()
    if upper == "PENDING_REVIEW":
        return "PENDING"
    return upper if upper in STATUSES else "PENDING"


def status_from_webhook_event(event: str | None) -> str | None:
    upper = (event or "").upper()
    if upper == "PENDING_REVIEW":
        return "PENDING"
    if upper in STATUSES:
        return upper
    return _WEBHOOK_EVENT_STATUS.get(upper)


def normalize_quality(raw) -> str | None:
    value = raw.get("score") if isinstance(raw, dict) else raw
    value = (value or "").upper() if isinstance(value, str) else ""
    return value if value in ("GREEN", "YELLOW", "RED") else None


def can_edit(status: str) -> bool:
    """Meta does not allow edits while a template is under review, in appeal, disabled or being deleted."""
    return status in ("DRAFT", "APPROVED", "REJECTED", "PAUSED")
