"""Email Template Helpers - branded HTML shell and building blocks for transactional emails.

Service helpers that wrap message content in a table-based, inline-styled eTown layout (teal accent on warm paper, slate secondary text) and render paragraphs, OTP panels, buttons and bullet lists that survive Gmail, Apple Mail and Outlook.

Features:
    - Table Layout: 600px centred card, no flexbox, no grid, no external CSS
    - Inline Styles: every declaration lives on the element Gmail cannot strip
    - Preheader: hidden text controlling the inbox preview line
    - Light Locked: color-scheme meta stops clients force-inverting the palette
    - Websafe Fonts: Segoe UI stack with Helvetica/Arial fallbacks, no web fonts

Functions:
    render_email(): Wrap content in the branded shell and return a full HTML document
    paragraph(): Render a body paragraph, optionally in muted slate
    otp_panel(): Render a one-time code in a bordered, letter-spaced panel
    button(): Render a table-cell call-to-action button with a plain-link fallback
    bullet_list(): Render a bulleted list in the body type scale

Dependencies:
    - None; pure string composition

Updates:
    24-Aug-2026: Initial creation; branded shell shared by auth, OTP and account emails.
    16-Sep-2026: Added bullet_list().
"""

_PAPER = "#FBFAF7"
_CARD = "#FFFFFF"
_INK = "#1C2321"
_SLATE = "#5C6B66"
_ACCENT = "#0E7C6B"
_ACCENT_SOFT = "#E4F0ED"
_RULE = "#E3E0D8"

_FONT = "'Segoe UI',-apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif"
_MONO = "ui-monospace,'Cascadia Mono',Consolas,'Courier New',monospace"

_DEFAULT_FOOTER = (
    "This is an automated message from Etown. Please do not reply to this email."
)


def paragraph(text: str, *, muted: bool = False) -> str:
    """Return a styled <p> for HTML text, smaller and slate when muted; escape user input first."""
    size, colour = ("14px", _SLATE) if muted else ("16px", _INK)
    return (
        f'<p style="margin:0 0 16px;font-family:{_FONT};font-size:{size};'
        f'line-height:1.6;color:{colour};">{text}</p>'
    )


def otp_panel(code: str, caption: str) -> str:
    """Return a full-width bordered panel showing a one-time code with a caption beneath it."""
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'width="100%" style="margin:0 0 20px;"><tr>'
        f'<td align="center" bgcolor="{_ACCENT_SOFT}" style="padding:20px 16px;'
        f'border:1px solid {_RULE};border-radius:8px;">'
        f'<div style="font-family:{_MONO};font-size:32px;font-weight:700;'
        f'letter-spacing:8px;color:{_ACCENT};line-height:1.2;">{code}</div>'
        f'<div style="margin-top:8px;font-family:{_FONT};font-size:13px;'
        f'color:{_SLATE};">{caption}</div>'
        "</td></tr></table>"
    )


def button(label: str, url: str) -> str:
    """Return a call-to-action button HTML followed by the raw URL as a copyable fallback."""
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="margin:0 0 20px;"><tr>'
        f'<td bgcolor="{_ACCENT}" style="border-radius:6px;">'
        f'<a href="{url}" style="display:inline-block;padding:13px 28px;'
        f'font-family:{_FONT};font-size:16px;font-weight:600;color:#FFFFFF;'
        'text-decoration:none;border-radius:6px;">'
        f"{label}</a></td></tr></table>"
        f'<p style="margin:0 0 16px;font-family:{_FONT};font-size:13px;'
        f'line-height:1.5;color:{_SLATE};word-break:break-all;">'
        f'<a href="{url}" style="color:{_SLATE};">{url}</a></p>'
    )


def bullet_list(items: list[str]) -> str:
    """Return a styled <ul> of HTML items, or "" when empty; escape user input first."""
    if not items:
        return ""
    rows = "".join(
        f'<li style="margin:0 0 8px;">{item}</li>' for item in items
    )
    return (
        f'<ul style="margin:0 0 16px;padding-left:22px;font-family:{_FONT};'
        f'font-size:16px;line-height:1.6;color:{_INK};">{rows}</ul>'
    )


def render_email(
    *,
    heading: str,
    content: str,
    preheader: str = "",
    footer: str = _DEFAULT_FOOTER,
) -> str:
    """Wrap body HTML in the branded shell with heading, hidden preheader and footer, returning a full document."""
    return (
        "<!DOCTYPE html><html><head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light">'
        '<meta name="supported-color-schemes" content="light">'
        f"<title>{heading}</title></head>"
        f'<body style="margin:0;padding:0;background-color:{_PAPER};">'
        '<div style="display:none;max-height:0;overflow:hidden;opacity:0;">'
        f"{preheader or heading}</div>"
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'width="100%" bgcolor="{_PAPER}" style="background-color:{_PAPER};"><tr>'
        '<td align="center" style="padding:32px 16px;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'width="600" style="width:600px;max-width:100%;"><tr>'
        '<td style="padding:0 4px 16px;">'
        f'<span style="font-family:{_FONT};font-size:20px;font-weight:700;'
        f'letter-spacing:0.5px;color:{_ACCENT};">eTown</span>'
        "</td></tr><tr>"
        f'<td bgcolor="{_CARD}" style="background-color:{_CARD};'
        f'border:1px solid {_RULE};border-radius:10px;padding:32px 28px;">'
        f'<h1 style="margin:0 0 20px;font-family:{_FONT};font-size:22px;'
        f'font-weight:600;line-height:1.3;color:{_INK};">{heading}</h1>'
        f"{content}"
        "</td></tr><tr>"
        f'<td style="padding:20px 4px 0;font-family:{_FONT};font-size:12px;'
        f'line-height:1.5;color:{_SLATE};">{footer}</td>'
        "</tr></table></td></tr></table></body></html>"
    )
