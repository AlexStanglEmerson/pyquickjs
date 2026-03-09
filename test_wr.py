import weakref

class JSObject:
    __slots__ = ('props', '__weakref__')
    def __init__(self): self.props = {}

o = JSObject()
r = weakref.ref(o)
print('alive:', r() is not None)
o = None
print('after None:', r())
