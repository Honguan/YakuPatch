// Fixture for tests/test_unity.py: compiled with
//   csc /target:library /optimize+ /out:Lines.dll Lines.cs
public static class Lines
{
    public static string Greet() { return "よ、よろしくお願いします…"; }
    public static string Pick(int n)
    {
        switch (n)
        {
            case 0: return "初めてなのに、こんなの嫌ぁ…っ";
            case 1: return "ここが検査室…";
            case 2: return "Resources/立ち絵/girl";
            default: return "ok";
        }
    }
    public static string Join()
    {
        string s = "";
        for (long i = 0; i < 2; i++) s += "教頭に怒られてしまった…";
        return s + 1.5.ToString() + "おまんこ";
    }
}
