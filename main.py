import json
from queue import Queue
from struct import *

from Constants import Constants


class ObjectIO:
    def __init__(self, base_stream):
        self.base_stream = base_stream

    def readByte(self) -> bytes:
        return self.base_stream.read(1)

    def peekByte(self) -> bytes:
        return self.base_stream.peek()[:1]

    def readUnsignedShort(self) -> int:
        number = self.readBytes(2)
        number = int.from_bytes(number, 'big')
        return number & 0xFFFF

    def readUnsignedLong(self) -> int:
        number = self.readBytes(8)
        return int.from_bytes(number, 'big') & 0xFFFFFFFFFFFFFFFF

    def readInt(self) -> int:
        return int.from_bytes(self.readBytes(4), 'big')

    def readBytes(self, length) -> int:
        return self.base_stream.read(length)

    def readString(self) -> str:
        length = self.readUnsignedShort()
        stringBuilder = ""
        for i in range(length):
            byte = self.readByte()
            stringBuilder += byte.decode()
        return stringBuilder

    def readFloat(self):
        num = self.readBytes(4)
        return num

    def readBoolean(self):
        tc = int.from_bytes(self.readByte(), 'big')
        return True if tc == 0 else False

    def writeBytes(self, value):
        self.base_stream.write(value)

    def writeString(self, value):
        length = len(value)
        self.writeUInt16(length)
        self.pack(str(length) + 's', value)

    def pack(self, fmt, data):
        return self.writeBytes(pack(fmt, data))

    def unpack(self, fmt, length=1):
        return unpack(fmt, self.readBytes(length))[0]


