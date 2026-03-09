"""QuickJS runtime — global execution environment.

Ported from quickjs.c JSRuntime. The runtime holds the atom table, class registry,
and manages the job queue for promises/async.

Phase 1 stub — will be fleshed out in Phase 4.
"""

from __future__ import annotations

from typing import Any

from pyquickjs.atoms import AtomTable


class JSClassDef:
    """Class definition, mirrors JSClassDef from quickjs.h."""
    __slots__ = ('class_name', 'finalizer', 'call', 'exotic')

    def __init__(self, class_name: str, finalizer=None, call=None, exotic=None):
        self.class_name = class_name
        self.finalizer = finalizer
        self.call = call
        self.exotic = exotic


class JSRuntime:
    """Global JavaScript runtime environment.

    Holds:
    - Atom table (string interning)
    - Class registry
    - Job queue (for Promise microtasks)
    - Configuration (memory limits, stack size, etc.)
    """

    def __init__(self):
        self.atom_table = AtomTable()
        self._class_defs: dict[int, JSClassDef] = {}
        self._next_class_id = 1
        self._job_queue: list[Any] = []
        self.opaque: Any = None

        # Configuration
        self.memory_limit: int = 0  # 0 = no limit
        self.gc_threshold: int = 0
        self.max_stack_size: int = 1024 * 1024

        # Initialize built-in classes
        self._init_builtin_classes()

    def _init_builtin_classes(self):
        """Register built-in class definitions."""
        from pyquickjs.objects import JSClassID
        builtin = [
            (JSClassID.OBJECT, "Object"),
            (JSClassID.ARRAY, "Array"),
            (JSClassID.ERROR, "Error"),
            (JSClassID.NUMBER, "Number"),
            (JSClassID.STRING, "String"),
            (JSClassID.BOOLEAN, "Boolean"),
            (JSClassID.SYMBOL, "Symbol"),
            (JSClassID.ARGUMENTS, "Arguments"),
            (JSClassID.MAPPED_ARGUMENTS, "Arguments"),
            (JSClassID.DATE, "Date"),
            (JSClassID.MODULE_NS, "Module"),
            (JSClassID.C_FUNCTION, "Function"),
            (JSClassID.BYTECODE_FUNCTION, "Function"),
            (JSClassID.BOUND_FUNCTION, "Function"),
            (JSClassID.GENERATOR_FUNCTION, "GeneratorFunction"),
            (JSClassID.FOR_IN_ITERATOR, "ForInIterator"),
            (JSClassID.REGEXP, "RegExp"),
            (JSClassID.ARRAY_BUFFER, "ArrayBuffer"),
            (JSClassID.SHARED_ARRAY_BUFFER, "SharedArrayBuffer"),
            (JSClassID.MAP, "Map"),
            (JSClassID.SET, "Set"),
            (JSClassID.WEAKMAP, "WeakMap"),
            (JSClassID.WEAKSET, "WeakSet"),
            (JSClassID.GENERATOR, "Generator"),
            (JSClassID.PROXY, "Proxy"),
            (JSClassID.PROMISE, "Promise"),
            (JSClassID.ASYNC_FUNCTION, "AsyncFunction"),
            (JSClassID.ASYNC_GENERATOR_FUNCTION, "AsyncGeneratorFunction"),
            (JSClassID.ASYNC_GENERATOR, "AsyncGenerator"),
        ]
        for cid, name in builtin:
            self._class_defs[cid] = JSClassDef(name)

    def new_class_id(self) -> int:
        cid = self._next_class_id
        self._next_class_id += 1
        return cid

    def new_class(self, class_id: int, class_def: JSClassDef) -> None:
        self._class_defs[class_id] = class_def

    def is_registered_class(self, class_id: int) -> bool:
        return class_id in self._class_defs

    def get_class_name(self, class_id: int) -> str:
        cd = self._class_defs.get(class_id)
        return cd.class_name if cd else ""

    def enqueue_job(self, job) -> None:
        """Add a job (microtask) to the queue."""
        self._job_queue.append(job)

    def execute_pending_jobs(self) -> int:
        """Execute all pending jobs. Returns number of jobs executed."""
        count = 0
        while self._job_queue:
            job = self._job_queue.pop(0)
            job()
            count += 1
        return count
