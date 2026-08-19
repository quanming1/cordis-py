"""冒烟测试：包可导入、版本号就位。"""


def test_import() -> None:
    import cordis

    assert cordis.__version__ == "0.1.0"


def test_package_metadata() -> None:
    import cordis

    assert cordis.__doc__ is not None
    assert "Cordis" in cordis.__doc__
