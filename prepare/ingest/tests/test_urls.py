from prepare.ingest.urls import Scope, normalize


def test_normalize_drops_fragment_tracking_and_default_port():
    url = "HTTPS://Docs.Example.com:443/Guide/?utm_source=x&b=2&a=1&gclid=y#install"
    assert normalize(url) == "https://docs.example.com/Guide/?a=1&b=2"


def test_normalize_keeps_non_default_port_and_trailing_slash():
    assert normalize("http://localhost:8080/docs/") == "http://localhost:8080/docs/"
    assert normalize("http://localhost:8080") == "http://localhost:8080/"


def test_scope_defaults_prefix_to_start_path():
    scope = Scope("https://docs.astral.sh/uv/")
    assert scope.why_out_of_scope("https://docs.astral.sh/uv/concepts/") is None
    assert scope.why_out_of_scope("https://docs.astral.sh/uv") is None
    assert scope.why_out_of_scope("https://docs.astral.sh/ruff/") == "outside path prefix"
    assert scope.why_out_of_scope("https://docs.astral.sh/uvx/") == "outside path prefix"


def test_scope_rejects_other_hosts_files_and_excludes():
    scope = Scope("https://docs.example.com/", exclude=[r"/(zh-cn|\d+\.\d+)/"])
    assert scope.why_out_of_scope("https://example.com/") == "other host"
    assert scope.why_out_of_scope("http://docs.example.com/") == "other host"
    assert scope.why_out_of_scope("mailto:a@b.c") == "not http"
    assert scope.why_out_of_scope("https://docs.example.com/guide.pdf") == "not a page"
    assert scope.why_out_of_scope("https://docs.example.com/zh-cn/intro/") == "excluded"
    assert scope.why_out_of_scope("https://docs.example.com/1.24/intro/") == "excluded"
    assert scope.why_out_of_scope("https://docs.example.com/intro/") is None