class ObjectStream:
    def __init__(self, stream):
        self.bin = stream
        self.fieldStack = []
        self.handles = []
        self.readStreamHeader()

    def newHandles(self, obj):
        self.handles.append(obj)
        return len(self.handles) - 1 + Constants.baseWireHandle

    def readStreamHeader(self):
        magic = self.bin.readUnsignedShort()
        version = self.bin.readUnsignedShort()
        if magic != Constants.magic or version != Constants.version:
            print(f"invalid bin header {magic:#2x} {version:#2x}")
            exit(-1)

    def readClassDescriptor(self):
        """
        读取非动态代理类的结构，目前还不支持动态代理的类
        :return:
        """
        javaClass = self.__readClassDesc__()
        # TODO: add classAnnotation to class structs
        self.readClassAnnotations()
        superjavaClass = self.readSuperClassDesc()
        javaClass.superJavaClass = superjavaClass
        return javaClass

    def __readClassDesc__(self):
        tc = self.bin.readByte()
        if tc != Constants.TC_CLASSDESC:
            print("InternalError")
            return
        # read Class name from bin
        className = self.bin.readString()
        suid = self.bin.readUnsignedLong()
        flags = self.bin.readByte()
        flags = int.from_bytes(flags, 'big')
        numFields = self.bin.readUnsignedShort()
        externalizable = flags & Constants.SC_EXTERNALIZABLE != 0
        sflag = flags & Constants.SC_SERIALIZABLE != 0
        hasWriteObjectData = flags & Constants.SC_WRITE_METHOD != 0
        hasBlockExternalData = flags & Constants.SC_BLOCK_DATA != 0
        if externalizable and sflag:
            print("serializable and externalizable flags conflict")

        print(f"className {className}")
        print(f"suid {suid}")
        print(f"number of fields {numFields}")
        classDesc = JavaClass(className, suid, flags)
        classDesc.hasWriteObjectData = hasWriteObjectData
        classDesc.hasBlockExternalData = hasBlockExternalData
        self.newHandles(classDesc)
        fields = []
        for i in range(numFields):
            tcode = self.bin.readByte()
            fname = self.bin.readString()
            if tcode == b'L' or tcode == b'[':
                signature = self.readTypeString()
            else:
                signature = tcode.decode()
            fields.append({'name': fname, 'sinnature': signature})
            print(f"name {fname} sinnature {signature}")
        if self.fieldStack and fields:
            lastFieldStack = self.fieldStack.pop()
            lastFieldStack.append(fields)
            self.fieldStack.append(lastFieldStack)
            classDesc.fields = fields
        return classDesc

    def readClassAnnotations(self):
        """
        读取类的附加信息
        """
        tc = self.bin.peekByte()
        print(f"ClassAnnotations start ")
        while tc != Constants.TC_ENDBLOCKDATA:
            self.readContent()
        self.bin.readByte()
        print(f"ClassAnnotations end ")

    def readSuperClassDesc(self):
        """
        读取父类的的class信息，一直到父类为空，类似于链表。java不支持多继承
        :return:
        """
        tc = self.bin.peekByte()
        print(f"Super Class start")
        if tc != Constants.TC_NULL:
            superJavaClass = self.readContent()
        else:
            self.bin.readByte()
            superJavaClass = None
        print(f"Super Class End")
        return superJavaClass

    def readObject(self):
        tc = self.bin.readByte()
        self.fieldStack.append([])
        if tc != Constants.TC_OBJECT:
            print("InternalError")
            return
        tc = self.bin.peekByte()
        if tc == Constants.TC_CLASSDESC:
            javaClass = self.__readClassDesc__()
            # TODO: add classAnnotation to class structs
            self.readClassAnnotations()
            superjavaClass = self.readSuperClassDesc()
            javaClass.superJavaClass = superjavaClass
            javaObject = JavaObject(javaClass)
            self.newHandles(javaObject)
            self.readClassData(javaObject)
        elif tc == Constants.TC_NULL:
            return self.readNull()
        elif tc == Constants.TC_REFERENCE:
            javaClass = self.readHandle()
            javaObject = JavaObject(javaClass)
            self.newHandles(javaObject)
            self.fieldStack.append([javaClass.fields])
            self.readClassData(javaObject)
        elif tc == Constants.TC_PROXYCLASSDESC:
            pass
        else:
            printInvalidTypeCode(tc)

        return javaObject

    def readClassData(self, javaObject):
        """
        读取对象的值，先读取父类的值，再读取子类的值
        :return:
        """
        fieldStack = self.fieldStack.pop()
        while len(fieldStack):
            fields = fieldStack.pop()
            currentField = []
            for field in fields:
                singature = field['sinnature']
                if singature.startswith('L') or singature.startswith('['):
                    value = self.readContent()
                elif singature == 'I':
                    value = self.bin.readInt()
                elif singature == 'F':
                    value = self.bin.readFloat()
                elif singature == "Z":
                    value = self.bin.readBoolean()
                print(f"name {field['name']}  value {value}")
                currentField.append({field['name']: [value, field['sinnature']]})
            javaObject.fields.put(currentField)

        if javaObject.javaClass.hasWriteObjectData:
            self.readObjectAnnotations(javaObject)

    def readHandle(self):
        """
        反序列化中是不会出现两个一摸一样的值，第二个值一般都是引用
        :return:
        """
        tc = self.bin.readByte()
        handle = self.bin.readInt()
        print(hex(handle))
        handle = handle - Constants.baseWireHandle
        obj = self.handles[handle]
        if isinstance(obj, JavaClass):
            return obj
        elif isinstance(obj, JavaString):
            return obj.string
        elif isinstance(obj, JavaObject):
            return obj
        else:
            print("unsuppprt type")
            return None

    def readTypeString(self):
        tc = self.bin.peekByte()
        if tc == Constants.TC_NULL:
            pass
        elif tc == Constants.TC_REFERENCE:
            return self.readHandle()
        elif tc == Constants.TC_STRING:
            return self.readString()
        elif tc == Constants.TC_LONGSTRING:
            return self.readString()
        else:
            printInvalidTypeCode(tc)

    def readString(self):
        tc = self.bin.readByte()
        string = self.bin.readString()
        self.newHandles(JavaString(string))
        return string

    def readContent(self):
        tc = self.bin.peekByte()
        if tc == Constants.TC_NULL:
            return self.readNull()
        elif tc == Constants.TC_REFERENCE:
            return self.readHandle()
        elif tc == Constants.TC_CLASS:
            self.bin.readByte()
            return self.readClassDescriptor()
        elif tc == Constants.TC_CLASSDESC or tc == Constants.TC_PROXYCLASSDESC:
            return self.readClassDescriptor()
        elif tc == Constants.TC_STRING or tc == Constants.TC_LONGSTRING:
            return self.readTypeString()
        elif tc == Constants.TC_ENUM:
            pass
        elif tc == Constants.TC_OBJECT:
            return self.readObject()
        elif tc == Constants.TC_EXCEPTION:
            pass
        elif tc == Constants.TC_ARRAY:
            return self.readArray()
        elif tc == Constants.TC_BLOCKDATA:
            return self.readBlockData()
        elif tc == Constants.TC_BLOCKDATALONG:
            pass
        elif tc == Constants.TC_ENDBLOCKDATA:
            print("end")
            self.bin.readByte()
            return 'end'
        else:
            printInvalidTypeCode(tc)
            exit(-1)

    def readBlockData(self):
        tc = self.bin.readByte()
        length = int.from_bytes(self.bin.readByte(), 'big')
        data = self.bin.readBytes(length)
        print(data)
        return data

    def readObjectAnnotations(self, javaObject):
        print("reading readObjectAnnotations")
        while True:
            obj = self.readContent()
            print(obj)
            if obj == 'end':
                break
            else:
                javaObject.objectAnnotation.append(obj)

    def readNull(self):
        tc = self.bin.readByte()
        return 'null'

    def readArray(self):
        self.bin.readByte()
        tc = self.bin.peekByte()
        if tc == Constants.TC_CLASSDESC:
            javaClass = self.readClassDescriptor()
        elif tc == Constants.TC_REFERENCE:
            javaClass = self.readHandle()
        else:
            print("unsupport type")
        size = self.bin.readInt()
        print(javaClass)
        print(f"array size {size}")
        array = []
        print(hex(self.newHandles(array)))
        for i in range(size):
            obj = self.readContent()
            if obj != 'end':
                array.append(obj)
            else:
                break
        return array


