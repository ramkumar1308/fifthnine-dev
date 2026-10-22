import pytest

from fifthnine.core.support import classify, in_range, load_matrix
from fifthnine.schema.v1 import SupportLevel


@pytest.mark.parametrize(("component", "version", "level"), [
    ("spring-boot", "3.4.13", SupportLevel.UNTESTED),
    ("spring-boot", "3.2.0", SupportLevel.UNTESTED),
    ("spring-boot", "2.7.18", SupportLevel.UNSUPPORTED),
    ("spring-boot", "4.0.1", SupportLevel.UNSUPPORTED),
    ("spring-boot", None, SupportLevel.UNTESTED),
    ("spring-boot", "3.4.0-M1", SupportLevel.UNTESTED),  # not comparable: never claimed unsupported or tested
    ("java", "21", SupportLevel.UNTESTED),
    ("java", "21.0.4", SupportLevel.UNTESTED),
    ("java", "11", SupportLevel.UNSUPPORTED),
    ("python", "3.9", SupportLevel.UNSUPPORTED),
    ("python", "3.13", SupportLevel.UNTESTED),
    ("kubernetes-api", "apps/v1/Deployment", SupportLevel.UNTESTED),
    ("kubernetes-api", "apps/v1/StatefulSet", SupportLevel.UNSUPPORTED),
    ("tenacity", "7.0.0", SupportLevel.UNSUPPORTED),
])
def test_classification(component: str, version: str | None, level: SupportLevel) -> None:
    assert classify(component, version).support is level


def test_nothing_is_tested_yet() -> None:
    assert all(not c.tested_versions for c in load_matrix().components)
    assert in_range("unknown-component", "1.0") is None
