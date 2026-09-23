"""Subsonic response envelope: one dict model, serialized as XML or JSON.

Model rules (they match how the Subsonic XML schema maps onto its JSON form):

* scalar values → XML attributes / JSON fields;
* a dict value → one child element;
* a list of dicts → repeated child elements named after the key;
* a list of scalars → repeated child elements with text content;
* the key ``value`` on a dict → that element's text content (lyrics).

HTTP status is always 200; failures are ``status="failed"`` + ``error{code,message}``
— clients depend on that.
"""

import json
from xml.sax.saxutils import escape, quoteattr

from flask import Response, request

from ...config import __version__

API_VERSION = "1.16.1"
XMLNS = "http://subsonic.org/restapi"

# Subsonic / OpenSubsonic error codes.
E_GENERIC = 0
E_MISSING_PARAM = 10
E_CLIENT_TOO_OLD = 20
E_WRONG_CREDENTIALS = 40
E_TOKEN_NOT_SUPPORTED = 41
E_AUTH_MECHANISM_UNSUPPORTED = 42
E_CONFLICTING_AUTH = 43
E_INVALID_API_KEY = 44
E_NOT_AUTHORIZED = 50
E_NOT_FOUND = 70


class SubsonicError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _scalar(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _xml(name: str, node: dict) -> str:
    attrs, children, text = [], [], None
    for k, v in node.items():
        if v is None:
            continue
        if k == "value" and not isinstance(v, (dict, list)):
            text = _scalar(v)
        elif isinstance(v, dict):
            children.append(_xml(k, v))
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    children.append(_xml(k, item))
                elif item is not None:
                    children.append(f"<{k}>{escape(_scalar(item))}</{k}>")
        else:
            attrs.append(f" {k}={quoteattr(_scalar(v))}")
    head = f"<{name}{''.join(attrs)}"
    if not children and text is None:
        return head + "/>"
    return f"{head}>{escape(text) if text is not None else ''}{''.join(children)}</{name}>"


def _strip_none(v):
    if isinstance(v, dict):
        return {k: _strip_none(x) for k, x in v.items() if x is not None}
    if isinstance(v, list):
        return [_strip_none(x) for x in v if x is not None]
    return v


def _envelope(status: str, body: dict) -> dict:
    return {"status": status, "version": API_VERSION, "type": "bpm-tagger",
            "serverVersion": __version__, "openSubsonic": True, **body}


def render(status: str, body: dict) -> Response:
    root = _envelope(status, body)
    fmt = (request.values.get("f") or "xml").lower()
    if fmt in ("json", "jsonp"):
        payload = json.dumps({"subsonic-response": _strip_none(root)}, ensure_ascii=False)
        cb = request.values.get("callback", "")
        if fmt == "jsonp" and cb.replace("_", "").replace(".", "").isalnum():
            return Response(f"{cb}({payload});", mimetype="application/javascript")
        return Response(payload, mimetype="application/json")
    xml = '<?xml version="1.0" encoding="UTF-8"?>' + _xml(
        "subsonic-response", {"xmlns": XMLNS, **root})
    return Response(xml, mimetype="text/xml")


def ok(**body) -> Response:
    return render("ok", body)


def failed(code: int, message: str) -> Response:
    return render("failed", {"error": {"code": code, "message": message}})