def printInvalidTypeCode(code: bytes):
    print(f"invalid type code {int.from_bytes(code, 'big'):#2x}")


class JavaClass:
    def __init__(self, name, suid, flags):
        self.name = name
        self.suid = suid
        self.flags = flags
        self.superJavaClass = None
        self.fields = []

    def __str__(self):
        return f"{self.name}"


class JavaString:
    def __init__(self, string):
        self.string = string

    def __str__(self):
        return self.string


class JavaObject:
    def __init__(self, javaClass):
        # TODO 必须定义父类的字段如何存储，如果都缠在一起，在写入的时候就会没有先后顺序之分
        self.javaClass = javaClass
        # fields 保存类的字段，队列数据结构。父类在最前，子类在最后
        self.fields = Queue()
        self.objectAnnotation = []

    def __str__(self):
        return f"className {self.javaClass.name}\t extend {self.javaClass.superJavaClass}"


def javaClass2Yaml(javaClass):
    d = {'suid': javaClass.suid, 'flags': javaClass.flags,
         'classAnnotation': None}
    # TODO classAnnotation
    if javaClass.superJavaClass:
        d['superClass'] = javaClass2Yaml(javaClass.superJavaClass)
    else:
        d['superClass'] = None
    return {javaClass.name: d}


def javaObject2Yaml(javaObject):
    d = {'suid': javaObject.javaClass.suid, 'flags': javaObject.javaClass.flags,
         'classAnnotation': None}
    # TODO classAnnotation
    if javaObject.javaClass.superJavaClass:
        d['superClass'] = javaClass2Yaml(javaObject.javaClass.superJavaClass)
    else:
        d['superClass'] = None
    fields = []
    while javaObject.fields.qsize():
        currentObjFields = javaObject.fields.get()
        for currentObjField in currentObjFields:
            for k, v in currentObjField.items():
                data = {'type': v[1], 'fieldName': k}
                if isinstance(v[0], JavaObject):
                    data['value'] = javaObject2Yaml(v[0])
                elif isinstance(v[0], list):
                    valueList = []
                    for o in v[0]:
                        if isinstance(o, JavaObject):
                            valueList.append(javaObject2Yaml(o))
                    data['value'] = valueList
                else:
                    data['value'] = v[0]
                fields.append({'data': data})

    d['Fields'] = fields
    objectAnnotation = []
    for o in javaObject.objectAnnotation:
        if isinstance(o, JavaObject):
            o = javaObject2Yaml(o)
        objectAnnotation.append(o)
    if objectAnnotation:
        d['objectAnnotation'] = objectAnnotation
    else:
        d['objectAnnotation'] = None
    return {javaObject.javaClass.name: d}


class MyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, bytes):
            return obj.hex()
        return json.JSONEncoder.default(self, obj)


if __name__ == '__main__':
    f = open("payload.ser", "rb")
    s = ObjectIO(f)
    obj = ObjectStream(s).readContent()
    print(obj)
    d = javaObject2Yaml(obj)
    print("------------------------------------")
    print(d)
    print("------------------------------------")
    print(json.dumps(d, indent=4, cls=MyEncoder, ensure_ascii=False))
