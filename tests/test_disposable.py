"""DisposableList 单元测试（PRD-B1 AC3）。"""
from cordis import DisposableList


def test_push_and_len() -> None:
    lst: DisposableList[str] = DisposableList()
    assert len(lst) == 0
    lst.push("a")
    lst.push("b")
    lst.push("c")
    assert len(lst) == 3
    assert list(lst) == ["a", "b", "c"]


def test_push_returns_remove_callback() -> None:
    lst: DisposableList[str] = DisposableList()
    remove_a = lst.push("a")
    lst.push("b")
    assert remove_a() is True
    assert list(lst) == ["b"]
    # 二次删除同一元素：已不在列表中
    assert remove_a() is False


def test_delete_by_identity() -> None:
    lst: DisposableList[object] = DisposableList()
    obj = object()
    lst.push(obj)
    assert lst.delete(obj) is True
    assert lst.delete(object()) is False
    assert len(lst) == 0


def test_clear_returns_lifo() -> None:
    lst: DisposableList[str] = DisposableList()
    for item in ["a", "b", "c"]:
        lst.push(item)
    popped = lst.clear()
    # 倒序弹出：后建立的先清理
    assert popped == ["c", "b", "a"]
    assert len(lst) == 0
    assert lst.clear() == []
