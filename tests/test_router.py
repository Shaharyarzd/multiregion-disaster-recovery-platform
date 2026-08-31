from dr_platform.router import Endpoint, SyntheticRouter


def test_router_is_deterministic_and_skips_failed_region() -> None:
    router = SyntheticRouter()
    endpoints = [Endpoint("a", "https://a", False), Endpoint("b", "https://b", True)]
    assert router.select("txn-1", endpoints).region == "b"
    assert router.select("txn-1", endpoints).region == "b"


def test_router_fails_without_endpoint() -> None:
    router = SyntheticRouter()
    try:
        router.select("x", [])
    except RuntimeError as error:
        assert "No healthy endpoint" in str(error)
    else:
        raise AssertionError("expected RuntimeError")


def test_router_assertions() -> None:
    result = SyntheticRouter().assertions(
        [Endpoint("a", "https://a", True), Endpoint("b", "https://b", False)]
    )
    assert all(result.values())
