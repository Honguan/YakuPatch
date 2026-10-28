using System;
using System.Collections.Generic;

namespace UnityEngine
{
    public class Object { }
    public class MonoBehaviour : Object { }
    public class SerializeReference : Attribute { }
}

namespace Fixture
{
    public interface IValue { }
    [Serializable] public class Holder<T> : UnityEngine.MonoBehaviour
    {
        [Serializable] public class Entry { public T content; }
        public Entry[] entries;
        [UnityEngine.SerializeReference] public List<IValue> referencesList;
    }
    public class Numbers : Holder<int> { }
    public class Words : Holder<string> { }
    [Serializable] public class Values
    {
        [UnityEngine.SerializeReference] public List<IValue> items;
    }
    public class Nested : UnityEngine.MonoBehaviour { public Values values; }
    public class ObjectReference : UnityEngine.MonoBehaviour
    {
        [UnityEngine.SerializeReference] public UnityEngine.Object target;
    }
}
