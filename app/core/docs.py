"""Swagger Docs Helpers - custom /docs page whose filter box searches endpoints as well as tags.

Core helper that renders the /docs Swagger UI from a template whose plugin replaces the tag-only opsFilter with one matching path, method, summary and operationId, while a tag whose own name matches is kept whole.

Functions:
    get_docs_html(): Render the /docs Swagger UI page with the endpoint search filter
"""

from fastapi.responses import HTMLResponse

SWAGGER_UI_JS = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"
SWAGGER_UI_CSS = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css"

FILTER_PLACEHOLDER = "Search endpoints by path, method, summary or tag"

_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="__CSS_URL__">
<style>
  .filter-container input { width: 100%; }
</style>
</head>
<body>
<div id="swagger-ui"></div>
<script src="__JS_URL__"></script>
<script>
// Replaces Swagger UI's tag-only filter. taggedOps is an Immutable OrderedMap of
// tag name -> Map{tagDetails, operations}; each operation carries path/method plus the
// raw spec fragment under "operation". Returning null for a tag drops the whole group.
var EndpointSearchPlugin = function () {
  return {
    fn: {
      opsFilter: function (taggedOps, phrase) {
        var q = String(phrase == null ? "" : phrase).trim().toLowerCase();
        if (!q) { return taggedOps; }
        return taggedOps
          .map(function (tagObj, tagName) {
            if (String(tagName).toLowerCase().indexOf(q) !== -1) { return tagObj; }
            var matches = tagObj.get("operations").filter(function (op) {
              var parts = [
                op.get("path"),
                op.get("method"),
                op.getIn(["operation", "summary"]),
                op.getIn(["operation", "operationId"])
              ];
              for (var i = 0; i < parts.length; i++) {
                var value = parts[i];
                if (value == null) { continue; }
                if (String(value).toLowerCase().indexOf(q) !== -1) { return true; }
              }
              return false;
            });
            return matches.size > 0 ? tagObj.set("operations", matches) : null;
          })
          .filter(function (tagObj) { return tagObj !== null; });
      }
    }
  };
};

var ui = SwaggerUIBundle({
  url: "__OPENAPI_URL__",
  dom_id: "#swagger-ui",
  presets: [SwaggerUIBundle.presets.apis],
  plugins: [EndpointSearchPlugin],
  layout: "BaseLayout",
  deepLinking: true,
  showExtensions: true,
  showCommonExtensions: true,
  filter: true,
  docExpansion: "list",
  defaultModelsExpandDepth: -1,
  persistAuthorization: false
});

// persistAuthorization used to be on, which left a token in localStorage that reopened
// the Authorize dialog already signed in. Clear it so the field starts empty and editable.
try { localStorage.removeItem("authorized"); } catch (e) {}

// The stock placeholder reads "Filter by tag", which now understates what the box does.
// Swagger UI renders asynchronously and re-renders the input on filtering, so relabel on
// mutation rather than once at load.
var relabelFilter = function () {
  var input = document.querySelector(".filter-container input");
  if (input && input.placeholder !== "__PLACEHOLDER__") {
    input.placeholder = "__PLACEHOLDER__";
  }
};
new MutationObserver(relabelFilter).observe(
  document.getElementById("swagger-ui"), { childList: true, subtree: true }
);
</script>
</body>
</html>
"""


def get_docs_html(*, openapi_url: str, title: str) -> HTMLResponse:
    """Render the Swagger /docs page for an OpenAPI URL that already carries any root_path prefix."""
    html = (
        _TEMPLATE
        .replace("__TITLE__", title)
        .replace("__CSS_URL__", SWAGGER_UI_CSS)
        .replace("__JS_URL__", SWAGGER_UI_JS)
        .replace("__OPENAPI_URL__", openapi_url)
        .replace("__PLACEHOLDER__", FILTER_PLACEHOLDER)
    )
    return HTMLResponse(html)
