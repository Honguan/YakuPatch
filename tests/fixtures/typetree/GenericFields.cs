using System;
using System.Collections.Generic;

namespace UnityEngine
{
    public class Object { }
    public class MonoBehaviour : Object { }
    public class SerializeReference : Attribute { }
    [Serializable] public struct LazyLoadReference<T> where T : Object { }
    [Serializable] public struct Color { public float r, g, b, a; }
}

namespace Fixture
{
    public interface IValue { }
    [Serializable] public abstract class AbstractValue { public int number; }
    [Serializable] public class ConcreteValue : AbstractValue { public string text; }
    public abstract class AbstractAsset : UnityEngine.Object { }
    public class AbstractFields : UnityEngine.MonoBehaviour
    {
        public AbstractValue inline;
        public List<AbstractValue> list;
        public AbstractValue[] array;
        [UnityEngine.SerializeReference] public AbstractValue reference;
        [UnityEngine.SerializeReference] public List<AbstractValue> references;
        public ConcreteValue concrete;
        public AbstractAsset asset;
    }
    [Serializable] public class Holder<T> : UnityEngine.MonoBehaviour
    {
        [Serializable] public class Entry { public T content; }
        public Entry[] entries;
        [UnityEngine.SerializeReference] public List<IValue> referencesList;
    }
    public class Numbers : Holder<int> { }
    public class Words : Holder<string> { }
    [Serializable] public class Box<T> { public T value; public T second; }
    [Serializable] public class Inner : Box<int> { }
    public class Rebound : Box<Inner> { }
    public class DirectBox : UnityEngine.MonoBehaviour { public Box<Box<int>> nested; }
    public class LazyAsset : UnityEngine.MonoBehaviour { public UnityEngine.LazyLoadReference<UnityEngine.Object> asset; }
    public class ReferencedValue : UnityEngine.MonoBehaviour
    {
        [UnityEngine.SerializeReference] public UnityEngine.Color color;
    }
    [Serializable] public class SelfReference
    {
        [UnityEngine.SerializeReference] public SelfReference parent;
        public SelfReference inline;
        public int value;
    }
    public class SelfHost : UnityEngine.MonoBehaviour { public SelfReference item; }
    public class BoxedEnum : UnityEngine.MonoBehaviour { [UnityEngine.SerializeReference] public Enum value; }
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
