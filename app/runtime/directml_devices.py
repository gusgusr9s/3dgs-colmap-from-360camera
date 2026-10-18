"""Read DXGI's adapter order (the indices used by ONNX Runtime DirectML)."""
import ctypes
import os


def adapters():
    if os.name != "nt":
        return []
    from ctypes import wintypes
    class GUID(ctypes.Structure):
        _fields_ = [("a",ctypes.c_uint32),("b",ctypes.c_uint16),("c",ctypes.c_uint16),("d",ctypes.c_uint8*8)]
    class Description(ctypes.Structure):
        _fields_ = [("name",ctypes.c_wchar*128),("vendor",ctypes.c_uint32),("device",ctypes.c_uint32),
            ("subsystem",ctypes.c_uint32),("revision",ctypes.c_uint32),("video_memory",ctypes.c_size_t),
            ("system_memory",ctypes.c_size_t),("shared_memory",ctypes.c_size_t),("luid_low",ctypes.c_uint32),
            ("luid_high",ctypes.c_int32),("flags",ctypes.c_uint32)]
    iid=GUID(0x770aae78,0xf26f,0x4dba,(ctypes.c_uint8*8)(0xa8,0x29,0x25,0x3c,0x83,0xd1,0xb3,0x87))
    factory=ctypes.c_void_p()
    create=ctypes.WinDLL('dxgi.dll').CreateDXGIFactory1
    create.argtypes=[ctypes.POINTER(GUID),ctypes.POINTER(ctypes.c_void_p)];create.restype=ctypes.c_int32
    if create(ctypes.byref(iid),ctypes.byref(factory)) < 0:
        return []
    def method(pointer,index,restype,*argtypes):
        table=ctypes.cast(pointer,ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(restype,ctypes.c_void_p,*argtypes)(table[index])
    result=[]
    try:
        for index in range(32):
            adapter=ctypes.c_void_p()
            status=method(factory,12,ctypes.c_int32,ctypes.c_uint32,ctypes.POINTER(ctypes.c_void_p))(factory,index,ctypes.byref(adapter))
            if status < 0:
                break
            try:
                description=Description()
                if method(adapter,10,ctypes.c_int32,ctypes.POINTER(Description))(adapter,ctypes.byref(description)) >= 0:
                    result.append({'index':index,'name':description.name,'video_memory':description.video_memory,
                                   'software':bool(description.flags & 2)})
            finally:
                method(adapter,2,ctypes.c_uint32)(adapter)
    finally:
        method(factory,2,ctypes.c_uint32)(factory)
    return result
