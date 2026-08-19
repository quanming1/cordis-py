"""disposable 容器 —— 对应 cordis utils.ts 的 DisposableList。

插入序维护、按元素删除、clear 时倒序弹出（后进先出是副作用撤销的核心语义）。
"""
from __future__ import annotations

from collections.abc import Callable, Iterator


class DisposableList[T]:
    """插入序维护的 disposable 容器。

    对齐 cordis 的 ``DisposableList``：
    - ``push`` 返回删除回调（用于从列表中移除该元素）
    - ``clear`` 倒序弹出全部元素并清空（撤销时后建立的副作用先清理）
    - 支持迭代与 ``len()``
    """

    def __init__(self) -> None:
        self._items: dict[int, T] = {}
        self._sn = 0

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[T]:
        return iter(self._items.values())

    def __repr__(self) -> str:
        return f"DisposableList({list(self._items.values())!r})"

    def push(self, value: T) -> Callable[[], bool]:
        """插入元素，返回删除回调（调用后从列表移除该元素，返回是否移除成功）。"""
        self._sn += 1
        sn = self._sn
        self._items[sn] = value

        def remove() -> bool:
            if sn not in self._items:
                return False
            del self._items[sn]
            return True

        return remove

    def delete(self, value: T) -> bool:
        """按同一性删除元素（对齐 TS WeakMap 的严格相等语义）。"""
        for sn, item in self._items.items():
            if item is value:
                del self._items[sn]
                return True
        return False

    def clear(self) -> list[T]:
        """倒序弹出全部元素并清空，返回弹出顺序（LIFO）。"""
        values = list(self._items.values())
        values.reverse()
        self._items.clear()
        return values
